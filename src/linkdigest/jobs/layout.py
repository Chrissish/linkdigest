"""任务产物在磁盘上的布局。

data/jobs/
  INDEX.md                     全部任务的可读索引
  <日期-时间>_<平台>_<标题>_<job_id>/
    manifest.json  身份证（标题 / 平台 / 源链接 / 模式 / 配置快照 / 产物清单）
    config.json    这个任务实际用的配置（密钥已打码）
    source.json    采集元数据
    audio.wav      16k mono
    transcript.json 契约
    report.json    结构化报告
    report.md      文本渲染稿
    report.html    单页网页稿
    usage.json     token / 成本 / 阶段耗时
    stages.jsonl   阶段日志
    handoff/       无模型时的交接包

函数都要求显式给 `jobs_dir`：布局由配置决定，不从模块级单例里读。
`Store` 上另有同名方法，日常调用走 store.job_dir(job_id) 更省事。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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


def job_dir(jobs_dir: Path, job_id: str) -> Path:
    """按 job_id 找产物目录：先看裸 id（老任务），再按 *_<job_id> 反查。"""
    direct = jobs_dir / job_id
    if direct.exists():
        return direct
    if jobs_dir.exists():
        matches = sorted(jobs_dir.glob(f"*_{job_id}"))
        if matches:
            return matches[0]
    return direct


def job_path(jobs_dir: Path, job_id: str, name: str) -> Path:
    return job_dir(jobs_dir, job_id) / name
