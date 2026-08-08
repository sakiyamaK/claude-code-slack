"""composition root。各サービスを組み立て、メッセージの並行 dispatch を担う。

責務はこの2つだけ:
- 依存の組み立て（すべてキーワード引数で差し替え可能＝テスト時は偽物を注入）
- スレッド単位の直列化（同一スレッドの指示を順番に処理）とエラーの Slack 報告
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from . import match as match_mod
from .commands import CommandService
from .config import Config
from .cmux import CmuxClient, CmuxUnavailable
from .links import ThreadLinks
from .llm import LlmClient
from .registry import Database, TaskStore, SettingsStore
from .router import MessageRouter
from .settings import RuntimeSettings
from .tasks import TaskService, Poster
from .watcher import SessionWatcher

_MAX_WORKERS = 4


class Orchestrator:
    def __init__(self, cfg: Config, poster: Poster, *,
                 db: Database | None = None,
                 cmux: CmuxClient | None = None,
                 llm: LlmClient | None = None) -> None:
        self.cfg = cfg
        self.post = poster

        # 永続化
        db = db or Database(cfg.registry_db)
        self.tasks_store = TaskStore(db)
        self.settings_store = SettingsStore(db)
        self.links = ThreadLinks(self.settings_store)
        self.runtime = RuntimeSettings(
            self.settings_store, cfg.default_model, cfg.task_permission_mode)

        # 外部プロセス
        self.cmux = cmux or CmuxClient(cfg.cmux_bin)
        self.llm = llm or LlmClient(cfg.claude_bin, cfg.target_repo)

        # サービス
        self.task_service = TaskService(
            cfg, self.cmux, self.tasks_store, self.links, self.runtime, poster,
            pick_session=partial(match_mod.pick_session, self.llm.ask_json, cfg.match_model),
            pick_repo=lambda body: match_mod.pick_repo(
                self.llm.ask_json, cfg.match_model, body,
                cfg.repos, cfg.repo_descriptions, cfg.default_repo),
            find_ticket=match_mod.find_ticket,
        )
        self.command_service = CommandService(
            cfg, self.cmux, self.task_service, self.tasks_store,
            self.links, self.runtime, poster)
        self.router = MessageRouter(
            self.command_service, self.task_service, self.links, poster)
        self.watcher = SessionWatcher(
            self.cmux, self.tasks_store, self.links, poster=poster)

        # dispatch（スレッド単位の直列化）
        self.pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ── app.py が使う入口 ───────────────────────────────
    def handle(self, channel: str, user: str, thread_ts: str, text: str) -> None:
        """1メッセージを処理（app.py のゲート通過後に呼ばれる）。ブロックせず投げる。"""
        self.pool.submit(self._handle_locked, channel, user, thread_ts, text)

    def start_watcher(self, interval: int = 20) -> None:
        self.watcher.start(interval)

    @property
    def model(self) -> str:
        return self.runtime.model

    @property
    def permission_mode(self) -> str:
        return self.runtime.permission_mode

    # ── 内部 ────────────────────────────────────────────
    def _lock(self, thread_ts: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(thread_ts, threading.Lock())

    def _handle_locked(self, channel: str, user: str, thread_ts: str, text: str) -> None:
        with self._lock(thread_ts):
            print(f"[relay] handle ch={channel} thread={thread_ts} text={text!r}", flush=True)
            try:
                self.router.route(channel, user, thread_ts, text)
            except CmuxUnavailable as e:
                self.post(channel, thread_ts,
                          f"⚠️ {e}。cmux が使えるようになってから再度指示してください。")
            except Exception as e:  # noqa: BLE001
                self.post(channel, thread_ts, f"❌ エラー: {e}")
