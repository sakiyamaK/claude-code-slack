"""Slack 入口の許可判定とテキスト整形（純粋ロジック）。"""
from __future__ import annotations

import re

_MENTION_RE = re.compile(r"<@[^>]+>")
# Slack は URL を <url> / <url|表示名> に包んで送ってくる（cmux://… も対象）
_LINK_RE = re.compile(r"<([a-z][a-z0-9+.\-]*:[^|>]+)(?:\|[^>]*)?>", re.I)


def normalize_text(text: str) -> str:
    """メンションを除き、Slack が包んだリンクを素の URL に戻す。"""
    stripped = _MENTION_RE.sub("", text or "")
    return _LINK_RE.sub(lambda m: m.group(1), stripped).strip()


def is_allowed(user_id: str, channel_id: str,
               allowed_users: set[str], allowed_channels: set[str]) -> bool:
    """空の許可リストは「制限なし」を意味する。"""
    if allowed_users and user_id not in allowed_users:
        return False
    if allowed_channels and channel_id not in allowed_channels:
        return False
    return True
