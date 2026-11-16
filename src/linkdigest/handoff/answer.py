"""收外部 agent 的答案：读、校验、渲染成正式报告、把任务标成完成。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..contracts.models import Report, TranscriptBundle
from ..conventions.jsonio import read_json, write_json
from ..jobs.db import Store
from ..jobs.finalize import finalize
from ..jobs.layout import job_path
from ..render.save import save_report
from ..understand.payload import report_from_payload
from .paths import ANSWER, BRIEF


def read_answer(job_id: str, path: Path | None = None) -> dict[str, Any]:
    target = path or job_path(job_id, ANSWER)
    if not target.exists():
        raise FileNotFoundError(
            f"没有找到 {target.name}。先让 agent 按 {BRIEF} 产出答案，"
            f"或把已有答案放到 {target.parent.name}/{target.name}。"
        )
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("answer.json 顶层必须是对象")
    return payload


def apply_answer(
    job_id: str, payload: dict[str, Any], store: Store | None = None, source: str = "external-agent"
) -> Report:
    store = store or Store()
    job = store.get(job_id)
    if job is None:
        raise KeyError(f"job {job_id} 不存在")
    bundle_path = job_path(job_id, "transcript.json")
    if not bundle_path.exists():
        raise FileNotFoundError("这个任务还没有 transcript.json，先跑完确定性层")
    bundle = TranscriptBundle.model_validate(read_json(bundle_path))

    report = report_from_payload(payload)
    if not report.one_liner and not report.body:
        raise ValueError(
            "答案太空：`one_liner`（一句话）和 `body`（正文）至少要给一个，"
            "否则报告顶部没有任何可读内容。字段名是否与 BRIEF.md 第 3.1 节一致？"
        )
    report.degraded = False
    report.notes = [f"理解层由外部 agent 完成（{source}），本机未调用模型。"] + [
        n for n in report.notes if "降级" not in n and "LLM_API_KEY" not in n
    ]
    if not report.sections:
        report.notes.append("未提供 sections，报告没有时间码锚点，无法逐节回查原片。")

    write_json(job_path(job_id, "report.json"), json.loads(report.model_dump_json()))
    save_report(
        job_id,
        bundle,
        report,
        extra={"mode": "external", "llm_stats": {}, "job_id": job.id, "created_at": job.created_at},
    )

    usage_path = job_path(job_id, "usage.json")
    usage = read_json(usage_path) if usage_path.exists() else {}
    usage.update(
        {
            "mode": "external",
            "external": {"by": source, "at": datetime.now().astimezone().isoformat(timespec="seconds")},
        }
    )
    write_json(usage_path, usage)

    store.update(job_id, degraded=0, llm_model=source, status="done", stage="done", progress=1.0)
    finalize(store, job_id)
    return report
