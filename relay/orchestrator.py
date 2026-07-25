"""司令塔（SPEC 全体）。ルーティング・普通/タスクモード・コマンド・通知監視を束ねる。"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import dashboard as dash
from . import intent as intent_mod
from . import todo as todo_mod
from .cmux import CmuxClient, CmuxUnavailable
from .config import Config
from .parsing import (
    parse_task_mode, parse_command, parse_version_spec,
)
from .registry import (
    Registry, Task, MODE_NORMAL, MODE_TASK,
    STATUS_ACTIVE, STATUS_DONE, now_iso,
)
from .runner import ClaudeRunner

# Slack への投稿: (channel, thread_ts, text) -> None
Poster = Callable[[str, str | None, str], None]

MODEL_CHOICES = [("opus", "Opus 4.8"), ("sonnet", "Sonnet 5"), ("haiku", "Haiku 4.5")]
MODE_CHOICES = [
    ("default", "default(都度確認)"),
    ("acceptEdits", "auto(編集自動許可)"),
    ("plan", "plan(計画のみ)"),
]
BYPASS_CHOICE = ("bypassPermissions", "yolo(全許可)")

_VER_RE = re.compile(r"increment version\s+(\d+)\.(\d+)\.(\d+)")


class Orchestrator:
    def __init__(self, cfg: Config, poster: Poster) -> None:
        self.cfg = cfg
        self.post = poster
        self.registry = Registry(cfg.registry_db)
        self.runner = ClaudeRunner(cfg.claude_bin)
        self.cmux = CmuxClient(cfg.cmux_bin)
        self.pool = ThreadPoolExecutor(max_workers=cfg.max_concurrent)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._pending_pick: dict[str, str] = {}  # thread_ts -> "model"|"mode"
        self._dash_state: dash.DashboardState | None = None
        # グローバル設定の初期化
        if self.registry.get_setting("model") is None:
            self.registry.set_setting("model", cfg.default_model)
        if self.registry.get_setting("permission_mode") is None:
            self.registry.set_setting("permission_mode", cfg.default_permission_mode)

    # ── 設定 ───────────────────────────────────────────
    @property
    def model(self) -> str:
        return self.registry.get_setting("model", self.cfg.default_model)

    @property
    def permission_mode(self) -> str:
        return self.registry.get_setting("permission_mode", self.cfg.default_permission_mode)

    def _lock(self, thread_ts: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(thread_ts, threading.Lock())

    # ── 入口 ───────────────────────────────────────────
    def handle(self, channel: str, user: str, thread_ts: str, text: str) -> None:
        """1メッセージを処理（app.py のゲート通過後に呼ばれる）。ブロックせず投げる。"""
        self.pool.submit(self._handle_locked, channel, user, thread_ts, text)

    def _handle_locked(self, channel: str, user: str, thread_ts: str, text: str) -> None:
        with self._lock(thread_ts):
            print(f"[relay] handle ch={channel} thread={thread_ts} text={text!r}", flush=True)
            try:
                self._route(channel, user, thread_ts, text)
            except CmuxUnavailable as e:
                self.post(channel, thread_ts,
                          f"⚠️ {e}。普通モードのみ受け付けます。")
            except Exception as e:  # noqa: BLE001
                self.post(channel, thread_ts, f"❌ エラー: {e}")

    def _route(self, channel: str, user: str, thread_ts: str, text: str) -> None:
        # 1) ピッカー待ち（/model /mode の番号返信）
        if thread_ts in self._pending_pick and text.strip().isdigit():
            self._apply_pick(channel, thread_ts, int(text.strip()))
            return
        # 2) コマンド
        cmd = parse_command(text)
        if cmd.is_command:
            self._command(channel, thread_ts, cmd.name, cmd.arg)
            return
        # 3) タスクモード
        tp = parse_task_mode(text, self.cfg.task_keywords)
        if tp.is_task:
            if tp.empty_body:
                self.post(channel, thread_ts, "何を作業しますか？内容を教えてください。")
                return
            self._task_mode(channel, user, thread_ts, tp.body)
            return
        # 4) 自然言語 → 操作 or 普通モード
        active = [t.branch for t in self.registry.active_tasks() if t.branch]
        it = intent_mod.classify(text, active)
        if it.is_operation:
            self._operation(channel, thread_ts, it, text)
        else:
            self._normal_mode(channel, thread_ts, text)

    # ── 普通モード ─────────────────────────────────────
    def _normal_mode(self, channel: str, thread_ts: str, text: str) -> None:
        task = self.registry.get(thread_ts)
        session = task.session_id if task else None
        if task is None:
            self.registry.create(Task(
                thread_ts=thread_ts, channel_id=channel, user_id="", mode=MODE_NORMAL,
                branch=None, session_id=None, anchor_ts=None, status=STATUS_ACTIVE,
                created_at=now_iso(), updated_at=now_iso(),
            ))
        res = self.runner.run(
            text, self.cfg.workspace_dir,
            model=self.model, permission_mode=self.permission_mode,
            resume_session=session,
        )
        if res.session_id:
            self.registry.update(thread_ts, session_id=res.session_id)
        self.post(channel, thread_ts, res.text[:3500])

    # ── タスクモード ───────────────────────────────────
    def _ensure_manager(self) -> str:
        stored = self.registry.get_setting("manager_surface")
        surface = self.cmux.ensure_manager(
            self.cfg.target_repo, stored, self.model, self.cfg.manager_permission_mode)
        if surface != stored:
            self.registry.set_setting("manager_surface", surface)
        return surface

    def _task_mode(self, channel: str, user: str, thread_ts: str, body: str) -> None:
        print(f"[relay] task_mode: ensure cmux manager…", flush=True)
        surface = self._ensure_manager()
        print(f"[relay] manager surface = {surface}", flush=True)
        cont = todo_mod.find_entry_by_thread(self.cfg.todo_path, thread_ts)
        _, branch = todo_mod.append_task(self.cfg.todo_path, body, thread_ts)

        existing = self.registry.get(thread_ts)
        if existing is None:
            self.registry.create(Task(
                thread_ts=thread_ts, channel_id=channel, user_id=user, mode=MODE_TASK,
                branch=branch, session_id=None, anchor_ts=None, status=STATUS_ACTIVE,
                created_at=now_iso(), updated_at=now_iso(),
            ))
        elif branch:
            self.registry.update(thread_ts, branch=branch)

        verb = "継続指示を追記" if cont else "新規タスクを追記"
        # /tcmtasks -t で todo.md を読み込ませてマネージャーとして処理させる
        self.cmux.send(surface, "/tcmtasks -t")
        self.post(channel, thread_ts,
                  f"🚀 cmux のマネージャーに{verb}しました。進捗はこのスレッドに通知します。")

    def _operation(self, channel: str, thread_ts: str, it: intent_mod.Intent, text: str) -> None:
        try:
            surface = self._ensure_manager()
        except CmuxUnavailable as e:
            self.post(channel, thread_ts, f"⚠️ {e}")
            return
        target = it.task or "（対象未指定）"
        self.cmux.send(surface, f"社長からの指示: {text}")
        self.post(channel, thread_ts, f"🛑 マネージャーに「{target} を止める」指示を送りました。")

    # ── コマンド ───────────────────────────────────────
    def _command(self, channel: str, thread_ts: str, name: str, arg: str) -> None:
        if name in ("model", "mode"):
            self._show_picker(channel, thread_ts, name)
        elif name in ("commit", "push"):
            self._git_command(channel, thread_ts, name)
        elif name in self.cfg.commands:
            self._custom_command(channel, thread_ts, name, arg)
        else:
            self.post(channel, thread_ts,
                      f"未知のコマンド: /{name}。config.yml の commands で定義できます。")

    def _custom_command(self, channel: str, thread_ts: str, name: str, arg: str) -> None:
        """config.yml で定義したプロジェクト固有コマンドを実行（マネージャーへ指示）。"""
        spec = self.cfg.commands[name] or {}
        branch = self._thread_branch(thread_ts)
        if not branch and "{branch}" in spec.get("prompt", ""):
            self.post(channel, thread_ts, "このスレッドはタスクに紐付いていません。")
            return
        ctx = {"branch": branch or "", "arg": arg}
        # バージョン記法を使うコマンド（配信等）
        if spec.get("version"):
            cur = self.current_version()
            try:
                vr = parse_version_spec(arg or None, cur)
            except ValueError as e:
                self.post(channel, thread_ts, f"❌ {e}")
                return
            ctx["version"] = ".".join(map(str, vr.version))
            if vr.note:
                self.post(channel, thread_ts, f"（{vr.note}）")
        # 確認メッセージ
        confirm = spec.get("confirm")
        if confirm:
            self.post(channel, thread_ts, self._fmt(confirm, ctx))
        # マネージャーへ指示投入
        surface = self._ensure_manager()
        self.cmux.send(surface, self._fmt(spec.get("prompt", ""), ctx))

    @staticmethod
    def _fmt(template: str, ctx: dict) -> str:
        class _D(dict):
            def __missing__(self, k):  # 未定義プレースホルダはそのまま残す
                return "{" + k + "}"
        return template.format_map(_D(ctx))

    def _show_picker(self, channel: str, thread_ts: str, kind: str) -> None:
        if kind == "model":
            cur = self.model
            opts = MODEL_CHOICES
            label = "モデル"
        else:
            cur = self.permission_mode
            opts = list(MODE_CHOICES)
            if self.cfg.allow_bypass:
                opts.append(BYPASS_CHOICE)
            label = "動作モード"
        lines = [f"現在: {cur}。{label}を選んでください（番号を返信）:"]
        for i, (_, disp) in enumerate(opts, 1):
            lines.append(f" {i} {disp}")
        self._pending_pick[thread_ts] = kind
        self.post(channel, thread_ts, "\n".join(lines))

    def _apply_pick(self, channel: str, thread_ts: str, num: int) -> None:
        kind = self._pending_pick.pop(thread_ts)
        opts = MODEL_CHOICES if kind == "model" else (
            list(MODE_CHOICES) + ([BYPASS_CHOICE] if self.cfg.allow_bypass else []))
        if not (1 <= num <= len(opts)):
            self.post(channel, thread_ts, "範囲外の番号です。もう一度コマンドを打ってください。")
            return
        value, disp = opts[num - 1]
        key = "model" if kind == "model" else "permission_mode"
        self.registry.set_setting(key, value)
        self.post(channel, thread_ts, f"✅ {('モデル' if kind=='model' else '動作モード')}を「{disp}」にしました（全スレッド共通）。")

    def _thread_branch(self, thread_ts: str) -> str | None:
        t = self.registry.get(thread_ts)
        return t.branch if t and t.mode == MODE_TASK else None

    def _git_command(self, channel: str, thread_ts: str, name: str) -> None:
        branch = self._thread_branch(thread_ts)
        if not branch:
            self.post(channel, thread_ts, "このスレッドはタスクに紐付いていません。どのタスクですか？")
            return
        surface = self._ensure_manager()
        if name == "commit":
            self.cmux.send(surface, f"社長承認: {branch} をコミットしてください。")
            self.post(channel, thread_ts, f"📝 {branch} のコミットをマネージャーに指示しました。")
        else:
            self.cmux.send(surface, f"社長指示: {branch} を origin に push してください。")
            self.post(channel, thread_ts, f"⬆️ {branch} の push をマネージャーに指示しました。")

    def current_version(self) -> tuple[int, int, int]:
        try:
            out = subprocess.run(
                ["git", "-C", self.cfg.target_repo, "log", "-50", "--pretty=%s"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in out.splitlines():
                m = _VER_RE.search(line)
                if m:
                    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass
        return (0, 0, 0)

    # ── dashboard 監視（通知） ─────────────────────────
    def start_watcher(self, interval: int = 15) -> None:
        threading.Thread(target=self._watch_loop, args=(interval,), daemon=True).start()

    def _watch_loop(self, interval: int) -> None:
        while True:
            try:
                self._poll_dashboard()
            except Exception:
                pass
            time.sleep(interval)

    def _poll_dashboard(self) -> None:
        import os
        if not os.path.exists(self.cfg.dashboard_path):
            return
        with open(self.cfg.dashboard_path, "r", encoding="utf-8") as f:
            cur = dash.parse_dashboard(f.read())
        events = dash.diff_events(self._dash_state, cur)
        self._dash_state = cur
        for e in events:
            self._notify_event(e)

    def _notify_event(self, e: dash.Event) -> None:
        icon = {"done": "✅", "review": "🔨", "stall": "⚠️", "alert": "🚨", "report": "📋"}.get(e.kind, "ℹ️")
        if e.kind == "review":
            body = f"{icon} {e.task} 実装・テスト完了、レビューへ"
        elif e.kind == "done":
            body = f"{icon} {e.task} 完了"
        elif e.kind == "stall":
            body = f"{icon} {e.task} が停滞しています（{e.detail}）"
        elif e.kind == "report":
            body = f"{icon} {e.task}\n\n{e.detail[:3000]}"
        else:
            body = f"{icon} 要判断: {e.detail}"
        # thread_ts からチャンネルを解決して該当スレッドへ
        if e.thread_ts:
            t = self.registry.get(e.thread_ts)
            if t:
                if e.kind == "done":
                    self.registry.update(e.thread_ts, status=STATUS_DONE)
                self.post(t.channel_id, e.thread_ts, body)
                return
        # 未紐付け（PC発タスク等）: 既定チャンネルがあれば流す。無ければ skip
        # （SPEC §15: 普通モードで「何が動いてる？」と聞けば見えるので取りこぼしにはならない）
