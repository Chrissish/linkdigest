"""任务实体：状态库一行就是一条任务。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(slots=True)
class Job:
    id: str
    input: str
    input_kind: str
    status: str
    stage: str
    progress: float
    title: str | None
    platform: str | None
    duration_sec: float | None
    chars: int | None
    llm_model: str | None
    degraded: bool
    error: str | None
    cost_usd: float | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        data = dict(row)
        data["degraded"] = bool(data.get("degraded"))
        return cls(**data)
