"""cmux 連携（SPEC §8, §9）。

cmux CLI（Unix socket 制御）経由でマネージャーペインを常駐・注入する。
- ensure(): cmux 起動確認 → 無ければ起動、マネージャー agent-session を用意
- send(): マネージャーペインへ指示行を注入（send + Enter）
- read_screen(): ペイン出力の読み取り（catch-up・状態確認）

cmux が起動できない（Mac未ログイン等）場合は CmuxUnavailable を投げる。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

_SURFACE_RE = re.compile(r"\bsurface:\d+\b")
_WORKSPACE_RE = re.compile(r"\bworkspace:\d+\b")
MANAGER_WORKSPACE_NAME = "relay-manager"


class CmuxUnavailable(RuntimeError):
    """cmux が起動しておらず、起動もできない（Macロック/ログイン画面の可能性）。"""


class CmuxClient:
    def __init__(self, cmux_bin: str) -> None:
        self.bin = cmux_bin

    def _run(self, *args: str, timeout: int = 20) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.bin, *args], capture_output=True, text=True, timeout=timeout
        )

    def installed(self) -> bool:
        return os.path.exists(self.bin)

    def is_running(self) -> bool:
        if not self.installed():
            return False
        try:
            return self._run("ping", timeout=5).returncode == 0
        except Exception:
            return False

    def launch(self) -> bool:
        """cmux アプリを起動する。GUIセッションが無ければ失敗する。"""
        try:
            # `cmux <path>` は必要なら cmux 自体を起動する仕様
            subprocess.run(["open", "-a", "cmux"], capture_output=True, text=True, timeout=15)
        except Exception:
            return False
        # 起動待ち（socket が立つまで）
        for _ in range(20):
            if self.is_running():
                return True
            time.sleep(0.5)
        return self.is_running()

    def ensure_running(self) -> None:
        if self.is_running():
            return
        if not self.launch():
            raise CmuxUnavailable(
                "cmux を起動できません（Macがロック/ログイン画面の可能性）"
            )

    # ── マネージャー（ターミナルで動く対話 claude） ────
    # agent-session(React UI) は new-surface しても claude プロセスが起動せず prompt が届かない。
    # 代わりに「ターミナルで対話 claude を起動」し、send+Enter でプロンプトを注入する（検証済み）。
    def surface_exists(self, surface: str) -> bool:
        try:
            return surface in self._run("list-pane-surfaces").stdout
        except Exception:
            return False

    def _wait_ready(self, surface: str, timeout: int = 40) -> bool:
        """claude の対話UIが立ち上がるまで待つ（入力欄プロンプトの出現を検出）。"""
        for _ in range(timeout):
            screen = self._run("read-screen", "--surface", surface, "--lines", "40").stdout
            if "accept edits" in screen or "shift+tab to cycle" in screen or "❯" in screen:
                return True
            time.sleep(1)
        return False

    def new_manager(self, cwd: str, model: str, permission_mode: str) -> str:
        """relay-manager ワークスペースを作り、ターミナルで対話 claude を起動して surface ref を返す。"""
        cmd = f"claude --permission-mode {permission_mode} --model {model}"
        res = self._run(
            "workspace", "create", "--name", MANAGER_WORKSPACE_NAME,
            "--cwd", cwd, "--command", cmd, "--focus", "false",
        )
        m = _WORKSPACE_RE.search(res.stdout or "")
        if not m:
            raise RuntimeError(f"workspace 作成失敗: {res.stdout!r} / {res.stderr!r}")
        ws = m.group(0)
        # ワークスペースの端末サーフェスを取得
        surfaces = self._run("list-pane-surfaces", "--workspace", ws).stdout
        sm = _SURFACE_RE.search(surfaces or "")
        if not sm:
            raise RuntimeError(f"端末サーフェスが見つかりません: {surfaces!r}")
        surface = sm.group(0)
        self._wait_ready(surface)  # claude 起動待ち（初回は必須）
        return surface

    def ensure_manager(self, cwd: str, stored_surface: str | None,
                       model: str, permission_mode: str) -> str:
        """cmux 起動＋マネージャー端末確保。使える端末 surface ref を返す。"""
        self.ensure_running()
        if stored_surface and self.surface_exists(stored_surface):
            return stored_surface
        return self.new_manager(cwd, model, permission_mode)

    # ── プロンプト投入（端末 send + Enter） ────────────
    def send(self, surface: str, text: str) -> None:
        r = self._run("send", "--surface", surface, text)
        if r.returncode != 0:
            raise RuntimeError(f"cmux send 失敗: {r.stderr.strip() or r.stdout.strip()}")
        time.sleep(0.3)
        self._run("send-key", "--surface", surface, "enter")
