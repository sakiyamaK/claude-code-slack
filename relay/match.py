"""Slack の指示と既存セッション/リポジトリの照合（純粋ロジック＋AskJson 注入）。

LLM の実行自体は行わない。呼び出しは llm.AskJson 型の callable を受け取る。
- pick_session: 「該当の作業がすでにセッションとして動いていたら連携する」判定
  ① 決定的一致: チケット風ID（例 NAPP-22262）がタブ名/タイトル/cwd に一致し1つに絞れれば即決
  ② AI 判定: 候補一覧を渡し「明確に同じ作業」だけ選ばせる（曖昧なら None＝新規が安全）
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

いま端末（cmux）で動いているセッションの一覧:
{sessions}

この指示が「既に進行中の、上記いずれかのセッションの作業そのもの」への指示であれば、
そのセッションを1つ選んでください。
- タブ名・タイトル・作業ディレクトリ（worktree/ブランチ名）が手掛かり
- **明確に同じ作業だと言い切れる場合だけ**選ぶ。少しでも曖昧なら null
  （誤って無関係なセッションに注入するより、新規セッションを作るほうが安全）
- 出力は JSON のみ: {{"surface": "surface:N"}} または {{"surface": null}}
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


def pick_session(ask: AskJson, model: str,
                 instruction: str, sessions: list[Session]) -> str | None:
    """指示に対応する既存セッションの surface ref を返す。無ければ None。"""
    if not sessions:
        return None
    hit = _ticket_match(instruction, sessions)
    if hit:
        return hit
    lines = [
        f"- {s.surface}: タブ名={s.name!r} タイトル={s.title!r} cwd={s.cwd or '不明'}"
        for s in sessions
    ]
    data = ask(_SESSION_PROMPT.format(
        instruction=(instruction or "")[:2000], sessions="\n".join(lines)), model)
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
