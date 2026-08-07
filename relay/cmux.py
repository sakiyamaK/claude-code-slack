"""cmux 連携（1タスク=1セッション）。

cmux CLI（Unix socket 制御）経由で、タスクごとの対話 claude セッション
（workspace タブ）を列挙・作成・操作する。
- list_sessions(): 既存セッションの列挙（タブ名・タイトル・cwd）。Slack 指示との照合に使う
- new_session(): タスク用 workspace タブを作り対話 claude を起動（cmux の UI からも見える）
- send(): セッションへ指示行を注入（send + Enter）
- read_screen(): 画面出力の読み取り（進捗の AI 解釈の入力）
- interrupt(): 実行中の claude への割り込み（ESC）

cmux が起動できない（Mac未ログイン等）場合は CmuxUnavailable を投げる。
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass

_SURFACE_RE = re.compile(r"\bsurface:\d+\b")
_WORKSPACE_RE = re.compile(r"\bworkspace:\d+\b")
_WINDOW_UUID_RE = re.compile(r"\b[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\b")
_PANE_RE = re.compile(r"\bpane:\d+\b")
# `workspace list` / `list-pane-surfaces` の行: 「* workspace:3  名前  [selected]」
_LIST_LINE_RE = re.compile(
    r"^[*\s]*((?:workspace|surface):\d+)\s+(.*?)(?:\s+\[selected\])?\s*$")

# lsof で cwd を引くとき、シェルより実作業プロセス（claude 等）を優先する
_SHELLS = {"zsh", "bash", "fish", "sh", "-zsh", "-bash", "login"}


class CmuxUnavailable(RuntimeError):
    """cmux が起動しておらず、起動もできない（Macロック/ログイン画面の可能性）。"""


@dataclass
class Session:
    surface: str        # surface ref（送信・読取のキー）
    workspace: str      # workspace ref
    name: str           # workspace 名（タブ名）
    title: str          # surface タイトル（claude が付けるセッショントピック）
    cwd: str | None     # セッション内プロセスの作業ディレクトリ（取れなければ None）


class CmuxClient:
    def __init__(self, cmux_bin: str, runner=None) -> None:
        """runner: (argv: list[str], timeout: int) -> CompletedProcess。テストで差し替える。"""
        self.bin = cmux_bin
        self._runner = runner or self._default_runner

    @staticmethod
    def _default_runner(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)

    def _run(self, *args: str, timeout: int = 20) -> subprocess.CompletedProcess:
        return self._runner([self.bin, *args], timeout)

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

    def surface_exists(self, surface: str) -> bool:
        """surface が存在するか（全ウィンドウ・全ペイン横断で確認する）。"""
        try:
            for ws_ref, _ in self._list_workspaces():
                if any(ref == surface for ref, _t in self._workspace_surfaces(ws_ref)):
                    return True
            return False
        except Exception:
            return False

    # ── セッション列挙（Slack 指示との照合用） ─────────
    @staticmethod
    def _parse_list(out: str) -> list[tuple[str, str]]:
        """`workspace list` / `list-pane-surfaces` の出力を (ref, 名前) に分解。"""
        items: list[tuple[str, str]] = []
        for line in (out or "").splitlines():
            m = _LIST_LINE_RE.match(line)
            if m:
                items.append((m.group(1), m.group(2).strip()))
        return items

    def _windows(self) -> list[str]:
        """全ウィンドウの UUID を列挙する（行頭の UUID がウィンドウ）。"""
        out = self._run("list-windows").stdout or ""
        wins: list[str] = []
        for line in out.splitlines():
            m = _WINDOW_UUID_RE.search(line)
            if m:
                wins.append(m.group(0))
        return wins

    def _list_workspaces(self) -> list[tuple[str, str]]:
        """全ウィンドウの workspace (ref, 名前) を列挙する。
        `workspace list` は1ウィンドウ分しか返さないため、ウィンドウごとに引く。"""
        items: list[tuple[str, str]] = []
        for win in self._windows() or [None]:
            args = ["workspace", "list"] + (["--window", win] if win else [])
            items.extend(self._parse_list(self._run(*args).stdout))
        return items

    def _workspace_surfaces(self, ws_ref: str) -> list[tuple[str, str]]:
        """workspace 内の surface (ref, タイトル) を全ペイン横断で列挙する。
        `list-pane-surfaces` は1ペイン分しか返さないため、ペインごとに引く。"""
        panes = _PANE_RE.findall(self._run("list-panes", "--workspace", ws_ref).stdout or "")
        items: list[tuple[str, str]] = []
        for pane in panes or [None]:
            args = ["list-pane-surfaces", "--workspace", ws_ref] + (
                ["--pane", pane] if pane else [])
            items.extend(self._parse_list(self._run(*args).stdout))
        return items

    def list_sessions(self) -> list[Session]:
        """全ウィンドウ・全 workspace の端末セッションを列挙する（cwd は best effort）。"""
        sessions: list[Session] = []
        workspaces = self._list_workspaces()
        pids = self._surface_pids()
        for ws_ref, ws_name in workspaces:
            for ref, title in self._workspace_surfaces(ws_ref):
                if not ref.startswith("surface:"):
                    continue
                sessions.append(Session(
                    surface=ref, workspace=ws_ref, name=ws_name, title=title,
                    cwd=self._pids_cwd(pids.get(ref, []))))
        return sessions

    def _surface_pids(self) -> dict[str, list[tuple[str, str]]]:
        """surface ref → 直下のプロセス [(pid, プロセス名)…]（`top` の TSV から）。"""
        out: dict[str, list[tuple[str, str]]] = {}
        try:
            tsv = self._run("top", "--all", "--processes", "--format", "tsv").stdout or ""
        except Exception:
            return out
        for line in tsv.splitlines():
            cols = line.split("\t")
            # 列: cpu, mem, count, 種別, id, 親, 名前
            if len(cols) >= 7 and cols[3] == "process" and cols[5].startswith("surface:"):
                out.setdefault(cols[5], []).append((cols[4], cols[6]))
        return out

    @staticmethod
    def _pids_cwd(procs: list[tuple[str, str]]) -> str | None:
        """プロセスの cwd を lsof で引く（シェルより claude 等の実作業プロセスを優先）。"""
        ordered = sorted(procs, key=lambda p: p[1].lower() in _SHELLS)
        for pid, _name in ordered:
            try:
                out = subprocess.run(
                    ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                    capture_output=True, text=True, timeout=5).stdout
            except Exception:
                continue
            for ln in (out or "").splitlines():
                if ln.startswith("n/"):
                    return ln[1:]
        return None

    # ── タスク用セッションの作成 ────────────────────────
    # agent-session(React UI) は new-surface しても claude プロセスが起動せず prompt が
    # 届かないため、「ターミナルで対話 claude を起動」し send+Enter で注入する（検証済み）。
    def _wait_ready(self, surface: str, timeout: int = 40) -> bool:
        """claude の対話UIが立ち上がるまで待つ（入力欄プロンプトの出現を検出）。"""
        for _ in range(timeout):
            screen = self._run("read-screen", "--surface", surface, "--lines", "40").stdout
            if "accept edits" in screen or "shift+tab to cycle" in screen or "❯" in screen:
                return True
            time.sleep(1)
        return False

    def new_session(self, name: str, cwd: str, claude_bin: str,
                    model: str, permission_mode: str) -> str:
        """名前付き workspace タブを作り、対話 claude を起動して surface ref を返す。"""
        cmd = f"{claude_bin} --permission-mode {permission_mode} --model {model}"
        res = self._run(
            "workspace", "create", "--name", name,
            "--cwd", cwd, "--command", cmd, "--focus", "false",
        )
        m = _WORKSPACE_RE.search(res.stdout or "")
        if not m:
            raise RuntimeError(f"workspace 作成失敗: {res.stdout!r} / {res.stderr!r}")
        ws = m.group(0)
        surfaces = self._run("list-pane-surfaces", "--workspace", ws).stdout
        sm = _SURFACE_RE.search(surfaces or "")
        if not sm:
            raise RuntimeError(f"端末サーフェスが見つかりません: {surfaces!r}")
        surface = sm.group(0)
        self._wait_ready(surface)  # claude 起動待ち（初回注入の取りこぼし防止）
        return surface

    def surface_names(self) -> dict[str, str]:
        """surface ref -> タブ（workspace）名。全ウィンドウ・全ペイン横断（cwd 解決なしの軽量版）。"""
        out: dict[str, str] = {}
        for ws_ref, ws_name in self._list_workspaces():
            for ref, _title in self._workspace_surfaces(ws_ref):
                if ref.startswith("surface:"):
                    out[ref] = ws_name
        return out

    # 生死判定でシェルと同様に無視する補助プロセス
    _IDLE_PROCS = {"sleep"}

    def surface_has_process(self, surface: str) -> bool:
        """surface 直下にシェル以外の作業プロセスがいるか（claude の生死判定）。

        claude はプロセス名がバージョン番号（例 "2.1.224"）で見えるため、
        名前で claude を探してはいけない。「シェル（と sleep 等の補助）以外の
        何かが動いているか」で判定する。
        """
        try:
            procs = self._surface_pids().get(surface, [])
        except Exception:
            return True  # 取得失敗時は「生きている」扱い（誤って追跡を切らない）
        if not procs:
            return True  # 情報なしも生存扱い（誤検知で追跡を切るほうが害が大きい）
        ignore = _SHELLS | self._IDLE_PROCS
        return any(name.lower() not in ignore for _pid, name in procs)

    def revive_session(self, surface: str, claude_bin: str,
                       model: str, permission_mode: str) -> bool:
        """claude が終了したタブのシェルで `claude --continue` を実行し、
        同じディレクトリの直前の会話を引き継いで復活させる。"""
        cmd = f"{claude_bin} --continue --permission-mode {permission_mode} --model {model}"
        self.send(surface, cmd)
        return self._wait_ready(surface)

    # ── セッション操作 ──────────────────────────────────
    def send(self, surface: str, text: str) -> None:
        """プロンプト投入（端末 send + Enter）。"""
        r = self._run("send", "--surface", surface, text)
        if r.returncode != 0:
            raise RuntimeError(f"cmux send 失敗: {r.stderr.strip() or r.stdout.strip()}")
        time.sleep(0.3)
        self._run("send-key", "--surface", surface, "enter")

    def interrupt(self, surface: str) -> None:
        """実行中の claude に ESC を送って割り込む。"""
        self._run("send-key", "--surface", surface, "escape")

    def read_screen(self, surface: str, lines: int = 200) -> str:
        """端末サーフェスの可視出力を読む（進捗の AI 解釈の入力）。"""
        try:
            return self._run("read-screen", "--surface", surface, "--lines", str(lines)).stdout or ""
        except Exception:
            return ""
