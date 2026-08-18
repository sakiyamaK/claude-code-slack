"""メッセージの振り分けだけを担う層。

DM のメッセージを種別（ピッカー返信 / コマンド / セッションID指名 / 継続 / 中断 /
新規指示）に判定し、対応するサービスへ委譲する。実行は行わない。
"""
from __future__ import annotations

from typing import Callable

from . import intent as intent_mod
from .commands import CommandService
from .links import ThreadLinks
from .parsing import parse_command, parse_session_ref
from .tasks import TaskService, Poster


class MessageRouter:
    def __init__(self, commands: CommandService, tasks: TaskService,
                 links: ThreadLinks, poster: Poster,
                 classify: Callable = intent_mod.classify) -> None:
        self.commands = commands
        self.tasks = tasks
        self.links = links
        self.post = poster
        self._classify = classify

    def route(self, channel: str, user: str, thread_ts: str, text: str) -> None:
        if not (text or "").strip():
            self.post(channel, thread_ts, "内容を教えてください。")
            return
        # 1) ピッカー待ち（/model /mode の番号返信）。番号以外なら破棄して通常処理へ
        if self.commands.has_pending_pick(thread_ts):
            if text.strip().isdigit():
                self.commands.apply_pick(channel, user, thread_ts, int(text.strip()))
                return
            self.commands.cancel_pick(thread_ts)
        # 2) コマンド
        cmd = parse_command(text)
        if cmd.is_command:
            self.commands.handle(channel, user, thread_ts, cmd.name, cmd.arg)
            return
        # 3) cmux のセッションID（workspace_id / cmux://workspace/…）が貼られていたら、
        #    照合を通さずそのタブへ直接つなぐ（既存の紐付けより指名を優先する）。
        #    添えられた指示は、紐付け済みスレッドの発言と同じ扱いで届ける
        ref = parse_session_ref(text)
        if ref.found:
            if self.tasks.attach_session(channel, user, thread_ts, ref):
                if ref.rest:
                    self._instruct_linked(channel, user, thread_ts, ref.rest)
                return
            if ref.explicit:
                return          # 指名が外れた案内は済んでいる（勝手に新規タブは作らない）
            # 目印の無い UUID がどのタブも指していない = ただの指示文だった
        # 4) セッション紐付け済みスレッド → 中断操作 or 同じセッションへの追加指示
        if self.links.surface_of(thread_ts):
            self._instruct_linked(channel, user, thread_ts, text)
            return
        # 5) 新しいスレッド = 新しい指示。「やめて」だけはどのタスクか分からないので案内する
        if self._classify(text, []).is_operation and self.tasks.has_active():
            self.tasks.operation(channel, thread_ts, text)  # 未紐付けの案内が返る
            return
        self.tasks.new_task(channel, user, thread_ts, text)

    def _instruct_linked(self, channel: str, user: str, thread_ts: str, text: str) -> None:
        """紐付き済みセッションへの発言（中断操作なら割り込み、それ以外は追加指示）。"""
        if self._classify(text, []).is_operation:
            self.tasks.operation(channel, thread_ts, text)
        else:
            self.tasks.follow_up(channel, user, thread_ts, text)
