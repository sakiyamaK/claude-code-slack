"""普通モードの claude -p ヘッドレス実行（SPEC §5）。

- 新規スレッド: claude -p "<prompt>"            → session_id 取得
- 継続スレッド: claude -p "<prompt>" --resume <sid>
モデル/権限モードはグローバル設定（/model /mode）を毎回渡す。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

# 普通モードの claude に持たせる前提（タスク状況を聞かれたら dashboard を読む）
NORMAL_SYSTEM_HINT = (
    "あなたはSlack経由で話しかけられている。"
    "動作中のタスク状況を聞かれたら、対象リポジトリの .claude/tcmtasks/dashboard.md を"
    "読んで要約して答えること。回答は簡潔に。"
)


@dataclass
class RunResult:
    session_id: str | None
    text: str
    ok: bool


class ClaudeRunner:
    def __init__(self, claude_bin: str) -> None:
        self.claude_bin = claude_bin

    def run(
        self,
        prompt: str,
        cwd: str,
        *,
        model: str,
        permission_mode: str,
        resume_session: str | None = None,
        append_system: str | None = NORMAL_SYSTEM_HINT,
    ) -> RunResult:
        cmd = [
            self.claude_bin, "-p", prompt,
            "--output-format", "stream-json", "--verbose",
            "--model", model,
            "--permission-mode", permission_mode,
        ]
        if append_system:
            cmd += ["--append-system-prompt", append_system]
        if resume_session:
            cmd += ["--resume", resume_session]

        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        session_id = resume_session
        final_text = ""
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("session_id"):
                session_id = event["session_id"]
            if event.get("type") == "result":
                final_text = event.get("result", "") or final_text

        ok = proc.returncode == 0
        if not final_text and not ok:
            final_text = (proc.stderr.strip() or "(no output)")[:1500]
        return RunResult(session_id=session_id, text=final_text or "(空の応答)", ok=ok)
