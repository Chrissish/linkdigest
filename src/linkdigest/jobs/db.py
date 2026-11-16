"""SQLite 状态库。只存任务状态，不存产物内容——产物在磁盘上，路径可推导。"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from .layout import job_dir
from .models import Job

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    input        TEXT NOT NULL,
    input_kind   TEXT NOT NULL DEFAULT 'url',
    status       TEXT NOT NULL DEFAULT 'queued',
    stage        TEXT NOT NULL DEFAULT 'queued',
    progress     REAL NOT NULL DEFAULT 0,
    title        TEXT,
    platform     TEXT,
    duration_sec REAL,
    chars        INTEGER,
    llm_model    TEXT,
    degraded     INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    cost_usd     REAL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path | None = None) -> None:
        settings.ensure_dirs()
        self.db_path = db_path or settings.db_path
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def create_job(self, input_value: str, input_kind: str = "url") -> Job:
        job_id = uuid.uuid4().hex[:12]
        stamp = now()
        with _LOCK, self._conn() as conn:
            conn.execute(
                "INSERT INTO jobs (id, input, input_kind, status, stage, progress, created_at, updated_at)"
                " VALUES (?, ?, ?, 'queued', 'queued', 0, ?, ?)",
                (job_id, input_value, input_kind, stamp, stamp),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        job_dir(job_id).mkdir(parents=True, exist_ok=True)
        return Job.from_row(row)

    def update(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now()
        keys = ", ".join(f"{k} = ?" for k in fields)
        with _LOCK, self._conn() as conn:
            conn.execute(f"UPDATE jobs SET {keys} WHERE id = ?", (*fields.values(), job_id))

    def get(self, job_id: str) -> Job | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    def list(self, limit: int = 30) -> list[Job]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [Job.from_row(r) for r in rows]

    def delete(self, job_id: str) -> None:
        """从状态库里移除记录（产物目录由调用方处理）。"""
        with _LOCK, self._conn() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def resolve(self, token: str) -> Job | None:
        """允许用 job_id 前缀、目录名片段或标题片段定位任务。"""
        token = (token or "").strip().strip("`'\"")
        for bad in "*?[]":
            token = token.replace(bad, "")
        if not token:
            return None
        job = self.get(token)
        if job is not None:
            return job
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id LIKE ? OR input LIKE ? ORDER BY created_at DESC LIMIT 1",
                (token + "%", f"%{token}%"),
            ).fetchone()
        if row is not None:
            return Job.from_row(row)
        if settings.jobs_dir.exists():
            for path in sorted(settings.jobs_dir.glob(f"*{token}*"), reverse=True):
                if path.is_dir():
                    found = self.get(path.name.rsplit("_", 1)[-1])
                    if found is not None:
                        return found
        return None
