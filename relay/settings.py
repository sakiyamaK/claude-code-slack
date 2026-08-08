"""実行時設定（/model /mode で変わる値）の永続化。

初期値は config から採り、以降は SettingsStore に保存された値が優先される。
Slack の catch-up 用の最終処理 ts もここで扱う。
"""
from __future__ import annotations

from .registry import SettingsStore

_MODEL = "model"
_PERMISSION = "permission_mode"
_LAST_TS = "last_processed_ts"


class RuntimeSettings:
    def __init__(self, store: SettingsStore,
                 default_model: str, default_permission_mode: str) -> None:
        self._s = store
        self._default_model = default_model
        self._default_permission = default_permission_mode
        if self._s.get(_MODEL) is None:
            self._s.set(_MODEL, default_model)
        if self._s.get(_PERMISSION) is None:
            self._s.set(_PERMISSION, default_permission_mode)

    @property
    def model(self) -> str:
        return self._s.get(_MODEL, self._default_model)

    def set_model(self, value: str) -> None:
        self._s.set(_MODEL, value)

    @property
    def permission_mode(self) -> str:
        return self._s.get(_PERMISSION, self._default_permission)

    def set_permission_mode(self, value: str) -> None:
        self._s.set(_PERMISSION, value)

    @property
    def last_processed_ts(self) -> str | None:
        return self._s.get(_LAST_TS)

    def mark_processed(self, ts: str) -> None:
        self._s.set(_LAST_TS, ts)
