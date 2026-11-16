"""data/jobs/INDEX.md：不用开数据库、不用记 id 也能一眼找到某条内容。

索引是「文档」，不是代码：版式在 resources/templates/index.md，这里只负责填行。
"""

from __future__ import annotations

from pathlib import Path

from ..config import settings
from ..conventions.jsonio import read_json
from ..conventions.resources import text as resource_text
from .layout import job_dir
from .models import Job


def _state(job: Job) -> str:
    if job.status == "failed":
        return "失败"
    if job.degraded:
        return "待外部理解"
    return "完成" if job.status == "done" else job.stage


def _tags(job: Job) -> str:
    report_file = job_dir(job.id) / "report.json"
    if not report_file.exists():
        return ""
    try:
        tags = (read_json(report_file).get("tags") or [])[:5]
    except (ValueError, OSError):
        return ""
    return " ".join(f"#{str(t).replace(' ', '-')}" for t in tags)


def _clip(text: str, limit: int) -> str:
    return text[: limit - 1] + "…" if len(text) > limit else text


def _row(job: Job) -> str:
    title = _clip((job.title or job.input).replace("|", "／"), 46)
    source = _clip(job.input.replace("|", "／"), 60)
    return (
        f"| `{job_dir(job.id).name}` | {title} | {job.platform or '-'} | {_state(job)} "
        f"| {job.chars or '-'} | {_tags(job) or '-'} | {source} |"
    )


def refresh_index(jobs: list[Job] | None = None, limit: int = 300) -> Path:
    if jobs is None:
        from .db import Store

        jobs = Store().list(limit)
    path = settings.jobs_dir / "INDEX.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = resource_text("templates/index.md").format(
        count=len(jobs), rows="\n".join(_row(job) for job in jobs)
    )
    path.write_text(body, encoding="utf-8")
    return path
