"""軽い意図判定。

`/コマンド` 以外の自然言語を「running タスクへの中断操作」か「通常の指示」に振り分ける。
まずはヒューリスティック（キーワード＋タスク名照合）。曖昧・高度化は LLM 化余地あり。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 「止める」系の操作意図。
# 単純な部分一致は誤爆する（kill ⊂ skill / stop ⊂ stopped / 止め ⊂ 受け止める）ため、
# ASCII は単語境界を要求し、日本語は指示形に絞る。
_ASCII_STOP_RE = re.compile(r"\b(?:stop|cancel|kill|abort)\b", re.IGNORECASE)
# 「止め」は直前が動詞連用形のかななら複合動詞（受け止める・書き止める・呼び止める等）
# なので除外する。
_JA_STOP_RE = re.compile(
    r"(?<![けきいびみち])止め[てろ]|やめ[てろ]|中断|中止|停止|キャンセル|ストップ"
)


@dataclass
class Intent:
    is_operation: bool
    action: str = ""          # "stop" 等
    task: str | None = None   # 照合できたタスク名（branch）


def _match_task(text: str, known_tasks: list[str]) -> str | None:
    # 長い名前から優先照合（部分一致の取り違え防止）
    for name in sorted(known_tasks, key=len, reverse=True):
        if name and name in text:
            return name
    return None


def classify(text: str, known_tasks: list[str]) -> Intent:
    t = text or ""
    is_stop = bool(_ASCII_STOP_RE.search(t) or _JA_STOP_RE.search(t))
    if is_stop:
        return Intent(is_operation=True, action="stop",
                      task=_match_task(text, known_tasks))
    return Intent(is_operation=False)
