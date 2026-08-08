"""セッション監視。各セッションの画面本文をそのままスレッドへ転送する。

- 画面ハッシュの変化検知でデバウンス（変わり続けている間＝出力中は送らない）
- 画面が落ち着いたら、前回送信時との差分（新しく増えた行）をそのまま投稿する
- 要約・AI 解釈はしない（内容の取り違えを防ぐため原文を送る）
- タブが閉じられていたら追跡を終了する（続きの指示で再開できる旨を通知）
"""
from __future__ import annotations

import difflib
import hashlib
import threading
import time

from .cmux import CmuxClient
from .links import ThreadLinks
from .registry import TaskStore, STATUS_DONE
from .tasks import Poster, active_instructed_threads, resolve_surface

_MAX_BODY = 3500  # Slack 投稿上限対策（超えたら末尾側を優先して残す）


def new_lines(prev: list[str], cur: list[str]) -> list[str]:
    """前回画面 prev と今回画面 cur を行単位で比較し、新しく現れた行を返す。

    画面下部の入力欄・区切り線など、両方に共通する行は差分に含まれない。
    （末尾スクロールの単純比較では入力欄が邪魔をするため difflib で対応）
    """
    if not prev:
        return cur
    out: list[str] = []
    sm = difflib.SequenceMatcher(None, prev, cur, autojunk=False)
    for op, _i1, _i2, j1, j2 in sm.get_opcodes():
        if op in ("insert", "replace"):
            out.extend(cur[j1:j2])
    return out


class SessionWatcher:
    def __init__(self, cmux: CmuxClient, tasks: TaskStore, links: ThreadLinks,
                 poster: Poster) -> None:
        self.cmux = cmux
        self.tasks = tasks
        self.links = links
        self.post = poster
        self._screen_hash: dict[str, str] = {}   # surface -> 前回ポーリングの画面ハッシュ
        self._sent_lines: dict[str, list[str]] = {}  # surface -> 前回送信時の画面（行）
        self._alerted: dict[str, list[str]] = {}     # thread -> 通知済みアラート

    def poll_once(self) -> None:
        """紐付き済みセッションの画面を読み、出力が落ち着いていたら差分を転送する。"""
        if not self.cmux.is_running():
            return  # cmux 停止中は「タブが閉じられた」と誤判定しない
        for thread_ts, _instr in active_instructed_threads(self.tasks, self.links):
            if not self.links.surface_of(thread_ts):
                continue
            # ref 失効時はタブ名で再発見してから判定（cmux 再起動対応）
            surface = resolve_surface(self.cmux, self.links, thread_ts)
            if not surface:
                self._alert(thread_ts,
                            "セッション（cmux のタブ）が閉じられたため追跡を終了します。"
                            "続きはこのスレッドに指示すれば新しいセッションで再開します。")
                self.tasks.update(thread_ts, status=STATUS_DONE)
                continue
            if not self.cmux.surface_has_process(surface):
                self._alert(thread_ts,
                            "セッションの claude が終了しています。"
                            "続きをこのスレッドに指示すれば、同じタブで会話を引き継いで再開を試みます。")
                self.tasks.update(thread_ts, status=STATUS_DONE)
                continue
            content = self.cmux.read_screen(surface, 200)
            if not content.strip():
                continue
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if self._screen_hash.get(surface) != h:
                # 画面が動いている（出力中）→ 落ち着くまで送らない
                self._screen_hash[surface] = h
                continue
            # 画面が安定 → 前回送信分との差分を原文のまま転送
            cur = content.splitlines()
            # 初回は全文、以降は前回送信分との差分（新しく増えた行）を送る
            diff = new_lines(self._sent_lines.get(surface, []), cur)
            body = "\n".join(diff).strip()
            if not body:
                continue
            self._sent_lines[surface] = cur
            t = self.tasks.get(thread_ts)
            if not t:
                continue
            if len(body) > _MAX_BODY:  # 末尾（最新の出力）を優先して残す
                body = "…（前略）…\n" + body[-_MAX_BODY:]
            self.post(t.channel_id, thread_ts, f"```{body}```")

    def _alert(self, thread_ts: str, text: str) -> None:
        # 同一スレッドへの同じアラートは繰り返さない
        sent = self._alerted.setdefault(thread_ts, [])
        if text in sent:
            return
        sent.append(text)
        t = self.tasks.get(thread_ts)
        if not t:
            return
        self.post(t.channel_id, thread_ts, f"🚨 {text}")

    def start(self, interval: int = 20) -> None:
        threading.Thread(target=self._loop, args=(interval,), daemon=True).start()

    def _loop(self, interval: int) -> None:
        while True:
            try:
                self.poll_once()
            except Exception:
                pass
            time.sleep(interval)
