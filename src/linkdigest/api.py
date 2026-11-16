"""HTTP 接口 + 单页界面。单进程即可跑，不需要独立任务队列。"""

from __future__ import annotations

import re
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import markdown as md
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .asr import available_engines
from .config import PROJECT_ROOT, settings
from .handoff import ANSWER, BRIEF, apply_answer, read_answer
from .pipeline import load_artifacts, run_job
from .sources import cookies_summary, current_proxy, normalize_input, reachability
from .store import Store, job_dir, job_path


def _cookies_banner() -> str:
    info = cookies_summary()
    if not info.get("present"):
        return "无"
    if info.get("logged_in"):
        return "已登录"
    if info.get("google_session"):
        return f"仅访客（{info.get('lines', 0)} 条，有 Google 会话但 YouTube 认证 cookie 缺失）"
    return f"仅访客（{info.get('lines', 0)} 条，未登录）"


@asynccontextmanager
async def lifespan(_: FastAPI):
    print(
        f"[linkdigest] 出口代理 {current_proxy() or settings.system_proxy or '（未设置，直连）'}"
        f"{'（系统代理）' if not current_proxy() and settings.system_proxy else ''}"
        f" · 模型 {'就绪 ' + settings.llm_model if settings.llm_ready else '未配置（降级模式）'}"
        f" · ASR {settings.asr_model}"
        f" · cookies {_cookies_banner()}",
        flush=True,
    )
    yield


app = FastAPI(title="LinkDigest", version=settings.pipeline_version, lifespan=lifespan)
store = Store()
pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
WEB_DIR = PROJECT_ROOT / "web"
UPLOADS = settings.data_dir / "uploads"


class CreateJob(BaseModel):
    url: str


class AnswerPayload(BaseModel):
    answer: dict | None = None


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "version": settings.pipeline_version,
        "ffmpeg": bool(settings.ffmpeg),
        "js_runtime": settings.js_runtime,
        "asr_engines": available_engines(),
        "asr_model": settings.asr_model,
        "llm": {"ready": settings.llm_ready, "model": settings.llm_model, "base_url": settings.llm_base_url},
        "cookies": cookies_summary(),
        "proxy": current_proxy(),
        "system_proxy": settings.system_proxy,
        "data_dir": str(settings.data_dir),
    }


@app.get("/api/diagnostics")
def diagnostics(url: str) -> dict:
    """排查「为什么这个链接跑不动」：先看网络，再看解析。"""
    try:
        clean = normalize_input(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    report = reachability(clean)
    report["input_clean"] = clean
    report["cookies"] = cookies_summary()
    if report.get("ok"):
        from yt_dlp import YoutubeDL

        try:
            with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                            "extract_flat": True, "socket_timeout": 15}) as ydl:
                info = ydl.extract_info(clean, download=False) or {}
            report["extractor"] = info.get("extractor_key") or info.get("extractor")
            report["title"] = info.get("title")
            report["has_formats"] = bool(info.get("formats"))
        except Exception as exc:  # noqa: BLE001
            from .sources import explain_error

            report["extract_error"] = explain_error(exc, clean)
    return report


@app.post("/api/jobs")
def create_job(payload: CreateJob) -> dict:
    try:
        url = normalize_input(payload.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    job = store.create_job(url, "url")
    pool.submit(_run, job.id)
    return job.as_dict()


@app.post("/api/upload")
async def upload(file: UploadFile) -> dict:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    safe = Path(file.filename or "upload.bin").name
    target = UPLOADS / f"{uuid.uuid4().hex[:8]}_{safe}"
    with target.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    job = store.create_job(str(target), "file")
    pool.submit(_run, job.id)
    return job.as_dict()


@app.get("/api/jobs")
def list_jobs(limit: int = 30) -> list[dict]:
    return [j.as_dict() for j in store.list(limit)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job 不存在")
    data = job.as_dict()
    data["stages"] = [s for s in _stages(job_id)]
    data["artifacts"] = _artifact_flags(job_id)
    return data


_FRONTMATTER = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.S)


@app.get("/api/jobs/{job_id}/report", response_class=HTMLResponse)
def get_report_html(job_id: str) -> str:
    path = job_path(job_id, "report.md")
    if not path.exists():
        raise HTTPException(404, "报告尚未生成")
    # frontmatter 是留给 Obsidian 之类工具读的，页面上不该当成正文第一段
    body = _FRONTMATTER.sub("", path.read_text(encoding="utf-8"), count=1)
    return md.markdown(body, extensions=["tables", "fenced_code", "sane_lists"])


@app.get("/api/jobs/{job_id}/markdown", response_class=PlainTextResponse)
def get_report_markdown(job_id: str) -> str:
    path = job_path(job_id, "report.md")
    if not path.exists():
        raise HTTPException(404, "报告尚未生成")
    return path.read_text(encoding="utf-8")


@app.get("/api/jobs/{job_id}/bundle")
def get_bundle(job_id: str) -> JSONResponse:
    path = job_path(job_id, "transcript.json")
    if not path.exists():
        raise HTTPException(404, "契约文件尚未生成")
    return JSONResponse(load_artifacts(job_id).get("bundle", {}))


@app.get("/api/jobs/{job_id}/handoff", response_class=PlainTextResponse)
def get_handoff(job_id: str) -> str:
    """无模型时导出的作业单，直接甩给外部 agent 读。"""
    path = job_path(job_id, BRIEF)
    if not path.exists():
        raise HTTPException(404, "这个任务没有交接包（当时配置了模型，或还没跑到理解层）")
    return path.read_text(encoding="utf-8")


@app.post("/api/jobs/{job_id}/apply")
def apply(job_id: str, payload: AnswerPayload | None = None) -> dict:
    """收外部 agent 的答案：走请求体，或读任务目录里的 handoff/answer.json。"""
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job 不存在")
    try:
        answer = (payload.answer if payload else None) or read_answer(job_id)
        apply_answer(job_id, answer, store)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, f"答案不合格：{exc}") from exc
    return {"ok": True, **(store.get(job_id).as_dict() if store.get(job_id) else {})}


@app.get("/api/jobs/{job_id}/html", response_class=HTMLResponse)
def get_report_document(job_id: str) -> HTMLResponse:
    """单文件 HTML 报告：自带完整样式，可直接预览、另存、转发。"""
    path = job_path(job_id, "report.html")
    if not path.exists():
        path = _rebuild_html(job_id)
    if path is None or not path.exists():
        raise HTTPException(404, "报告尚未生成")
    return HTMLResponse(path.read_text(encoding="utf-8"))


def _rebuild_html(job_id: str):
    """老任务没有 report.html 时按需补一份，不动已有的 report.md。"""
    from .html_report import render_html
    from .models import Report, TranscriptBundle
    from .store import read_json

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


@app.get("/api/jobs/{job_id}/download/{name}")
def download(job_id: str, name: str) -> FileResponse:
    if name not in {"transcript.json", "report.json", "report.md", "report.html", "audio.wav",
                    "usage.json", "BRIEF.md", "transcript.md", "answer.template.json"}:
        raise HTTPException(400, "不支持的文件")
    if name == "report.html" and not job_path(job_id, name).exists():
        _rebuild_html(job_id)
    in_handoff = name in {"BRIEF.md", "transcript.md", "answer.template.json"}
    path = job_path(job_id, f"handoff/{name}" if in_handoff else name)
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=f"{job_dir(job_id).name}_{name}")


def _run(job_id: str) -> None:
    try:
        run_job(job_id, store)
    except Exception:
        pass


def _stages(job_id: str) -> list[dict]:
    from .store import read_stages

    return [s for s in read_stages(job_id) if s.get("stage") != "error"]


def _artifact_flags(job_id: str) -> dict[str, bool]:
    return {name: job_path(job_id, name).exists()
            for name in ("transcript.json", "report.json", "report.md", "report.html",
                         BRIEF, ANSWER)}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
