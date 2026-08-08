"""セッション監視。各セッションの画面を AI 解釈し、状態変化をスレッドへ通知する。

- 画面ハッシュの変化検知でデバウンス（変わらなければ LLM を呼ばない）
- 同一スレッドへの同じ要約の重複通知はしない
- タブが閉じられていたら追跡を終了する（続きの指示で再開できる旨を通知）
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Callable

from .cmux import CmuxClient
from .interpret import InterpEvent
from .links import ThreadLinks
from .registry import TaskStore, STATUS_DONE
from .tasks import Poster, active_instructed_threads, resolve_surface

# 解釈: (画面内容, tasks, notified) -> イベント一覧
Interpreter = Callable[[str, list[dict], dict[str, list[str]]], list[InterpEvent]]

_ICONS = {"done": "✅", "alert": "🚨", "progress": "🔨"}


class SessionWatcher:
    def __init__(self, cmux: CmuxClient, tasks: TaskStore, links: ThreadLinks,
                 interpreter: Interpreter, poster: Poster) -> None:
        self.cmux = cmux
        self.tasks = tasks
        self.links = links
        self.interpret = interpreter
        self.post = poster
        self._screen_hash: dict[str, str] = {}   # surface -> 画面ハッシュ（変化検知）
        self._notified: dict[str, list[str]] = {}  # thread -> 通知済み要約

    def poll_once(self) -> None:
        """紐付き済みセッションの画面を読み、変化があれば AI 解釈してスレッドへ通知。"""
        if not self.cmux.is_running():
            return  # cmux 停止中は「タブが閉じられた」と誤判定しない
        for thread_ts, instr in active_instructed_threads(self.tasks, self.links):
            if not self.links.surface_of(thread_ts):
                continue
            # ref 失効時はタブ名で再発見してから判定（cmux 再起動対応）
            surface = resolve_surface(self.cmux, self.links, thread_ts)
            if not surface:
                self._notify(InterpEvent(
                    thread_ts, "alert",
                    "セッション（cmux のタブ）が閉じられたため追跡を終了します。"
                    "続きはこのスレッドに指示すれば新しいセッションで再開します。"))
                self.tasks.update(thread_ts, status=STATUS_DONE)
                continue
            if not self.cmux.surface_has_process(surface):
                self._notify(InterpEvent(
                    thread_ts, "alert",
                    "セッションの claude が終了しています。"
                    "続きをこのスレッドに指示すれば、同じタブで会話を引き継いで再開を試みます。"))
                self.tasks.update(thread_ts, status=STATUS_DONE)
                continue
            content = self.cmux.read_screen(surface, 200)
            if not content.strip():
                continue
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if self._screen_hash.get(surface) == h:
                continue  # 画面変化なし → LLM を呼ばない（デバウンス）
            self._screen_hash[surface] = h
            events = self.interpret(
                content, [{"id": thread_ts, "task": instr}], self._notified)
            for e in events:
                self._notify(e)

    def _notify(self, e: InterpEvent) -> None:
        # 重複排除（同一スレッドで同じ要約は送らない）
        sent = self._notified.setdefault(e.thread_ts, [])
        if e.summary in sent:
            return
        sent.append(e.summary)
        t = self.tasks.get(e.thread_ts)
        if not t:
            return
        if e.kind == "done":
            self.tasks.update(e.thread_ts, status=STATUS_DONE)
        icon = _ICONS.get(e.kind, "ℹ️")
        # Slack の投稿上限に当たらないよう本文を切る（重複判定は全文で行う）
        self.post(t.channel_id, e.thread_ts, f"{icon} {e.summary[:3500]}")

    def start(self, interval: int = 20) -> None:
        threading.Thread(target=self._loop, args=(interval,), daemon=True).start()

    def _loop(self, interval: int) -> None:
        while True:
            try:
                self.poll_once()
            except Exception:
                pass
            time.sleep(interval)
