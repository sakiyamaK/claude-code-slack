"""軽い意図判定（SPEC §14）。

`作業:`・`/コマンド` 以外の自然言語を「running タスクへの操作」か「その他（質問等）」に振り分ける。
まずはヒューリスティック（キーワード＋タスク名照合）。曖昧・高度化は LLM 化余地あり（SPEC §22）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 「止める」系の操作意図
STOP_MARKERS = [
    "止め", "停止", "やめ", "中断", "キャンセル", "ストップ", "stop", "cancel", "kill",
]


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
    t = (text or "").lower()
    is_stop = any(m in t for m in STOP_MARKERS)
    if is_stop:
        return Intent(is_operation=True, action="stop",
                      task=_match_task(text, known_tasks))
    return Intent(is_operation=False)
