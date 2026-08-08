"""Slack 入口の許可判定とテキスト整形（純粋ロジック）。"""
from __future__ import annotations

import re

_MENTION_RE = re.compile(r"<@[^>]+>")


def strip_mentions(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def is_allowed(user_id: str, channel_id: str,
               allowed_users: set[str], allowed_channels: set[str]) -> bool:
    """空の許可リストは「制限なし」を意味する。"""
    if allowed_users and user_id not in allowed_users:
        return False
    if allowed_channels and channel_id not in allowed_channels:
        return False
    return True
