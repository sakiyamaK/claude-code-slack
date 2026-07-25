"""入力パース層（純粋ロジック・副作用なし）。

- タスクモード判定（寛容パース）
- コマンド判定（/model /mode /commit /push /firebase /testflight）
- バージョン記法（X.X.X+1 等）→ 具体的なバージョン算出

SPEC §6, §13, §13.5 準拠。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ── タスクモード判定（SPEC §6） ─────────────────────────
def build_task_regex(keywords: list[str]) -> re.Pattern:
    """先頭 (作業|タスク|task) [:：] 本文 を寛容にマッチする正規表現を作る。"""
    alt = "|".join(re.escape(k) for k in keywords)
    return re.compile(rf"^\s*(?:{alt})\s*[:：]\s*([\s\S]*)$", re.IGNORECASE)


@dataclass
class TaskParse:
    is_task: bool
    body: str = ""          # 区切り以降の本文（改行含む）
    empty_body: bool = False  # 「作業:」だけで本文が空


def parse_task_mode(text: str, keywords: list[str]) -> TaskParse:
    m = build_task_regex(keywords).match(text or "")
    if not m:
        return TaskParse(is_task=False)
    body = m.group(1).strip()
    return TaskParse(is_task=True, body=body, empty_body=(body == ""))


# ── コマンド判定（SPEC §13, §13.5） ─────────────────────
# 組み込み（generic）。プロジェクト固有コマンドは config.yml の commands で定義する。
BUILTIN_COMMANDS = {"model", "mode", "commit", "push"}

_CMD_RE = re.compile(r"^\s*/([a-zA-Z][a-zA-Z0-9_-]*)\s*(.*)$", re.DOTALL)


@dataclass
class CommandParse:
    is_command: bool
    name: str = ""
    arg: str = ""


def parse_command(text: str) -> CommandParse:
    """先頭が /<name> ならコマンドとして解釈（組み込み/カスタムの判定は呼び側）。"""
    m = _CMD_RE.match(text or "")
    if not m:
        return CommandParse(is_command=False)
    return CommandParse(is_command=True, name=m.group(1).lower(), arg=m.group(2).strip())


# ── バージョン記法（SPEC §13.5） ────────────────────────
# 各桁: X（維持）/ X+N（現在+N）/ 数字（固定）
# 例: X.X.X+1（パッチ+1）/ X.X+1.0（マイナー+1・パッチ0）/ X+1.0.0（メジャー+1）
_VER_TOKEN = re.compile(r"^(?:X|X\+\d+|\d+)$", re.IGNORECASE)


@dataclass
class VersionResult:
    version: tuple[int, int, int]
    corrected: bool            # 慣習に合わせて補正したか（下位桁を0にした）
    note: str = ""


def parse_version_spec(
    spec: str | None, current: tuple[int, int, int]
) -> VersionResult:
    """バージョン記法を現在値に適用して具体的な (major, minor, patch) を返す。

    - spec が空/None → 現状維持（据え置き配信）
    - 桁上げ時は下位桁を0リセット（慣習・案B）。ユーザ指定が慣習と食い違えば補正して note を返す。
    """
    if not spec or not spec.strip():
        return VersionResult(version=current, corrected=False)

    tokens = spec.strip().split(".")
    if len(tokens) != 3 or not all(_VER_TOKEN.match(t) for t in tokens):
        raise ValueError(f"バージョン記法が不正です: {spec!r}（例: X.X.X+1 / X.X+1.0）")

    # まず記法通りに各桁を算出
    literal: list[int] = []
    bumped_index: int | None = None  # +N が入った最上位桁
    for i, (tok, cur) in enumerate(zip(tokens, current)):
        up = tok.upper()
        if up == "X":
            literal.append(cur)
        elif up.startswith("X+"):
            literal.append(cur + int(up[2:]))
            if bumped_index is None:
                bumped_index = i
        else:
            literal.append(int(tok))

    # 慣習（案B）: 桁上げした桁より下位は0にする
    conventional = list(literal)
    if bumped_index is not None:
        for j in range(bumped_index + 1, 3):
            conventional[j] = 0

    corrected = conventional != literal
    note = ""
    if corrected:
        note = (
            f"慣習に合わせて {'.'.join(map(str, conventional))} にします"
            f"（指定通りだと {'.'.join(map(str, literal))}）"
        )
    return VersionResult(
        version=(conventional[0], conventional[1], conventional[2]),
        corrected=corrected,
        note=note,
    )
