"""セッション画面の AI 解釈（純粋ロジック＋AskJson 注入）。

タスクを実行しているセッションの端末画面を、追跡中タスクと一緒に LLM へ渡し、
「新しく通知すべき状態変化」だけを構造化で受け取る。format 契約・相関タグは不要。
"""
from __future__ import annotations

from dataclasses import dataclass

from .llm import AskJson

_PROMPT = """あなたは進捗モニターです。以下は「タスクを実行しているセッション
（対話AI）の端末画面」または「進捗ファイルの内容」です。装飾・スピナー・入力欄・
過去ログが混じっていることがあるので、意味のある内容だけを読み取ってください。
<progress>
{content}
</progress>

追跡中のタスク（id と依頼内容）:
{tasks}

**重要**: 上の内容には、追跡中のタスクとは無関係な作業（人が手で進めているもの、
過去の別タスク等）も混在している。**依頼内容と明確に対応づけられるものだけ**を返し、
どのタスクの話か判断がつかないものは絶対に含めないこと（推測で紐付けない）。
見出しやセクション名に id が書かれている場合は、それを最優先の手掛かりにする。
該当が無ければ空で返すのが正しい動作。

各タスクについて読み取れる状態を判断し、**まだ通知していない意味のある変化だけ**を返してください。
セッションの AI がユーザーに向けて直接述べた回答・判断・着手/完了の報告も、対象タスクに紐づく
意味のある内容なら含めてください（端末の反響入力や Slack から注入された指示文そのものは除く）。
すでに通知済みの内容（下記）は繰り返さないこと:
{notified}

- kind: "done"(完了) / "alert"(要判断・停滞・エラーで人の判断が要る) / "progress"(重要な節目) のいずれか
- 進行中で報告不要なタスクは含めない
- summary は「状態の報告」ではなく **ユーザーにそのまま送る本文**にすること:
  - 調査・質問・説明・報告タスク → **結果の中身そのもの**を具体的に書く（要点を箇条書きや数文で。「報告済み」等で済ませない）
  - 実装・修正タスク → 何を変更/実行したか、結果（テスト結果・注意点）を具体的に
  - 進捗ファイルに結果本文があれば、それを要約せず要点を取り込む
- 出力は JSON のみ: {{"events":[{{"id":"<taskのid>","kind":"done|alert|progress","summary":"<ユーザーに送る本文>"}}]}}
- 該当なしなら {{"events":[]}}
JSON 以外は一切出力しないこと。"""


@dataclass
class InterpEvent:
    thread_ts: str
    kind: str
    summary: str


def interpret(ask: AskJson, model: str, content: str,
              tasks: list[dict], notified: dict[str, list[str]]) -> list[InterpEvent]:
    """tasks: [{id, task}], notified: {id: [既通知summary...]} → 新規イベント一覧。"""
    if not tasks:
        return []
    tasks_str = "\n".join(f"- id={t['id']}: {t['task']}" for t in tasks) or "（なし）"
    notified_str = "\n".join(
        f"- id={tid}: {'; '.join(s)}" for tid, s in notified.items() if s
    ) or "（まだ無し）"
    prompt = _PROMPT.format(content=content[:8000], tasks=tasks_str, notified=notified_str)
    data = ask(prompt, model, timeout=120) or {}
    out: list[InterpEvent] = []
    ids = {t["id"] for t in tasks}
    for e in data.get("events", []):
        tid = str(e.get("id", "")).strip()
        kind = str(e.get("kind", "")).strip()
        if tid in ids and kind in ("done", "alert", "progress"):
            out.append(InterpEvent(thread_ts=tid, kind=kind,
                                   summary=str(e.get("summary", "")).strip()))
    return out
