"""設定読み込み（SPEC §18）。

config.yml を主とし、環境変数があれば上書きできる。
横展開のため絶対パスのハードコードは禁止。既定値は target_repo から相対導出する。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

_DEFAULT_CMUX = "/Applications/cmux.app/Contents/Resources/bin/cmux"
_CONFIG_CANDIDATES = ["config.yml", "config.yaml"]



def _load_yaml() -> dict:
    path = os.environ.get("RELAY_CONFIG")
    candidates = [path] if path else _CONFIG_CANDIDATES
    for c in candidates:
        if c and os.path.exists(c):
            with open(c, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    return {}


def _get(y: dict, *path, default=None):
    """ネストしたキーを辿る。環境変数（大文字_連結）があれば最優先で上書き。"""
    env_key = "_".join(p.upper() for p in path)
    if env_key in os.environ:
        return os.environ[env_key]
    cur = y
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur if cur is not None else default


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return [str(s).strip() for s in v if str(s).strip()]


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    slack_bot_token: str
    slack_app_token: str
    allowed_user_ids: set[str]
    allowed_channel_ids: set[str]
    task_keywords: list[str]
    target_repo: str
    worktree_parent: str
    workspace_dir: str
    max_concurrent: int
    default_model: str
    default_permission_mode: str
    manager_permission_mode: str
    allow_bypass: bool
    claude_bin: str
    cmux_bin: str
    registry_db: str
    commands: dict  # ユーザー定義コマンド: name -> {prompt, version?, confirm?}
    version_source: str  # 現在バージョンを得るシェルコマンド（出力から X.Y.Z を拾う）
    task_backend: str          # "solo" or backends のキー
    backends: dict             # name -> {start: [...], watch: "<path>"}
    solo_worktree: bool        # solo でタスクごとに worktree を切るか
    solo_base: str             # solo worktree のベース ref（空=対象repoの現在ブランチ）

    @property
    def is_solo(self) -> bool:
        return self.task_backend == "solo"

    @property
    def backend(self) -> dict:
        return self.backends.get(self.task_backend, {})

    @property
    def watch_path(self) -> str:
        """外部 backend の進捗監視ファイル（絶対パス）。無ければ空。"""
        p = self.backend.get("watch")
        return os.path.join(self.target_repo, p) if p else ""

    @staticmethod
    def load() -> "Config":
        y = _load_yaml()
        bot = str(_get(y, "slack", "bot_token", default="")).strip()
        app = str(_get(y, "slack", "app_token", default="")).strip()
        if not bot or not app or bot.startswith("xoxb-...") or app.startswith("xapp-..."):
            raise SystemExit(
                "slack.bot_token / slack.app_token が未設定です。"
                "config.sample.yml を config.yml にコピーして記入してください。"
            )
        target = str(_get(y, "paths", "target_repo", default="")).strip().rstrip("/")
        if not target or target.startswith("<"):
            raise SystemExit("paths.target_repo が未設定です（config.yml を記入してください）。")
        if not os.path.isdir(target):
            raise SystemExit(f"paths.target_repo が存在しません: {target}")

        parent = str(_get(y, "paths", "worktree_parent", default="")).strip().rstrip("/") or os.path.dirname(target)
        workspace = str(_get(y, "paths", "workspace_dir", default="")).strip().rstrip("/") or os.path.dirname(target)
        keywords = _as_list(_get(y, "behavior", "task_keywords", default=None)) or ["作業", "タスク", "task"]

        # タスク背骨: 既定 solo。backend は config の task.backends で各自定義。
        task_cfg = (y.get("task") or {}) if isinstance(y, dict) else {}
        task_backend = os.environ.get("TASK_BACKEND") or task_cfg.get("backend") or "solo"
        backends = {k: dict(v) for k, v in (task_cfg.get("backends") or {}).items()}
        solo_cfg = task_cfg.get("solo") or {}
        solo_worktree = _as_bool(solo_cfg.get("worktree", True))
        solo_base = str(solo_cfg.get("base", "")).strip()

        return Config(
            slack_bot_token=bot,
            slack_app_token=app,
            allowed_user_ids=set(_as_list(_get(y, "security", "allowed_user_ids", default=None))),
            allowed_channel_ids=set(_as_list(_get(y, "security", "allowed_channel_ids", default=None))),
            task_keywords=keywords,
            target_repo=target,
            worktree_parent=parent,
            workspace_dir=workspace,
            max_concurrent=int(_get(y, "behavior", "max_concurrent", default=2)),
            default_model=str(_get(y, "behavior", "default_model", default="opus")).strip(),
            default_permission_mode=str(_get(y, "behavior", "default_permission_mode", default="acceptEdits")).strip(),
            # マネージャー（無人オーケストレーター）の権限。無人運用したいなら config で bypassPermissions を指定
            manager_permission_mode=str(_get(y, "behavior", "manager_permission_mode", default="acceptEdits")).strip(),
            allow_bypass=_as_bool(_get(y, "security", "allow_bypass", default=False)),
            claude_bin=str(_get(y, "bin", "claude", default="claude")).strip(),
            cmux_bin=str(_get(y, "bin", "cmux", default=_DEFAULT_CMUX)).strip(),
            registry_db=str(_get(y, "registry_db", default="./relay_registry.sqlite3")).strip(),
            commands=(y.get("commands") or {}) if isinstance(y, dict) else {},
            version_source=str(_get(y, "version_source",
                                    default="git describe --tags --abbrev=0")).strip(),
            task_backend=task_backend,
            backends=backends,
            solo_worktree=solo_worktree,
            solo_base=solo_base,
        )
