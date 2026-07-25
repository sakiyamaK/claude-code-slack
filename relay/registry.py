"""永続レジストリ（SPEC §20）。再起動から復元できるよう SQLite を使う。

- tasks: thread_ts をキーにタスク状態。branch ↔ thread の対応（完了通知ルーティング）
- settings: モデル/モードのグローバル設定、最後に処理した DM ts（catch-up 用）
"""
from __future__ import annotations

import sqlite3
import threading
import datetime
from dataclasses import dataclass

# モード
MODE_NORMAL = "normal"
MODE_TASK = "task"

# タスクステータス（relay視点）
STATUS_ACTIVE = "active"      # マネージャーに渡して進行中
STATUS_DONE = "done"
STATUS_FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    thread_ts   TEXT PRIMARY KEY,
    channel_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    mode        TEXT NOT NULL,
    branch      TEXT,
    session_id  TEXT,
    anchor_ts   TEXT,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_branch ON tasks(branch);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


@dataclass
class Task:
    thread_ts: str
    channel_id: str
    user_id: str
    mode: str
    branch: str | None
    session_id: str | None
    anchor_ts: str | None
    status: str
    created_at: str
    updated_at: str


class Registry:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _row(self, row: sqlite3.Row) -> Task:
        return Task(**{k: row[k] for k in row.keys()})

    # ── tasks ──────────────────────────────────────────
    def get(self, thread_ts: str) -> Task | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE thread_ts = ?", (thread_ts,)
            ).fetchone()
        return self._row(row) if row else None

    def get_by_branch(self, branch: str) -> Task | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE branch = ? ORDER BY updated_at DESC LIMIT 1",
                (branch,),
            ).fetchone()
        return self._row(row) if row else None

    def create(self, task: Task) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO tasks
                   (thread_ts, channel_id, user_id, mode, branch, session_id,
                    anchor_ts, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (task.thread_ts, task.channel_id, task.user_id, task.mode,
                 task.branch, task.session_id, task.anchor_ts, task.status,
                 task.created_at, task.updated_at),
            )
            self._conn.commit()

    def update(self, thread_ts: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE tasks SET {cols} WHERE thread_ts = ?",
                list(fields.values()) + [thread_ts],
            )
            self._conn.commit()

    def active_tasks(self) -> list[Task]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = ?", (STATUS_ACTIVE,)
            ).fetchall()
        return [self._row(r) for r in rows]

    # ── settings ───────────────────────────────────────
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
            self._conn.commit()
