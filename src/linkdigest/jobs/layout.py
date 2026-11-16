"""任务产物在磁盘上的布局。

data/jobs/
  INDEX.md                     全部任务的可读索引
  <日期-时间>_<平台>_<标题>_<job_id>/
    manifest.json  身份证（标题 / 平台 / 源链接 / 模式 / 产物清单）
    source.json    采集元数据
    audio.wav      16k mono
    transcript.json 契约
    report.json    结构化报告
    report.md      文本渲染稿
    report.html    单页网页稿
    usage.json     token / 成本 / 阶段耗时
    stages.jsonl   阶段日志
    handoff/       无模型时的交接包
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from ..conventions.naming import slugify, source_hint
from .models import Job


def _stamp(created_at: str | None) -> str:
    try:
        moment = datetime.fromisoformat(created_at) if created_at else datetime.now(timezone.utc)
    except ValueError:
        moment = datetime.now(timezone.utc)
    return moment.astimezone().strftime("%Y%m%d-%H%M")


def job_dir_name(job: Job) -> str:
    """<本地时间>_<平台>_<标题>_<job_id>。job_id 放末尾，既能反查也不会撞名。"""
    title = slugify(job.title, 48) if job.title else (source_hint(job.input) or "untitled")
    platform = slugify(job.platform, 12) if job.platform else "unknown"
    return "_".join([_stamp(job.created_at), platform, title, job.id])


def job_dir(job_id: str) -> Path:
    """按 job_id 找产物目录：先看裸 id（老任务），再按 *_<job_id> 反查。"""
    direct = settings.jobs_dir / job_id
    if direct.exists():
        return direct
    if settings.jobs_dir.exists():
        matches = sorted(settings.jobs_dir.glob(f"*_{job_id}"))
        if matches:
            return matches[0]
    return direct


def job_path(job_id: str, name: str) -> Path:
    return job_dir(job_id) / name
