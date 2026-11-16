"""用例层：API 与 CLI 共用的一套动作。

两个交付形态（HTTP、终端）不应该各自实现一遍「建任务 / 应用答案 / 补渲染 / 清理」，
否则行为迟早分叉。这里只依赖领域模块，不认识 FastAPI、也不 print。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..config import settings
from ..contracts.models import Report, TranscriptBundle
from ..conventions.jsonio import read_json
from ..handoff import ANSWER, BRIEF, apply_answer, read_answer
from ..ingest.normalize import normalize_input
from ..jobs.artifacts import artifacts_present, load_artifacts
from ..jobs.db import Store
from ..jobs.layout import job_dir, job_path
from ..jobs.models import Job
from ..jobs.stages import read_stages
from ..jobs.view import job_view
from ..render.html import render_html


def create_job(store: Store, raw_input: str) -> Job:
    """从用户粘贴的内容建任务；kind 由「是不是已存在的本地文件」决定。"""
    clean = normalize_input(raw_input)
    return store.create_job(clean, "file" if Path(clean).exists() else "url")


def create_job_for_file(store: Store, path: Path) -> Job:
    return store.create_job(str(path), "file")


def job_detail(store: Store, job_id: str) -> dict | None:
    job = store.get(job_id)
    if job is None:
        return None
    data = job_view(job)
    data["stages"] = [s for s in _stages(job_id)]
    data["artifacts"] = artifacts_present(job_id)
    return data


def artifacts(job_id: str) -> dict[str, Any]:
    return load_artifacts(job_id)


def rebuild_html(job_id: str) -> Path | None:
    """老任务没有 report.html 时按需补一份，不动已有的 report.md。"""
    bundle_path = job_path(job_id, "transcript.json")
    report_path = job_path(job_id, "report.json")
    if not (bundle_path.exists() and report_path.exists()):
        return None
    bundle = TranscriptBundle.model_validate(read_json(bundle_path))
    report = Report.model_validate(read_json(report_path))
    usage_path = job_path(job_id, "usage.json")
    usage = read_json(usage_path) if usage_path.exists() else {}
    extra = {"llm_stats": usage.get("llm") or {}, "mode": usage.get("mode") or ""}
    target = job_path(job_id, "report.html")
    target.write_text(render_html(bundle, report, extra), encoding="utf-8")
    return target


def apply_external(store: Store, job_id: str, payload: dict | None = None, path: Path | None = None) -> Report:
    """收外部 agent 的答案：请求体优先，其次读 answer.json（默认在任务目录）。"""
    answer = payload or read_answer(job_id, path)
    return apply_answer(job_id, answer, store)


def _stages(job_id: str) -> list[dict]:
    return [s for s in read_stages(job_id) if s.get("stage") != "error"]


def brief_text(job_id: str) -> str:
    path = job_path(job_id, BRIEF)
    if not path.exists():
        raise FileNotFoundError(f"这个任务没有交接包：{path.parent}")
    return path.read_text(encoding="utf-8")


def answer_path(job_id: str) -> Path:
    return job_path(job_id, ANSWER)


def failed_jobs(store: Store, limit: int = 500) -> list[Job]:
    return [job for job in store.list(limit) if job.status == "failed"]


def prune(store: Store, tokens: list[str]) -> list[tuple[Job, Path | None]]:
    """删任务：记录移除，目录整体移进废纸篓（外置盘上不做真删除）。"""
    removed: list[tuple[Job, Path | None]] = []
    seen: set[str] = set()
    for token in tokens:
        job = store.resolve(token)
        if job is None or job.id in seen:
            continue
        seen.add(job.id)
        directory = job_dir(job.id)
        moved = _trash(directory) if directory.exists() else None
        store.delete(job.id)
        removed.append((job, moved))
    return removed


def _trash(path: Path) -> Path:
    trash = Path.home() / ".Trash"
    trash.mkdir(parents=True, exist_ok=True)
    target = trash / path.name
    n = 1
    while target.exists():
        target = trash / f"{path.name}-{n}"
        n += 1
    shutil.move(str(path), str(target))
    return target


def index_path() -> Path:
    return settings.jobs_dir / "INDEX.md"
