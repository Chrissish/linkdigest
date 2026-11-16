"""任务收尾：目录改成可读名、写身份证、刷新索引。

改名发生在收尾而不是创建时，是因为标题要等采集完才知道。
幂等：重复调用只会得到同一个目标名；改名失败时退回旧目录，不影响产物可读性。

身份证里带一份**配置快照**（密钥已打码）：事后想知道「这份报告当时用什么参数跑的」，
看 manifest.json 就够了，不用翻环境变量。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from ..conventions.jsonio import read_json, write_json
from .index import refresh_index
from .stages import append_stage

if TYPE_CHECKING:
    from .db import Store


def finalize(store: "Store", job_id: str) -> Path:
    job = store.get(job_id)
    if job is None:
        return store.jobs_dir / job_id

    current = store.job_dir(job_id)
    target = store.jobs_dir / store.dir_name(job)
    if current.resolve() != target.resolve():
        if target.exists():
            target = target.with_name(f"{target.name}-{uuid.uuid4().hex[:4]}")
        try:
            if current.exists():
                current.rename(target)
            else:
                target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # 磁盘/权限问题不该让任务失败
            append_stage(store, job_id, {"stage": "finalize", "error": f"改名失败：{exc}"})
            target = current
            target.mkdir(parents=True, exist_ok=True)

    source_file = target / "source.json"
    config_file = target / "config.json"
    write_json(
        target / "manifest.json",
        {
            "id": job.id,
            "dir": target.name,
            "input": job.input,
            "title": job.title,
            "platform": job.platform,
            "url": (
                read_json(source_file).get("source", {}).get("canonical_url")
                if source_file.exists()
                else job.input
            ),
            "status": job.status,
            "chars": job.chars,
            "duration_sec": job.duration_sec,
            "llm_model": job.llm_model,
            "degraded": job.degraded,
            "config": read_json(config_file) if config_file.exists() else None,
            "created_at": job.created_at,
            "artifacts": sorted(p.name for p in target.iterdir() if p.is_file()),
        },
    )
    refresh_index(store, store.list(300))
    return target
