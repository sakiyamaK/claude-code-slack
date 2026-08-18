"""共有フィクスチャ。Config は load() を通さず直接組み立てる（純粋データ）。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from relay.config import Config
from relay.cmux import Session
from relay.links import ThreadLinks
from relay.registry import Database, TaskStore, SettingsStore
from relay.settings import RuntimeSettings


@pytest.fixture
def cfg(tmp_path) -> Config:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()
    return Config(
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        allowed_user_ids={"U1"},
        allowed_channel_ids=set(),
        allow_bypass=False,
        repos={"ios": str(repo_a), "docs": str(repo_b)},
        repo_descriptions={"ios": "アプリ本体", "docs": "ドキュメント"},
        default_repo="ios",
        target_repo=str(repo_a),
        workspace_dir=str(tmp_path),
        default_model="fable",
        match_model="sonnet",
        task_permission_mode="acceptEdits",
        claude_bin="claude",
        cmux_bin="/nonexistent/cmux",
        registry_db=str(tmp_path / "registry.sqlite3"),
        commands={},
        version_source="echo v1.2.3",
        cmux_prompt="{task}",
    )


@pytest.fixture
def db(cfg) -> Database:
    return Database(cfg.registry_db)


@pytest.fixture
def task_store(db) -> TaskStore:
    return TaskStore(db)


@pytest.fixture
def settings_store(db) -> SettingsStore:
    return SettingsStore(db)


@pytest.fixture
def links(settings_store) -> ThreadLinks:
    return ThreadLinks(settings_store)


@pytest.fixture
def runtime(settings_store, cfg) -> RuntimeSettings:
    return RuntimeSettings(settings_store, cfg.default_model, cfg.task_permission_mode)


@pytest.fixture
def posts() -> list[tuple[str, str, str]]:
    return []


@pytest.fixture
def poster(posts):
    def _post(channel, thread_ts, text):
        posts.append((channel, thread_ts, text))
    return _post


class FakeCmux:
    """CmuxClient の見た目互換の偽物。呼び出しを記録する。"""

    def __init__(self, sessions: list[Session] | None = None):
        self.sessions = sessions or []
        self.sent: list[tuple[str, str]] = []          # (surface, text)
        self.created: list[tuple[str, str]] = []       # (name, cwd)
        self.interrupted: list[str] = []
        self.screens: dict[str, str] = {}              # surface -> 画面内容
        self.running = True
        self.dead_claude: set[str] = set()             # claude が終了している surface
        self.revive_ok = True                          # revive_session が成功するか
        self.revived: list[str] = []
        self.own: str | None = None                    # relay 自身のタブ（照合の候補外）
        self._next_surface = 100

    def ensure_running(self):
        pass

    def is_running(self):
        return self.running

    def list_sessions(self, with_screens=False):
        if not with_screens:
            return list(self.sessions)
        # screens に登録があればそれを、無ければ Session 自身が持つ画面を使う
        return [replace(s, screen=self.screens.get(s.surface, s.screen))
                for s in self.sessions]

    def surface_exists(self, surface):
        return any(s.surface == surface for s in self.sessions)

    def own_surface(self):
        return self.own

    def workspace_session(self, workspace):
        for s in self.sessions:
            if workspace in (s.workspace_id, s.workspace):
                return s
        return None

    def find_surface(self, surface_id):
        return None

    def workspace_surfaces(self, workspace):
        return [s.surface for s in self.sessions
                if workspace in (s.workspace_id, s.workspace)]

    def new_session(self, name, cwd, claude_bin, model, permission_mode):
        self._next_surface += 1
        ref = f"surface:{self._next_surface}"
        self.created.append((name, cwd))
        self.sessions.append(Session(ref, f"workspace:{self._next_surface}", name, name, cwd))
        return ref

    def send(self, surface, text):
        self.sent.append((surface, text))

    def interrupt(self, surface):
        self.interrupted.append(surface)

    def read_screen(self, surface, lines=200):
        return self.screens.get(surface, "")

    def surface_names(self):
        return {s.surface: s.name for s in self.sessions}

    def surface_has_process(self, surface):
        return surface not in self.dead_claude

    def revive_session(self, surface, claude_bin, model, permission_mode):
        self.revived.append(surface)
        if self.revive_ok:
            self.dead_claude.discard(surface)
            return True
        return False


@pytest.fixture
def fake_cmux() -> FakeCmux:
    return FakeCmux()


def make_session(surface="surface:10", name="A機能の実装", title="A機能の実装中",
                 cwd="/tmp", workspace="workspace:9", workspace_id="",
                 screen="") -> Session:
    return Session(surface=surface, workspace=workspace, name=name, title=title,
                   cwd=cwd, workspace_id=workspace_id, screen=screen)
