"""对外视图：任务怎么呈现给 API / CLI。放这里，避免 Job 自己知道目录名与阶段标签。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import Job
from .stages import STAGES

if TYPE_CHECKING:
    from .db import Store


def job_view(store: "Store", job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "dir": store.job_dir(job.id).name,
        "input": job.input,
        "input_kind": job.input_kind,
        "status": job.status,
        "stage": job.stage,
        "stage_label": dict(STAGES).get(job.stage, job.stage),
        "progress": round(job.progress, 3),
        "title": job.title,
        "platform": job.platform,
        "duration_sec": job.duration_sec,
        "chars": job.chars,
        "llm_model": job.llm_model,
        "degraded": job.degraded,
        "error": job.error,
        "cost_usd": job.cost_usd,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
