"""todo.md（`.claude/tcmtasks/todo.md`）の更新（SPEC §7, §12, §19）。

方針:
- relay は「社長の代理」として todo.md を書く（manager.agent.md「todo.md は社長が書く」に整合）
- **ブランチ名は relay が発明しない**。指示文に明示ブランチがあればそれを見出しに使い、
  無ければ見出しを未確定にしてマネージャーに命名を委ねる（本文にその旨を明記）
- 本文末尾に不可視の相関タグ `<!-- from-thread: <ts> -->` を必ず入れる
  → manager.agent.md 追加ルールで dashboard に branch↔thread を併記させ、relay が通知をルーティングする

【要確認・実装細部】見出し未確定時の扱い（マネージャーへの命名依頼の書式）は
運用しながら調整する余地あり。SPEC §22 参照。
"""
from __future__ import annotations

import os
import re

# 指示文からの明示ブランチ検出（例: "feature/fix-login", "ブランチはfix-login", "branch: xxx"）
_EXPLICIT_BRANCH = re.compile(
    r"(?:ブランチ\s*(?:は|:|：)\s*|branch\s*[:：]?\s*)([A-Za-z0-9._\-/]+)"
    r"|(?<![\w/])(feature/[A-Za-z0-9._\-/]+|experiment/[A-Za-z0-9._\-/]+)",
    re.IGNORECASE,
)


def detect_branch(instruction: str) -> str | None:
    m = _EXPLICIT_BRANCH.search(instruction or "")
    if not m:
        return None
    return (m.group(1) or m.group(2) or "").strip() or None


def from_thread_tag(thread_ts: str) -> str:
    return f"<!-- from-thread: {thread_ts} -->"


def build_entry(instruction: str, thread_ts: str, branch: str | None) -> str:
    """todo.md へ追記する1エントリのテキストを組み立てる。"""
    if branch:
        heading = branch
        naming = ""
    else:
        # 見出しは未確定。マネージャーに命名を委ねる（relay はタスク前提名を付けない）
        heading = "UNNAMED"
        naming = "\n> ブランチ名はタスク内容に応じてマネージャーが決めてください。\n"
    return (
        f"# {heading}\n"
        f"{instruction.strip()}\n"
        f"{naming}"
        f"{from_thread_tag(thread_ts)}\n"
    )


def append_task(todo_path: str, instruction: str, thread_ts: str) -> tuple[str, str | None]:
    """todo.md に新規タスクを追記する。(entry_text, detected_branch) を返す。

    同一 thread の再指示は build_entry を呼ぶ側（orchestrator）で継続扱いにする。
    """
    branch = detect_branch(instruction)
    entry = build_entry(instruction, thread_ts, branch)

    os.makedirs(os.path.dirname(todo_path), exist_ok=True)
    existing = ""
    if os.path.exists(todo_path):
        with open(todo_path, "r", encoding="utf-8") as f:
            existing = f.read()

    sep = "" if existing.endswith("\n\n") or not existing else "\n"
    with open(todo_path, "w", encoding="utf-8") as f:
        f.write(existing + sep + "\n" + entry)
    return entry, branch


def find_entry_by_thread(todo_path: str, thread_ts: str) -> bool:
    """指定 thread の from-thread タグを持つエントリが既にあるか。"""
    if not os.path.exists(todo_path):
        return False
    with open(todo_path, "r", encoding="utf-8") as f:
        return from_thread_tag(thread_ts) in f.read()
