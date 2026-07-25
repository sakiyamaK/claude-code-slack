"""司令塔。ルーティング・普通/タスクモード・コマンド・進捗監視(AI解釈)を束ねる。"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import intent as intent_mod
from . import interpret as interp_mod
from .cmux import CmuxClient, CmuxUnavailable
from .config import Config
from .parsing import (
    parse_task_mode, parse_command, parse_version_spec,
)
from .registry import (
    Registry, Task, MODE_NORMAL, MODE_TASK,
    STATUS_ACTIVE, STATUS_DONE, now_iso,
)
from .runner import ClaudeRunner, NORMAL_SYSTEM_HINT

# Slack への投稿: (channel, thread_ts, text) -> None
Poster = Callable[[str, str | None, str], None]

MODEL_CHOICES = [("opus", "Opus 4.8"), ("sonnet", "Sonnet 5"), ("haiku", "Haiku 4.5")]
MODE_CHOICES = [
    ("default", "default(都度確認)"),
    ("acceptEdits", "auto(編集自動許可)"),
    ("plan", "plan(計画のみ)"),
]
BYPASS_CHOICE = ("bypassPermissions", "yolo(全許可)")

_VER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


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
        self._watch_hash: str | None = None                # 進捗ファイル変化検知
        self._manager_hash: str | None = None              # マネージャー端末変化検知
        self._notified: dict[str, list[str]] = {}          # thread -> 通知済み要約
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
        # 3) タスクモード開始（作業:）
        tp = parse_task_mode(text, self.cfg.task_keywords)
        if tp.is_task:
            if tp.empty_body:
                self.post(channel, thread_ts, "何を作業しますか？内容を教えてください。")
                return
            self._task_mode(channel, user, thread_ts, tp.body)
            return
        # 3.5) 既にタスクスレッドなら、以降のやりとりもタスクモードで継続
        existing = self.registry.get(thread_ts)
        active = [t.branch for t in self.registry.active_tasks() if t.branch]
        if existing and existing.mode == MODE_TASK:
            it = intent_mod.classify(text, active)
            if it.is_operation:
                self._operation(channel, thread_ts, it, text)
            else:
                self._task_follow_up(channel, user, thread_ts, text)
            return
        # 4) 自然言語 → 操作 or 普通モード（タスク未紐付けのスレッド）
        it = intent_mod.classify(text, active)
        if it.is_operation:
            self._operation(channel, thread_ts, it, text)
        else:
            self._normal_mode(channel, thread_ts, text)

    # ── 普通モード ─────────────────────────────────────
    def _progress_files(self) -> list[str]:
        """進捗の真実の在り処（config 由来）を絶対パスで集める。

        - backend の watch ファイル（dashboard 等）
        - backend の start ステップで書き込む file（todo 等）
        推測ではなくこれらを読ませることで、status 質問の食い違いを防ぐ。
        """
        paths: list[str] = []
        if self.cfg.watch_path:
            paths.append(self.cfg.watch_path)
        for step in (self.cfg.backend.get("start") or []):
            if (step or {}).get("kind") == "file" and step.get("path"):
                paths.append(os.path.join(self.cfg.target_repo, step["path"]))
        # 重複除去（順序維持）
        seen: set[str] = set()
        return [p for p in paths if not (p in seen or seen.add(p))]

    def _normal_hint(self) -> str:
        files = self._progress_files()
        if not files:
            return NORMAL_SYSTEM_HINT
        lst = "\n".join(f"  - {p}" for p in files)
        return (
            "あなたはSlack経由で話しかけられている。回答は簡潔に。\n"
            "タスクの進捗・状況を聞かれたら、必ず次の進捗ファイルを実際に read してから答えること:\n"
            f"{lst}\n"
            "ファイルを読まずに『タスクはない』等と憶測で答えてはならない。"
            "読めなかった・存在しなかった場合はその事実（どのパスが無かったか）をそのまま伝えること。"
        )

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
            add_dirs=[self.cfg.worktree_parent],
            append_system=self._normal_hint(),
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
        if self.cfg.is_solo:
            self._solo_task(channel, user, thread_ts, body)
        else:
            self._backend_task(channel, user, thread_ts, body)

    _SOLO_HINT = (
        "あなたは隔離された作業ディレクトリ（detached worktree）にいる。"
        "コードを変更するなら、まず適切な名前のブランチを作って作業すること。回答は簡潔に。"
    )

    def _solo_cwd(self, thread_ts: str) -> str:
        """solo: タスクごとに worktree を切って隔離する（並行しても衝突しない）。"""
        if not self.cfg.solo_worktree:
            return self.cfg.target_repo
        name = "task-" + re.sub(r"[^0-9A-Za-z]", "-", thread_ts)
        path = os.path.join(self.cfg.worktree_parent, name)
        if not os.path.isdir(path):
            base = self.cfg.solo_base or "HEAD"
            r = subprocess.run(
                ["git", "-C", self.cfg.target_repo, "worktree", "add", "--detach", path, base],
                capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"worktree 作成失敗: {r.stderr.strip()}")
        return path

    def _solo_task(self, channel: str, user: str, thread_ts: str, body: str) -> None:
        """依存ゼロの簡易実行: タスクごとに worktree を切り claude -p を回して結果を返す。"""
        print(f"[relay] solo task", flush=True)
        task = self.registry.get(thread_ts)
        session = task.session_id if task else None
        if task is None:
            self.registry.create(Task(
                thread_ts=thread_ts, channel_id=channel, user_id=user, mode=MODE_TASK,
                branch=None, session_id=None, anchor_ts=None, status=STATUS_ACTIVE,
                created_at=now_iso(), updated_at=now_iso()))
        cwd = self._solo_cwd(thread_ts) if session is None else self.cfg.target_repo
        self.post(channel, thread_ts, "🛠 タスクを実行します…")
        res = self.runner.run(
            body, cwd, model=self.model,
            permission_mode=self.cfg.manager_permission_mode,
            resume_session=session, append_system=self._SOLO_HINT,
            add_dirs=[self.cfg.worktree_parent])
        if res.session_id:
            self.registry.update(thread_ts, session_id=res.session_id)
        head = "✅ 完了" if res.ok else "⚠️ 失敗"
        self.post(channel, thread_ts, f"{head}\n\n{res.text[:3500]}")

    def _task_follow_up(self, channel: str, user: str, thread_ts: str, text: str) -> None:
        """タスクスレッドの2通目以降。solo=同一セッション継続 / backend=マネージャーに注入して返答を返す。"""
        if self.cfg.is_solo:
            self._solo_task(channel, user, thread_ts, text)
            return
        # backend: マネージャーへ注入。返答（直接返答 or エージェント起動→dashboard）は
        # watch_manager / watch の AI 解釈で該当スレッドに戻る。
        ref = self._task_ref(thread_ts)
        ctx = f"（タスク「{ref}」について）" if ref else ""
        surface = self._ensure_manager()
        self.cmux.send(surface, f"{ctx}{text}")
        # watch_manager が無い構成でだけ、最低限の受領 ack を出す（二重投稿回避）
        if not self.cfg.watch_manager:
            self.post(channel, thread_ts, "📨 マネージャーに伝えました。")

    def _backend_task(self, channel: str, user: str, thread_ts: str, body: str) -> None:
        """プロジェクトのオーケストレーターに委譲: start フックを実行して起動。"""
        print(f"[relay] backend={self.cfg.task_backend} start", flush=True)
        existing = self.registry.get(thread_ts)
        if existing is None:
            self.registry.create(Task(
                thread_ts=thread_ts, channel_id=channel, user_id=user, mode=MODE_TASK,
                branch=None, session_id=None, anchor_ts=None, status=STATUS_ACTIVE,
                created_at=now_iso(), updated_at=now_iso()))
        # タスク内容を保持（進捗の AI 解釈でスレッドに紐付けるため）
        self.registry.set_setting(f"instr:{thread_ts}", body)
        self._run_start_steps(thread_ts, body)
        self.post(channel, thread_ts,
                  f"🚀 タスクを開始しました（{self.cfg.task_backend}）。進捗はこのスレッドに通知します。")

    def _run_start_steps(self, thread_ts: str, body: str) -> None:
        """backend の start フック（inject/file/command のリスト）を順に実行。"""
        ctx = {"task": body, "id": thread_ts}
        steps = self.cfg.backend.get("start") or []
        for step in steps:
            kind = (step or {}).get("kind")
            if kind == "inject":
                surface = self._ensure_manager()
                self.cmux.send(surface, self._fmt(step.get("prompt", "{task}"), ctx))
            elif kind == "file":
                path = os.path.join(self.cfg.target_repo, step["path"])
                os.makedirs(os.path.dirname(path), exist_ok=True)
                entry = self._fmt(step.get("template", "{task}\n"), ctx)
                with open(path, "a", encoding="utf-8") as f:
                    f.write("\n" + entry + "\n")
            elif kind == "command":
                subprocess.run(self._fmt(step.get("command", ""), ctx),
                               shell=True, cwd=self.cfg.target_repo,
                               capture_output=True, text=True)

    def _operation(self, channel: str, thread_ts: str, it: intent_mod.Intent, text: str) -> None:
        if self.cfg.is_solo:
            self.post(channel, thread_ts, "solo モードでは個別タスクの中断は未対応です。")
            return
        try:
            surface = self._ensure_manager()
        except CmuxUnavailable as e:
            self.post(channel, thread_ts, f"⚠️ {e}")
            return
        target = it.task or "（対象未指定）"
        self.cmux.send(surface, f"指示: {text}")
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
        """config.yml で定義したユーザー定義コマンドを実行（マネージャーへ指示）。"""
        spec = self.cfg.commands[name] or {}
        ref = self._task_ref(thread_ts)
        prompt_tpl = spec.get("prompt", "")
        if not ref and ("{task}" in prompt_tpl or "{branch}" in prompt_tpl):
            self.post(channel, thread_ts, "このスレッドはタスクに紐付いていません。")
            return
        ctx = {"task": ref or "", "branch": ref or "", "arg": arg}
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

    def _task_ref(self, thread_ts: str) -> str | None:
        """このスレッドのタスクを指す参照（依頼内容）。マネージャーがどのタスクか特定する手掛かり。"""
        t = self.registry.get(thread_ts)
        if not t or t.mode != MODE_TASK:
            return None
        instr = self.registry.get_setting(f"instr:{thread_ts}", "")
        return (instr or "").strip()[:120] or None

    def _git_command(self, channel: str, thread_ts: str, name: str) -> None:
        if self.cfg.is_solo:
            self.post(channel, thread_ts, "solo モードでは /commit /push は使いません（タスク内で直接お願いします）。")
            return
        ref = self._task_ref(thread_ts)
        if not ref:
            self.post(channel, thread_ts, "このスレッドはタスクに紐付いていません。どのタスクですか？")
            return
        surface = self._ensure_manager()
        if name == "commit":
            self.cmux.send(surface, f"承認します。「{ref}」のタスクの成果をコミットしてください。")
            self.post(channel, thread_ts, "📝 コミットをマネージャーに指示しました。")
        else:
            self.cmux.send(surface, f"「{ref}」のタスクのブランチを origin に push してください。")
            self.post(channel, thread_ts, "⬆️ push をマネージャーに指示しました。")

    def current_version(self) -> tuple[int, int, int]:
        """config の version_source（シェルコマンド）を実行し、出力から X.Y.Z を拾う。"""
        try:
            out = subprocess.run(
                self.cfg.version_source, shell=True, cwd=self.cfg.target_repo,
                capture_output=True, text=True, timeout=15,
            ).stdout
            m = _VER_RE.search(out)
            if m:
                return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass
        return (0, 0, 0)

    # ── 進捗監視（AI 解釈で通知） ───────────────────────
    def start_watcher(self, interval: int = 20) -> None:
        # solo は end=プロセス終了で結果を返すため監視不要。外部 backend のみ監視。
        if self.cfg.is_solo:
            return
        if self.cfg.watch_path:
            threading.Thread(target=self._loop, args=(self._poll_watch, interval),
                             daemon=True).start()
        # マネージャー端末の直接返答も同じ AI 解釈で拾う（dashboard に出ない分）
        if self.cfg.watch_manager:
            threading.Thread(target=self._loop, args=(self._poll_manager, interval),
                             daemon=True).start()

    def _loop(self, fn: Callable[[], None], interval: int) -> None:
        while True:
            try:
                fn()
            except Exception:
                pass
            time.sleep(interval)

    def _active_task_list(self) -> list[dict]:
        """追跡中の backend タスク（id=thread, task=依頼内容）。"""
        tasks = []
        for t in self.registry.active_tasks():
            if t.mode != MODE_TASK:
                continue
            instr = self.registry.get_setting(f"instr:{t.thread_ts}", "")
            tasks.append({"id": t.thread_ts, "task": instr or "(内容不明)"})
        return tasks

    def _interpret_and_notify(self, content: str) -> None:
        """任意テキスト（進捗ファイル or マネージャー端末）を解釈してイベント通知。"""
        tasks = self._active_task_list()
        if not tasks:
            return
        events = interp_mod.interpret(   # 頻繁に呼ぶので安価なモデル
            self.cfg.claude_bin, "haiku", self.cfg.target_repo,
            content, tasks, self._notified)
        for e in events:
            self._notify_ai_event(e)

    def _poll_watch(self) -> None:
        """進捗ファイル（dashboard 等）を解釈。"""
        path = self.cfg.watch_path
        if not path or not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if h == self._watch_hash:
            return  # 変化なし → LLM を呼ばない（デバウンス）
        self._watch_hash = h
        self._interpret_and_notify(content)

    def _poll_manager(self) -> None:
        """マネージャー端末の出力を解釈（エージェントを立てず直接返した返事を拾う）。"""
        surface = self.registry.get_setting("manager_surface")
        if not surface or not self.cmux.surface_exists(surface):
            return
        content = self.cmux.read_screen(surface, 200)
        if not content.strip():
            return
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if h == self._manager_hash:
            return  # 画面変化なし → デバウンス
        self._manager_hash = h
        self._interpret_and_notify(content)

    def _notify_ai_event(self, e: interp_mod.InterpEvent) -> None:
        # 重複排除（同一スレッドで同じ要約は送らない）
        sent = self._notified.setdefault(e.thread_ts, [])
        if e.summary in sent:
            return
        sent.append(e.summary)
        t = self.registry.get(e.thread_ts)
        if not t:
            return
        icon = {"done": "✅", "alert": "🚨", "progress": "🔨"}.get(e.kind, "ℹ️")
        if e.kind == "done":
            self.registry.update(e.thread_ts, status=STATUS_DONE)
        self.post(t.channel_id, e.thread_ts, f"{icon} {e.summary}")
