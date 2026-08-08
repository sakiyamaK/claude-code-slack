"""メッセージの振り分けだけを担う層。

DM のメッセージを種別（ピッカー返信 / コマンド / 継続 / 中断 / 新規指示）に
判定し、対応するサービスへ委譲する。実行は行わない。
"""
from __future__ import annotations

from typing import Callable

from . import intent as intent_mod
from .commands import CommandService
from .links import ThreadLinks
from .parsing import parse_command
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
                self.commands.apply_pick(channel, thread_ts, int(text.strip()))
                return
            self.commands.cancel_pick(thread_ts)
        # 2) コマンド
        cmd = parse_command(text)
        if cmd.is_command:
            self.commands.handle(channel, user, thread_ts, cmd.name, cmd.arg)
            return
        # 3) セッション紐付け済みスレッド → 中断操作 or 同じセッションへの追加指示
        if self.links.surface_of(thread_ts):
            if self._classify(text, []).is_operation:
                self.tasks.operation(channel, thread_ts, text)
            else:
                self.tasks.follow_up(channel, user, thread_ts, text)
            return
        # 4) 新しいスレッド = 新しい指示。「やめて」だけはどのタスクか分からないので案内する
        if self._classify(text, []).is_operation and self.tasks.has_active():
            self.tasks.operation(channel, thread_ts, text)  # 未紐付けの案内が返る
            return
        self.tasks.new_task(channel, user, thread_ts, text)
