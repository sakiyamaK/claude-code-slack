"""スレッド ⇔ セッションの紐付け管理。

thread_ts をキーに surface（cmux セッション）・workspace UUID・作業先リポジトリ・
依頼内容を SettingsStore へ永続化する。キーの書式はこのモジュールだけが知る。
"""
from __future__ import annotations

from .registry import SettingsStore

_SURFACE = "surface:"
_REPO = "repo:"
_INSTR = "instr:"
_TAB = "tab:"
_WS = "ws:"


class ThreadLinks:
    def __init__(self, settings: SettingsStore) -> None:
        self._s = settings

    # ── surface ─────────────────────────────────────────
    def surface_of(self, thread_ts: str) -> str | None:
        return self._s.get(_SURFACE + thread_ts)

    def link_surface(self, thread_ts: str, surface: str) -> None:
        self._s.set(_SURFACE + thread_ts, surface)

    def unlink_surface(self, thread_ts: str) -> None:
        """紐付けを外す（surface は再び照合候補に戻る）。"""
        self._s.delete(_SURFACE + thread_ts)
        self._s.delete(_TAB + thread_ts)
        self._s.delete(_WS + thread_ts)

    def threads_for_surface(self, surface: str, exclude: str | None = None) -> list[str]:
        """その surface に紐付いているスレッド（1セッション=1窓口を保つのに使う）。"""
        return [key[len(_SURFACE):]
                for key, ref in self._s.with_prefix(_SURFACE).items()
                if ref == surface and key[len(_SURFACE):] != (exclude or "")]

    def linked_surfaces(self, exclude: str | None = None) -> set[str]:
        """いずれかのスレッドに紐付き済みの surface（照合候補から除外する）。

        done 済みスレッドの紐付けも残す＝surface の窓口スレッドは最初の1つに固定
        （1スレッド=1surface）。続きはその元スレッドに書けば再開できる。
        """
        out: set[str] = set()
        for key, surface in self._s.with_prefix(_SURFACE).items():
            if surface and key != _SURFACE + (exclude or ""):
                out.add(surface)
        return out

    # ── タブ名（cmux 再起動で surface ref が変わったときの再発見に使う） ──
    def tab_name_of(self, thread_ts: str) -> str | None:
        return self._s.get(_TAB + thread_ts)

    def set_tab_name(self, thread_ts: str, name: str) -> None:
        self._s.set(_TAB + thread_ts, name)

    # ── workspace UUID（ID 指名で合流したスレッドの再解決に使う。UUID は不変） ──
    def workspace_of(self, thread_ts: str) -> str | None:
        return self._s.get(_WS + thread_ts)

    def set_workspace(self, thread_ts: str, workspace_id: str) -> None:
        self._s.set(_WS + thread_ts, workspace_id)

    # ── 作業先リポジトリ ─────────────────────────────────
    def repo_of(self, thread_ts: str) -> str | None:
        return self._s.get(_REPO + thread_ts)

    def set_repo(self, thread_ts: str, repo_name: str) -> None:
        self._s.set(_REPO + thread_ts, repo_name)

    # ── 依頼内容 ─────────────────────────────────────────
    def instruction_of(self, thread_ts: str) -> str:
        return (self._s.get(_INSTR + thread_ts, "") or "").strip()

    def set_instruction(self, thread_ts: str, body: str) -> None:
        self._s.set(_INSTR + thread_ts, body)
