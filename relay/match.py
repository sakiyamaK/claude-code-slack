"""Slack の指示と既存セッション/リポジトリの照合（純粋ロジック＋AskJson 注入）。

LLM の実行自体は行わない。呼び出しは llm.AskJson 型の callable を受け取る。
- pick_session: 「該当の作業がすでにセッションとして動いていたら連携する」判定
  ① 決定的一致: チケット風ID（例 NAPP-22262）がタブ名/タイトル/cwd に一致し1つに絞れれば即決
  ② 貼り付け一致: 指示中の特徴的な一節がある画面にそのまま写っていれば即決
     （cmux の画面から文面をコピペして「これの続き」と言う使い方を拾う）
  ③ AI 判定: 候補一覧（タブ名・cwd＋画面の要旨）を渡し「同じ作業」だけ選ばせる
     （曖昧なら None＝新規が安全）
- pick_repo: 複数リポジトリ運用での作業先の決定
  ① 決定的一致: 指示にリポジトリ名が単語として明示されていれば即決
  ② AI 判定: 説明文を手掛かりに選ばせる（判断不能なら既定）
"""
from __future__ import annotations

import re

from .cmux import Session
from .llm import AskJson

_TICKET_RE = re.compile(r"\b[A-Za-z]{2,10}-\d+\b")

_SESSION_PROMPT = """あなたはセッション照合器です。Slack から次の作業指示が来ました:
<instruction>
{instruction}
</instruction>

いま端末（cmux）で動いているセッションの一覧（画面には直近のやりとりが写っています）:
{sessions}

この指示が「既に進行中の、上記いずれかのセッションの作業そのもの」への指示であれば、
そのセッションを1つ選んでください。
- **画面の内容が最も強い手掛かり**（そのセッションがいま何の話をしているか）。
  タブ名・タイトル・作業ディレクトリ（worktree/ブランチ名）も併せて見る
- 指示が画面の話題の続き・確認・修正依頼として自然に読めるなら、それは同じ作業とみなす
- 逆に、どの画面の話題とも噛み合わないなら null（新しい作業として始めるほうが安全）
- 複数当てはまるように見えるときは null（誤って無関係なセッションに注入するのが最悪）
- 出力は JSON のみ: {{"surface": "surface:N", "reason": "<20字程度の根拠>"}}
  または {{"surface": null}}
JSON 以外は一切出力しないこと。"""

_REPO_PROMPT = """あなたは作業指示のルーティング器です。Slack から次の作業指示が来ました:
<instruction>
{instruction}
</instruction>

作業先の候補リポジトリ:
{repos}

この指示がどのリポジトリに対する作業かを1つ選んでください。
- 名前・パス・説明文が手掛かり
- 判断できない場合は {default!r}（既定）を選ぶ
- 出力は JSON のみ: {{"repo": "<名前>"}}
JSON 以外は一切出力しないこと。"""


def find_ticket(text: str) -> str | None:
    """指示文からチケット風ID（新規タブの名前などに使う）を拾う。"""
    m = _TICKET_RE.search(text or "")
    return m.group(0).upper() if m else None


def _ticket_match(instruction: str, sessions: list[Session]) -> str | None:
    tickets = {m.upper() for m in _TICKET_RE.findall(instruction or "")}
    if not tickets:
        return None
    hits = []
    for s in sessions:
        hay = " ".join(filter(None, [s.name, s.title, s.cwd or ""])).upper()
        if any(t in hay for t in tickets):
            hits.append(s.surface)
    return hits[0] if len(hits) == 1 else None


# ── 画面テキストの整形（照合の手掛かりにする） ──────────
# 端末画面は「発話（claude の応答 ⏺ / 利用者の入力 ❯ / recap ※）」と
# 「枠線・ステータス行・ツール出力」が混ざる。話題が分かる発話だけを取り出す。
_SPEECH_HEADS = ("⏺", "❯", "※", ">")
_DROP_HEADS = ("●", "⏵", "⏸", "⧉", "✻", "✽", "✢", "✳", "⎿", "▎", "⧗")
_BORDER_CHARS = set("─═━│┃╭╮╰╯┌┐└┘├┤┬┴┼▔▁·・…")
# 画面に常駐する案内文（内容ではない）。行ごと捨てず、この部分だけ取り除く
_NOISE_RE = re.compile(r"\(?new task\? /clear[^)]*\)?|\(disable recaps[^)]*\)"
                       r"|\(shift\+tab to cycle\)")
_RECAP_MARK = "recap:"
_MIN_FRAGMENT = 12          # これ未満の一節は「特徴的」とみなさない（誤一致を防ぐ）


def _is_border(line: str) -> bool:
    """枠線・区切り線（装飾文字が半分以上）の行か。"""
    return sum(ch in _BORDER_CHARS for ch in line) * 2 >= len(line)


def screen_blocks(screen: str) -> list[str]:
    """画面を発話ブロックに分解する（端末の折り返しは1つに繋げる）。

    ツール出力・差分・枠線は落ちる。発話マーカーの無い行は、直前の発話の
    折り返しとしてのみ拾う（見出しの無いダンプは拾わない）。
    """
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            blocks.append(" ".join(current))
            current.clear()

    for raw in (screen or "").splitlines():
        line = _NOISE_RE.sub("", raw).strip()
        if not line or _is_border(line) or line.startswith(_DROP_HEADS):
            flush()
            continue
        head = next((h for h in _SPEECH_HEADS if line.startswith(h)), None)
        if head:
            flush()
            line = line[len(head):].strip()
        elif not current:
            continue        # 発話の続きではない行（ツール出力など）は捨てる
        if line:
            current.append(line)
    flush()
    return blocks


def screen_digest(screen: str, max_chars: int = 600) -> str:
    """照合に渡す画面の要旨。claude が出す recap を優先し、あとは直近の発話。"""
    blocks = screen_blocks(screen)
    picked = [b for b in blocks if _RECAP_MARK in b.lower()][-1:]
    budget = max_chars - sum(len(b) for b in picked)
    tail: list[str] = []
    for b in reversed([b for b in blocks if b not in picked]):
        if budget - len(b) < 0:
            break
        tail.append(b)
        budget -= len(b) + 1
    return " / ".join(picked + list(reversed(tail)))[:max_chars]


def _squash(text: str) -> str:
    """空白を全て除いた比較用の文字列（端末の折り返しを無視して突き合わせる）。"""
    return re.sub(r"\s+", "", text or "")


def _paste_match(instruction: str, sessions: list[Session]) -> str | None:
    """指示中の特徴的な一節が、ある画面にそのまま写っていれば即決（コピペ運用）。"""
    fragments = [f for f in re.split(r"[\n。．!？?]+", instruction or "")
                 if len(_squash(f)) >= _MIN_FRAGMENT]
    if not fragments:
        return None
    hits = []
    for s in sessions:
        haystack = _squash(s.screen)
        if haystack and any(_squash(f) in haystack for f in fragments):
            hits.append(s.surface)
    return hits[0] if len(hits) == 1 else None


def describe_session(s: Session) -> str:
    """照合プロンプト用の1件分の説明（画面の要旨まで含める）。"""
    head = (f"- {s.surface}: タブ名={s.name!r} タイトル={s.title!r} "
            f"cwd={s.cwd or '不明'}")
    digest = screen_digest(s.screen)
    return f"{head}\n    画面: {digest}" if digest else head


def pick_session(ask: AskJson, model: str,
                 instruction: str, sessions: list[Session]) -> str | None:
    """指示に対応する既存セッションの surface ref を返す。無ければ None。"""
    if not sessions:
        return None
    hit = _ticket_match(instruction, sessions) or _paste_match(instruction, sessions)
    if hit:
        return hit
    data = ask(_SESSION_PROMPT.format(
        instruction=(instruction or "")[:2000],
        sessions="\n".join(describe_session(s) for s in sessions)), model)
    # reason は根拠を言わせて精度を上げるためのもので、判定には surface だけを使う
    ref = (data or {}).get("surface")
    valid = {s.surface for s in sessions}
    return ref if isinstance(ref, str) and ref in valid else None


def pick_repo(ask: AskJson, model: str, instruction: str,
              repos: dict[str, str], descriptions: dict[str, str],
              default: str) -> str:
    """指示の作業先リポジトリ名を返す。単一構成なら即 default。"""
    if len(repos) <= 1:
        return default
    # 決定的一致: 指示にリポジトリ名が単語として明示されていれば即決（LLM を呼ばない）
    hits = [n for n in repos
            if re.search(rf"(?<![0-9A-Za-z_]){re.escape(n)}(?![0-9A-Za-z_])",
                         instruction or "", re.I)]
    if len(hits) == 1:
        return hits[0]
    lines = []
    for n, p in repos.items():
        d = (descriptions.get(n) or "").strip()
        lines.append(f"- {n}: {p}" + (f"（{d}）" if d else ""))
    data = ask(_REPO_PROMPT.format(
        instruction=(instruction or "")[:2000], repos="\n".join(lines), default=default), model)
    name = (data or {}).get("repo")
    return name if isinstance(name, str) and name in repos else default
