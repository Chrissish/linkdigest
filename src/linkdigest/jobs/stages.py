"""阶段日志：append-only 的 stages.jsonl。

不写数据库：阶段记录是给人看的排查材料，跟着任务目录走才不会丢。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .layout import job_path

STAGES = [
    ("queued", "排队"),
    ("ingest", "解析链接"),
    ("download", "取流"),
    ("extract", "抽取音轨"),
    ("asr", "语音转写"),
    ("normalize", "校验归一"),
    ("bundle", "落盘契约"),
    ("understand", "分层理解"),
    ("report", "生成报告"),
    ("handoff", "导出交接"),
    ("done", "完成"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_stage(job_id: str, record: dict[str, Any]) -> None:
    path = job_path(job_id, "stages.jsonl")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now(), **record}, ensure_ascii=False) + "\n")


def read_stages(job_id: str) -> list[dict[str, Any]]:
    path: Path = job_path(job_id, "stages.jsonl")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
