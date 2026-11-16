"""命令行交付层。

    python -m linkdigest.cli <url|file>        # 跑一条内容
    python -m linkdigest.cli --list           # 看所有任务（目录名可读）
    python -m linkdigest.cli --prune-failed   # 清掉所有失败任务（移入废纸篓）
    python -m linkdigest.cli <job> --brief    # 打印交接作业单（无模型时给外部 agent 读）
    python -m linkdigest.cli <job> --apply    # 把 handoff/answer.json 变成正式报告

这里只做参数解析与终端排版；动作都在 app.service。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import bootstrap
from .app import service
from .app.pipeline import run_job
from .config import settings
from .handoff import ANSWER, BRIEF
from .ingest.normalize import normalize_input
from .jobs.db import Store
from .jobs.layout import job_dir
from .jobs.stages import STAGES, read_stages
from .net.probe import reachability


def _width(text: str) -> int:
    """终端里中日韩字符占两格，按显示宽度补齐才不会错位。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


def _pad(text: str, size: int) -> str:
    return text + " " * max(0, size - _width(text))


def _clipw(text: str, size: int) -> str:
    out, used = "", 0
    for ch in text:
        step = 2 if ord(ch) > 0x2E7F else 1
        if used + step > size - 1:
            return out + "…"
        out, used = out + ch, used + step
    return text


def _print_jobs(store: Store, limit: int = 40) -> None:
    jobs = store.list(limit)
    if not jobs:
        print("还没有任务。", file=sys.stderr)
        return
    labels = dict(STAGES)
    print(f"{_pad('目录', 44)} {_pad('状态', 6)} {_pad('平台', 10)} 标题", file=sys.stderr)
    for job in jobs:
        state = "失败" if job.status == "failed" else ("待理解" if job.degraded else labels.get(job.stage, job.stage))
        title = _clipw(job.title or job.input, 40)
        print(
            f"{_pad(_clipw(job_dir(job.id).name, 44), 44)} {_pad(state, 6)} "
            f"{_pad(job.platform or '-', 10)} {title}",
            file=sys.stderr,
        )
    print(f"\n索引文件：{service.index_path()}", file=sys.stderr)


def _prune(store: Store, tokens: list[str]) -> int:
    total = len(store.list(500))
    if not tokens:
        print("没有指定任务。", file=sys.stderr)
        return 1
    removed = service.prune(store, tokens)
    for job, moved in removed:
        tail = f" → {moved}" if moved else "（目录已不存在）"
        print(f"· {job.id} {job_dir(job.id).name}{tail}", file=sys.stderr)
    if not removed:
        print("没有删除任何任务。", file=sys.stderr)
        return 1
    print(f"\n已删除 {len(removed)} 个任务（目录在废纸篓），剩余 {total - len(removed)} 个。", file=sys.stderr)
    print(f"索引：{service.index_path()}", file=sys.stderr)
    return 0


def _run_job(store: Store, clean: str, show: bool) -> int:
    job = service.create_job(store, clean)
    print(f"job {job.id} 开始处理：{clean}", file=sys.stderr)
    started = time.time()
    try:
        run_job(job.id, store)
    except Exception as exc:  # noqa: BLE001 - 原因已写进任务状态，这里只报出口
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    directory = job_dir(job.id)
    print(f"完成，用时 {time.time() - started:.1f}s", file=sys.stderr)
    print(f"产物目录：{directory}", file=sys.stderr)
    for stage in read_stages(job.id):
        print(f"  {stage['stage']:<10} {stage.get('seconds', 0):>7.2f}s", file=sys.stderr)
    final = store.get(job.id)
    if final and final.degraded:
        print("\n没有模型密钥，理解层已交出去：", file=sys.stderr)
        print(f"  作业单 {directory / BRIEF}", file=sys.stderr)
        print(f"  填好 {directory / ANSWER} 后运行：python -m linkdigest.cli {job.id} --apply", file=sys.stderr)
    if show:
        print((directory / "report.md").read_text(encoding="utf-8"))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser("linkdigest", description="媒体链接 → 内容总结报告")
    parser.add_argument(
        "input", nargs="?", help="链接、本地音视频/文本文件，或已有任务的 id/目录名/标题片段"
    )
    parser.add_argument("--no-llm", action="store_true", help="强制降级模式，不调用模型")
    parser.add_argument("--show", action="store_true", help="结束后直接打印报告")
    parser.add_argument("--check", action="store_true", help="只做连通性与解析自检，不跑管道")
    parser.add_argument("--list", action="store_true", help="列出所有任务（按时间倒序）")
    parser.add_argument("--prune-failed", action="store_true", help="删除所有失败任务（目录移入废纸篓）")
    parser.add_argument(
        "--prune", nargs="+", metavar="JOB", help="删除指定任务，可给 id 前缀 / 目录名 / 链接片段，多个用空格隔开"
    )
    parser.add_argument("--brief", action="store_true", help="打印交接作业单，交给外部 agent 读")
    parser.add_argument("--apply", action="store_true", help="应用 handoff/answer.json，生成正式报告")
    parser.add_argument("--answer", help="答案文件路径（默认 <任务目录>/handoff/answer.json）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    bootstrap.bootstrap()
    store = Store()

    if args.list:
        _print_jobs(store)
        return 0
    if args.prune_failed:
        return _prune(store, [job.id for job in service.failed_jobs(store)])
    if args.prune:
        return _prune(store, args.prune)

    if args.brief or args.apply:
        if not args.input:
            print("要用 --brief / --apply，得指定一个任务：id、目录名或标题片段都行。", file=sys.stderr)
            return 2
        job = store.resolve(args.input)
        if job is None:
            print(f"没找到任务「{args.input}」。先跑 --list 看看有哪些。", file=sys.stderr)
            return 2
        directory = job_dir(job.id)
        if args.brief:
            try:
                print(service.brief_text(job.id))
            except FileNotFoundError:
                print(f"这个任务没有交接包（说明当时配了模型，或还没跑完）。{directory}", file=sys.stderr)
                return 1
            return 0
        try:
            report = service.apply_external(store, job.id, path=Path(args.answer) if args.answer else None)
        except (FileNotFoundError, ValueError, KeyError) as exc:
            print(f"应用失败：{exc}", file=sys.stderr)
            return 1
        print(f"已应用，报告写入：{directory / 'report.md'}", file=sys.stderr)
        print(f"一句话：{report.one_liner}", file=sys.stderr)
        if args.show:
            print((directory / "report.md").read_text(encoding="utf-8"))
        return 0

    if not args.input:
        _build_parser().print_help()
        return 2

    if args.no_llm:
        settings.llm_api_key = None

    try:
        clean = normalize_input(args.input)
    except ValueError as exc:
        print(f"输入有误：{exc}", file=sys.stderr)
        return 2

    if args.check:
        report = reachability(clean)
        print(f"归一后输入：{clean}", file=sys.stderr)
        for key, value in report.items():
            print(f"  {key}: {value}", file=sys.stderr)
        return 0 if report.get("ok") else 1

    if clean != args.input.strip():
        print(f"（已从你粘贴的内容里提取出干净链接：{clean}）", file=sys.stderr)
    return _run_job(store, clean, args.show)


if __name__ == "__main__":
    raise SystemExit(main())
