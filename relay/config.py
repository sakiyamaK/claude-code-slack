"""設定読み込み。

config.yml を主とし、環境変数（大文字_連結。例 SLACK_BOT_TOKEN, MODELS_TASK）で上書きできる。
横展開のため絶対パスのハードコードは禁止。既定値は repos から相対導出する。

タスク実行は cmux（1タスク=1セッション）のみ。旧 solo backend と旧キー
（paths.* / task.* / behavior.*）は廃止した。旧 config はエラーで移行案内する。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

_DEFAULT_CMUX = "/Applications/cmux.app/Contents/Resources/bin/cmux"
_CONFIG_CANDIDATES = ["config.yml", "config.yaml"]

# 旧キー → 新キーの対応（検出したらエラーで案内する）
_LEGACY_KEYS = {
    "paths": "repos / default_repo（トップレベル）",
    "task": "prompt（トップレベル）。backend・solo・status_paths は廃止",
    "behavior": "models / permission_mode（トップレベル）。task_keywords は廃止（全DMが指示）",
    "task_keywords": "廃止（`作業:` プレフィックス不要。全DMが指示として扱われる）",
    "status_paths": "廃止（状況はタスクスレッドで直接そのセッションに聞く）",
}


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
    allow_bypass: bool         # /mode で yolo(bypassPermissions) を選べるようにするか
    repos: dict                # 名前 -> パス
    repo_descriptions: dict    # 名前 -> 説明文（AI ルーティングの手掛かり）
    default_repo: str          # repos のうち既定の名前
    target_repo: str           # 既定リポジトリのパス（= repos[default_repo]）
    workspace_dir: str         # 補助的な作業ディレクトリ（= 既定リポジトリの親）
    default_model: str         # タスク実装モデル（/model で変更可・registry に永続化）
    match_model: str           # セッション照合・リポジトリ振り分け用モデル
    task_permission_mode: str  # タスクセッションの権限の初期値（/mode で変更可）
    claude_bin: str
    cmux_bin: str
    registry_db: str
    commands: dict             # ユーザー定義コマンド: name -> {prompt, version?, confirm?}
    version_source: str        # 現在バージョンを得るシェルコマンド（出力から X.Y.Z を拾う）
    cmux_prompt: str           # セッションに注入するテンプレ（{task} {id}）

    @staticmethod
    def load() -> "Config":
        y = _load_yaml()
        # 旧書式の検出（solo backend 時代の paths / task / behavior）
        if isinstance(y, dict):
            legacy = [k for k in _LEGACY_KEYS if k in y]
            if legacy:
                hints = "\n".join(f"  - {k}: → {_LEGACY_KEYS[k]}" for k in legacy)
                raise SystemExit(
                    "config.yml が旧書式です。次のキーを新書式に移行してください"
                    f"（config.sample.yml 参照）:\n{hints}"
                )

        bot = str(_get(y, "slack", "bot_token", default="")).strip()
        app = str(_get(y, "slack", "app_token", default="")).strip()
        if not bot or not app or bot.startswith("xoxb-...") or app.startswith("xapp-..."):
            raise SystemExit(
                "slack.bot_token / slack.app_token が未設定です。"
                "config.sample.yml を config.yml にコピーして記入してください。"
            )

        # リポジトリ（必須・名前付き。単一でも repos に1つ書く）
        repos: dict[str, str] = {}
        descs: dict[str, str] = {}
        repos_cfg = _get(y, "repos", default=None)
        if not isinstance(repos_cfg, dict) or not repos_cfg:
            raise SystemExit("repos が未設定です（config.yml に作業先リポジトリを記入してください）。")
        for rname, v in repos_cfg.items():
            if isinstance(v, dict):
                rpath = str(v.get("path", "")).strip().rstrip("/")
                descs[str(rname)] = str(v.get("description", "")).strip()
            else:
                rpath = str(v).strip().rstrip("/")
                descs[str(rname)] = ""
            if not rpath or rpath.startswith("<"):
                raise SystemExit(f"repos.{rname} の path が未記入です。")
            if not os.path.isdir(rpath):
                raise SystemExit(f"repos.{rname} が存在しません: {rpath!r}")
            repos[str(rname)] = rpath
        default_repo = str(_get(y, "default_repo", default="")).strip() or next(iter(repos))
        if default_repo not in repos:
            raise SystemExit(f"default_repo が repos にありません: {default_repo!r}")
        target = repos[default_repo]

        return Config(
            slack_bot_token=bot,
            slack_app_token=app,
            allowed_user_ids=set(_as_list(_get(y, "security", "allowed_user_ids", default=None))),
            allowed_channel_ids=set(_as_list(_get(y, "security", "allowed_channel_ids", default=None))),
            allow_bypass=_as_bool(_get(y, "security", "allow_bypass", default=False)),
            repos=repos,
            repo_descriptions=descs,
            default_repo=default_repo,
            target_repo=target,
            workspace_dir=os.path.dirname(target),
            default_model=str(_get(y, "models", "task", default="fable")).strip(),
            # 照合ミスは無関係なセッションへの注入事故になるので既定は sonnet
            match_model=str(_get(y, "models", "match", default="sonnet")).strip(),
            task_permission_mode=str(_get(y, "permission_mode", default="acceptEdits")).strip(),
            claude_bin=str(_get(y, "bin", "claude", default="claude")).strip(),
            cmux_bin=str(_get(y, "bin", "cmux", default=_DEFAULT_CMUX)).strip(),
            registry_db=str(_get(y, "registry_db", default="./relay_registry.sqlite3")).strip(),
            commands=(y.get("commands") or {}) if isinstance(y, dict) else {},
            version_source=str(_get(y, "version_source",
                                    default="git describe --tags --abbrev=0")).strip(),
            cmux_prompt=str(_get(y, "prompt", default="{task}")),
        )
