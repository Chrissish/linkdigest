"""把历史任务整理成可读目录，并给降级任务补交接包。

    .venv/bin/python scripts/relabel.py            # 只预览
    .venv/bin/python scripts/relabel.py --write    # 真改

老任务的目录是纯 12 位 hash，标题只在数据库里，目录名看不出内容。这里按当前规则
统一改成「时间_平台_标题_job_id」，顺带给当时没配模型的降级任务补 handoff/，
这样它们也能直接交给外部 agent 补报告。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from linkdigest.config import settings  # noqa: E402
from linkdigest.handoff import BRIEF, export_handoff  # noqa: E402
from linkdigest.models import TranscriptBundle  # noqa: E402
from linkdigest.store import (  # noqa: E402
    Store,
    job_dir,
    job_dir_name,
    job_path,
    read_json,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser("relabel")
    parser.add_argument("--write", action="store_true", help="真正执行改名与导出")
    parser.add_argument("--refresh", action="store_true", help="重新导出已存在的交接包")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    store = Store()
    jobs = store.list(args.limit)
    renamed = exported = 0

    for job in jobs:
        before = job_dir(job.id).name
        after = job_dir_name(job) if not args.write else store.finalize_dir(job.id).name
        if before != after:
            renamed += 1
            print(f"· {before}\n  → {after}")

        bundle_path = job_path(job.id, "transcript.json")
        wanted = job.degraded and bundle_path.exists() and (
            args.refresh or not job_path(job.id, BRIEF).exists()
        )
        if wanted:
            exported += 1
            if args.write:
                bundle = TranscriptBundle.model_validate(read_json(bundle_path))
                export_handoff(job.id, bundle)
                print(f"  + 交接包 handoff/（{len(bundle.segments)} 段 / {bundle.quality.chars} 字）")
        elif args.write and bundle_path.exists():
            # 目录改名后，契约里的音频路径要跟着改，不然 agent 顺着找会扑空
            payload = read_json(bundle_path)
            media = payload.get("media") or {}
            want = f"jobs/{job_dir(job.id).name}/audio.wav"
            if media.get("audio_path") != want and (job_dir(job.id) / "audio.wav").exists():
                media["audio_path"] = want
                write_json(bundle_path, payload)

    mode = "已写入" if args.write else "预览（加 --write 生效）"
    print(f"\n{mode}：{len(jobs)} 个任务，改名 {renamed} 个，补交接包 {exported} 个")
    print(f"索引：{settings.jobs_dir / 'INDEX.md'}")
    if not args.write:
        print("（预览不会动磁盘；确认无误后加 --write）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
