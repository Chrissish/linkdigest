"""用例层：API 与 CLI 共用的一套动作。

两个交付形态（HTTP、终端）不应该各自实现一遍「建任务 / 应用答案 / 补渲染 / 清理」，
否则行为迟早分叉。这里只依赖领域模块，不认识 FastAPI、也不 print。

配置跟着 `Store` 走（`store.cfg`）：Store 是用某一份配置造出来的，
两者不可能对不上，也就不需要每个函数再多传一个 cfg 参数。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..contracts.models import Report, TranscriptBundle
from ..conventions.jsonio import read_json, write_json
from ..handoff import ANSWER, BRIEF, apply_answer, read_answer
from ..ingest.normalize import normalize_input
from ..jobs.artifacts import artifacts_present, load_artifacts
from ..jobs.db import Store
from ..jobs.models import Job
from ..jobs.stages import read_stages
from ..jobs.view import job_view
from ..render.html import render_html


def create_job(store: Store, raw_input: str) -> Job:
    """从用户粘贴的内容建任务；kind 由「是不是已存在的本地文件」决定。

    建完立刻把配置快照写进任务目录：哪怕后面失败，也能查到当时用的是哪套参数。
    """
    clean = normalize_input(raw_input)
    job = store.create_job(clean, "file" if Path(clean).exists() else "url")
    write_json(store.job_path(job.id, "config.json"), store.cfg.snapshot())
    return job


def create_job_for_file(store: Store, path: Path) -> Job:
    return create_job(store, str(path))


def job_detail(store: Store, job_id: str) -> dict | None:
    job = store.get(job_id)
    if job is None:
        return None
    data = job_view(store, job)
    data["stages"] = _stages(store, job_id)
    data["artifacts"] = artifacts_present(store, job_id)
    return data


def job_config(store: Store, job_id: str) -> dict | None:
    """这个任务当时实际用的配置（密钥已打码）。老任务没有 config.json 时返回 None。"""
    path = store.job_path(job_id, "config.json")
    return read_json(path) if path.exists() else None


def artifacts(store: Store, job_id: str) -> dict[str, Any]:
    return load_artifacts(store, job_id)


def rebuild_html(store: Store, job_id: str) -> Path | None:
    """老任务没有 report.html 时按需补一份，不动已有的 report.md。"""
    bundle_path = store.job_path(job_id, "transcript.json")
    report_path = store.job_path(job_id, "report.json")
    if not (bundle_path.exists() and report_path.exists()):
        return None
    bundle = TranscriptBundle.model_validate(read_json(bundle_path))
    report = Report.model_validate(read_json(report_path))
    usage_path = store.job_path(job_id, "usage.json")
    usage = read_json(usage_path) if usage_path.exists() else {}
    extra = {"llm_stats": usage.get("llm") or {}, "mode": usage.get("mode") or ""}
    target = store.job_path(job_id, "report.html")
    target.write_text(render_html(bundle, report, extra), encoding="utf-8")
    return target


def apply_external(
    store: Store, job_id: str, payload: dict | None = None, path: Path | None = None
) -> Report:
    """收外部 agent 的答案：请求体优先，其次读 answer.json（默认在任务目录）。"""
    answer = payload if payload is not None else read_answer(store, job_id, path)
    return apply_answer(store, job_id, answer)


def _stages(store: Store, job_id: str) -> list[dict]:
    return [s for s in read_stages(store, job_id) if s.get("stage") != "error"]


def brief_text(store: Store, job_id: str) -> str:
    path = store.job_path(job_id, BRIEF)
    if not path.exists():
        raise FileNotFoundError(f"这个任务没有交接包：{path.parent}")
    return path.read_text(encoding="utf-8")


def answer_path(store: Store, job_id: str) -> Path:
    return store.job_path(job_id, ANSWER)


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
        directory = store.job_dir(job.id)
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


def index_path(store: Store) -> Path:
    return store.jobs_dir / "INDEX.md"
