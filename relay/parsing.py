"""入力パース層（純粋ロジック・副作用なし）。

- コマンド判定（`/<name> <arg>` の書式。名前の解釈は commands 側）
- cmux のセッションID指名（workspace_id / cmux://workspace/<UUID>）の抽出
- バージョン記法（X.X.X+1 等）→ 具体的なバージョン算出
- テンプレ整形（未定義プレースホルダは保持）
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# cmux の ID（window/workspace/pane/surface すべて UUID）。cmux.py も使う
UUID_PATTERN = r"[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}"


# ── コマンド判定 ────────────────────────────────────────
# どの名前を受け付けるかは commands.CommandService が決める（ここは書式だけ見る）。
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


# ── cmux セッションID の指名 ────────────────────────────
# すでに cmux で動いているタブを Slack から名指しするための ID。次のどれでも貼れる:
#   cmux://workspace/<UUID> ... タブの「Copy Link」で得られる
#   workspace_id=<UUID>     ... `cmux identify --id-format both` の出力（surface_id も同様）
#   <UUID> だけ             ... 上記から UUID を抜き出して貼った場合
# ID の前後に指示文を添えてよい（ID を取り除いた残りを指示として扱う）。
_SESSION_ID_RE = re.compile(
    rf"""(?:
            cmux://(?P<kind1>workspace|surface)/
          | ["']?(?P<kind2>workspace|surface)[_\- ]?id["']?\s*[=:]\s*["']?
        )?
        (?P<id>{UUID_PATTERN})["']?/?""",
    re.IGNORECASE | re.VERBOSE)


@dataclass
class SessionRef:
    session_id: str = ""       # 貼られた UUID（無ければ空）
    is_surface: bool = False   # surface_id として貼られたか（既定は workspace 扱い）
    explicit: bool = False     # cmux:// や workspace_id= の目印付きか（裸の UUID なら False）
    rest: str = ""             # ID を取り除いた残り＝実際の指示

    @property
    def found(self) -> bool:
        return bool(self.session_id)


def parse_session_ref(text: str) -> SessionRef:
    """本文から cmux のセッションID指名を1つ抜き出し、残りを指示として返す。

    目印の無い裸の UUID も拾うが、指示文に紛れた UUID の可能性があるため
    explicit=False にする（該当タブが無ければ通常の指示として扱えるように）。
    """
    m = _SESSION_ID_RE.search(text or "")
    if not m:
        return SessionRef(rest=(text or "").strip())
    kind = (m.group("kind1") or m.group("kind2") or "").lower()
    rest = (text[:m.start()] + " " + text[m.end():])
    return SessionRef(
        session_id=m.group("id"),
        is_surface=(kind == "surface"),
        explicit=bool(kind),
        # ID を囲っていた括弧・引用符の残骸は指示として扱わない
        rest=re.sub(r"\s+", " ", rest).strip(" \t\n\"',:<>"),
    )


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


# ── テンプレ整形 ────────────────────────────────────────
def format_template(template: str, ctx: dict) -> str:
    """{key} を埋める。未定義プレースホルダはそのまま残す。"""
    class _Defaulting(dict):
        def __missing__(self, k):
            return "{" + k + "}"
    return (template or "").format_map(_Defaulting(ctx))
