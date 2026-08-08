"""永続化層（SQLite）。再起動から復元できるようにする。

- Database: 接続・スキーマ・排他ロックの管理
- TaskStore: tasks テーブル（thread_ts をキーにタスク状態）
- SettingsStore: settings テーブル（key-value。紐付けや実行時設定の保存先）
"""
from __future__ import annotations

import sqlite3
import threading
import datetime
from dataclasses import dataclass

# タスクの種別・ステータス（relay 視点）
MODE_TASK = "task"
STATUS_ACTIVE = "active"
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


class Database:
    """SQLite 接続とスキーマの管理。Store 群はこれを共有する。"""

    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(_SCHEMA)
            self.conn.commit()


class TaskStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _row(row: sqlite3.Row) -> Task:
        return Task(**{k: row[k] for k in row.keys()})

    def get(self, thread_ts: str) -> Task | None:
        with self._db.lock:
            row = self._db.conn.execute(
                "SELECT * FROM tasks WHERE thread_ts = ?", (thread_ts,)
            ).fetchone()
        return self._row(row) if row else None

    def get_by_branch(self, branch: str) -> Task | None:
        with self._db.lock:
            row = self._db.conn.execute(
                "SELECT * FROM tasks WHERE branch = ? ORDER BY updated_at DESC LIMIT 1",
                (branch,),
            ).fetchone()
        return self._row(row) if row else None

    def create(self, task: Task) -> None:
        with self._db.lock:
            self._db.conn.execute(
                """INSERT OR REPLACE INTO tasks
                   (thread_ts, channel_id, user_id, mode, branch, session_id,
                    anchor_ts, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (task.thread_ts, task.channel_id, task.user_id, task.mode,
                 task.branch, task.session_id, task.anchor_ts, task.status,
                 task.created_at, task.updated_at),
            )
            self._db.conn.commit()

    def update(self, thread_ts: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._db.lock:
            self._db.conn.execute(
                f"UPDATE tasks SET {cols} WHERE thread_ts = ?",
                list(fields.values()) + [thread_ts],
            )
            self._db.conn.commit()

    def active(self) -> list[Task]:
        with self._db.lock:
            rows = self._db.conn.execute(
                "SELECT * FROM tasks WHERE status = ?", (STATUS_ACTIVE,)
            ).fetchall()
        return [self._row(r) for r in rows]


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str, default: str | None = None) -> str | None:
        with self._db.lock:
            row = self._db.conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        with self._db.lock:
            self._db.conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
            self._db.conn.commit()

    def delete(self, key: str) -> None:
        with self._db.lock:
            self._db.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            self._db.conn.commit()

    def with_prefix(self, prefix: str) -> dict[str, str]:
        with self._db.lock:
            rows = self._db.conn.execute(
                r"SELECT key, value FROM settings WHERE key LIKE ? ESCAPE '\'",
                (prefix.replace("%", r"\%").replace("_", r"\_") + "%",),
            ).fetchall()
        return {r["key"]: r["value"] for r in rows}
