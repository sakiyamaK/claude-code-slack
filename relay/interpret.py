"""進捗ファイルの AI 解釈。

外部 backend の進捗ファイル（書式自由）を、追跡中タスク一覧と一緒に claude -p へ渡し、
「新しく通知すべき状態変化」だけを構造化で受け取る。format 契約・相関タグは不要。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

_PROMPT = """あなたは進捗モニターです。以下は「進捗ファイルの現在の内容」です。
<progress>
{content}
</progress>

追跡中のタスク（id と依頼内容）:
{tasks}

各タスクについて、進捗ファイルから読み取れる状態を判断し、**まだ通知していない意味のある変化だけ**を返してください。
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


def _extract_json(text: str) -> dict:
    # 応答から最初の { ... } を取り出す
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {"events": []}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"events": []}


def interpret(claude_bin: str, model: str, cwd: str, content: str,
              tasks: list[dict], notified: dict[str, list[str]]) -> list[InterpEvent]:
    """tasks: [{id, task}], notified: {id: [既通知summary...]} → 新規イベント一覧。"""
    if not tasks:
        return []
    tasks_str = "\n".join(f"- id={t['id']}: {t['task']}" for t in tasks) or "（なし）"
    notified_str = "\n".join(
        f"- id={tid}: {'; '.join(s)}" for tid, s in notified.items() if s
    ) or "（まだ無し）"
    prompt = _PROMPT.format(content=content[:8000], tasks=tasks_str, notified=notified_str)

    cmd = [claude_bin, "-p", prompt, "--model", model,
           "--permission-mode", "plan", "--output-format", "text"]
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    except Exception:
        return []
    data = _extract_json(proc.stdout or "")
    out: list[InterpEvent] = []
    ids = {t["id"] for t in tasks}
    for e in data.get("events", []):
        tid = str(e.get("id", "")).strip()
        kind = str(e.get("kind", "")).strip()
        if tid in ids and kind in ("done", "alert", "progress"):
            out.append(InterpEvent(thread_ts=tid, kind=kind, summary=str(e.get("summary", "")).strip()))
    return out
