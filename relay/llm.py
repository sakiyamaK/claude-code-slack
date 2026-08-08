"""LLM 呼び出しの唯一の窓口。

claude -p をヘッドレス実行し、応答から JSON を取り出す。
他モジュール（match / interpret）はプロンプト構築と結果解釈だけを持ち、
実行はこの ask_json（AskJson 型の callable）を注入して使う。
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable, Optional, Protocol


class AskJson(Protocol):
    def __call__(self, prompt: str, model: str, timeout: int = 60) -> Optional[dict]: ...


def extract_json(text: str) -> Optional[dict]:
    """応答テキストから最初の { ... } を取り出して parse する（無ければ None）。"""
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


class LlmClient:
    """claude CLI のヘッドレス実行。runner 注入でテスト可能。"""

    def __init__(self, claude_bin: str, cwd: str,
                 runner: Callable | None = None) -> None:
        self.claude_bin = claude_bin
        self.cwd = cwd
        self._runner = runner or self._default_runner

    @staticmethod
    def _default_runner(cmd: list[str], cwd: str, timeout: int):
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)

    def ask_json(self, prompt: str, model: str, timeout: int = 60) -> Optional[dict]:
        cmd = [self.claude_bin, "-p", prompt, "--model", model,
               "--permission-mode", "plan", "--output-format", "text"]
        try:
            proc = self._runner(cmd, self.cwd, timeout)
        except Exception:
            return None
        return extract_json(proc.stdout or "")
