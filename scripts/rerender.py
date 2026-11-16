#!/usr/bin/env python3
"""把已有任务的产物对齐到当前代码：重导作业单、重渲染报告、补齐 answer.json。

用途是版本演进后的回溯：改了 BRIEF 模板、改了渲染顺序、或者早期通过
API body 直接应用（答案没落盘）的任务，都能在这里一次拉齐，不用重跑管道。

    python scripts/rerender.py               # 预览要做的事
    python scripts/rerender.py --write       # 真执行
    python scripts/rerender.py --write 412b  # 只处理某几个（前缀即可）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from linkdigest.config import load  # noqa: E402
from linkdigest.contracts.models import Report, TranscriptBundle  # noqa: E402
from linkdigest.conventions.jsonio import read_json, write_json  # noqa: E402
from linkdigest.handoff import ANSWER, BRIEF, export_handoff  # noqa: E402
from linkdigest.jobs.db import Store  # noqa: E402
from linkdigest.jobs.index import refresh_index  # noqa: E402
from linkdigest.render.save import save_report  # noqa: E402


def _answer_from_report(report: Report) -> dict:
    """从 report.json 反推 answer.json，让外部产出可追溯、可重放。"""
    payload = json.loads(report.model_dump_json())
    for key in ("degraded", "notes"):
        payload.pop(key, None)
    return payload


def _brief_stale(brief_path: Path) -> bool:
    if not brief_path.exists():
        return True
    text = brief_path.read_text(encoding="utf-8")
    return "### 3.1 必填" not in text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jobs", nargs="*", help="任务 id 前缀 / 目录名片段；留空表示全部")
    parser.add_argument("--write", action="store_true", help="真正写盘")
    parser.add_argument("--force", action="store_true", help="无视版本标记，强制重导作业单")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    store = Store(load())
    jobs = store.list(args.limit)
    if args.jobs:
        picked = []
        for token in args.jobs:
            job = store.resolve(token)
            if job is None:
                print(f"! 找不到任务 {token}", file=sys.stderr)
                return 1
            picked.append(job)
        jobs = picked

    touched = 0
    for job in jobs:
        directory = store.job_dir(job.id)
        bundle_path = store.job_path(job.id, "transcript.json")
        if not bundle_path.exists():
            continue
        bundle = TranscriptBundle.model_validate(read_json(bundle_path))

        todo: list[str] = []

        # 1. 作业单模板升级（已完成的任务也拉齐：它们是回归基线，不能留旧口径）
        if args.force or _brief_stale(store.job_path(job.id, BRIEF)):
            todo.append("重导作业单")

        # 2. 答案落盘（早期走 API body 应用的任务没有 answer.json）
        answer_path = store.job_path(job.id, ANSWER)
        report_path = store.job_path(job.id, "report.json")
        if report_path.exists() and not answer_path.exists() and not job.degraded:
            todo.append("补 answer.json")

        # 3. 报告重渲染（渲染顺序在版本间变过；html 是新加的形态）
        if report_path.exists():
            todo.append("重渲染 report.md + report.html")

        if not todo:
            continue

        print(f"· {directory.name}")
        for item in todo:
            print(f"    - {item}")
        touched += 1
        if not args.write:
            continue

        if args.force or _brief_stale(store.job_path(job.id, BRIEF)):
            export_handoff(store, job.id, bundle)

        report = Report.model_validate(read_json(report_path)) if report_path.exists() else None

        if report and not answer_path.exists() and not job.degraded:
            write_json(answer_path, _answer_from_report(report))

        if report:
            mode = "external" if "external" in (job.llm_model or "") else "local"
            usage_path = store.job_path(job.id, "usage.json")
            usage = read_json(usage_path) if usage_path.exists() else {}
            save_report(store, job.id, bundle, report,
                        extra={"mode": mode, "llm_stats": usage.get("llm") or {},
                               "job_id": job.id, "created_at": job.created_at})

    if args.write:
        refresh_index(store)
        print(f"\n已处理 {touched} 个任务，索引已刷新。", file=sys.stderr)
    else:
        print(f"\n预览：{touched} 个任务待处理。加 --write 执行。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
