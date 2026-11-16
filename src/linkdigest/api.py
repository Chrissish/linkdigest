"""HTTP 交付层：只做路由与错误码映射，业务动作全在 app.service。

    uvicorn linkdigest.api:app --host 127.0.0.1 --port 8787

单进程即可跑，不需要独立任务队列：瓶颈在 ASR 与网络，不在调度。

配置在启动时读一次；每个任务可以在 `POST /api/jobs` 的 `config` 字段里覆盖白名单内的键
（模型、引擎、分块、温度……）。覆盖不会动数据目录和代理，那些是部署决策。
"""

from __future__ import annotations

import re
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import markdown as md
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import bootstrap
from .app import service
from .app.pipeline import run_job
from .capabilities.speech import available_engines
from .config import ConfigError, Settings, load
from .config import override as config_override
from .conventions.resources import text as resource_text
from .ingest.classify import classify, detect_platform
from .ingest.media_download import probe_extractor
from .ingest.normalize import normalize_input
from .jobs.db import Store
from .jobs.view import job_view
from .net import cookies, proxy
from .net.probe import reachability

cfg: Settings = bootstrap.bootstrap(load())

WEB_DIR = Path(__file__).resolve().parent / "resources" / "web"
UPLOADS = cfg.data_dir / "uploads"
DOWNLOADS = {
    "transcript.json",
    "report.json",
    "report.md",
    "report.html",
    "audio.wav",
    "usage.json",
    "config.json",
    "BRIEF.md",
    "transcript.md",
    "answer.template.json",
}
HANDOFF_FILES = {"BRIEF.md", "transcript.md", "answer.template.json"}
FRONTMATTER = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.S)


class CreateJob(BaseModel):
    url: str
    # 白名单内的覆盖，键名同 .env（ASR_MODEL）或点号式（asr.model）
    config: dict[str, str] | None = None


class AnswerPayload(BaseModel):
    answer: dict | None = None


def _store_for(overrides: dict[str, str] | None) -> Store:
    """按本次请求的覆盖造一个 Store。覆盖改不了 data_dir，所以库还是同一个。"""
    if not overrides:
        return Store(cfg)
    pairs = [f"{key}={value}" for key, value in overrides.items()]
    try:
        resolved = config_override.apply(cfg, pairs)
    except ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Store(resolved)


def _cookies_banner() -> str:
    info = cookies.summary(cfg)
    if not info.get("present"):
        return "无"
    if info.get("logged_in"):
        return "已登录"
    if info.get("google_session"):
        return f"仅访客（{info.get('lines', 0)} 条，有 Google 会话但 YouTube 认证 cookie 缺失）"
    return f"仅访客（{info.get('lines', 0)} 条，未登录）"


@asynccontextmanager
async def lifespan(_: FastAPI):
    effective = proxy.current()
    print(
        f"[linkdigest] 出口代理 {effective or cfg.system_proxy or '（未设置，直连）'}"
        f"{'（系统代理）' if not effective and cfg.system_proxy else ''}"
        f" · 模型 {'就绪 ' + cfg.llm_model if cfg.llm_ready else '未配置（降级模式）'}"
        f" · ASR {cfg.asr_model}"
        f" · cookies {_cookies_banner()}",
        flush=True,
    )
    yield


app = FastAPI(title="LinkDigest", version=cfg.pipeline_version, lifespan=lifespan)
store = Store(cfg)
pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "version": cfg.pipeline_version,
        "ffmpeg": bool(cfg.ffmpeg),
        "js_runtime": cfg.js_runtime,
        "asr_engines": available_engines(),
        "asr_model": cfg.asr_model,
        "llm": {"ready": cfg.llm_ready, "model": cfg.llm_model, "base_url": cfg.llm_base_url},
        "cookies": cookies.summary(cfg),
        "proxy": proxy.current(),
        "system_proxy": cfg.system_proxy,
        "data_dir": str(cfg.data_dir),
        # 前端与排查脚本据此知道「这一进程实际在用哪套配置、配置从哪读的」
        "config": cfg.snapshot(),
    }


@app.get("/api/diagnostics")
def diagnostics(url: str) -> dict:
    """排查「为什么这个链接跑不动」：先看网络，再看解析。"""
    try:
        clean = normalize_input(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    report = reachability(cfg, clean)
    report["input_clean"] = clean
    report["platform"] = detect_platform(clean)
    report["kind"] = classify(clean)
    report["cookies"] = cookies.summary(cfg)
    if report.get("ok"):
        report.update(probe_extractor(cfg, clean))
    return report


@app.post("/api/jobs")
def create_job(payload: CreateJob) -> dict:
    job_store = _store_for(payload.config)
    try:
        job = service.create_job(job_store, payload.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    pool.submit(_run, job_store, job.id)
    return job_view(job_store, job_store.get(job.id) or job)


@app.post("/api/upload")
async def upload(file: UploadFile) -> dict:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    safe = Path(file.filename or "upload.bin").name
    target = UPLOADS / f"{uuid.uuid4().hex[:8]}_{safe}"
    with target.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    job = service.create_job_for_file(store, target)
    pool.submit(_run, store, job.id)
    return job_view(store, job)


@app.get("/api/jobs")
def list_jobs(limit: int = 30) -> list[dict]:
    return [job_view(store, job) for job in store.list(limit)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    detail = service.job_detail(store, job_id)
    if detail is None:
        raise HTTPException(404, "job 不存在")
    return detail


@app.get("/api/jobs/{job_id}/config")
def get_job_config(job_id: str) -> dict:
    """这个任务当时实际用的配置（密钥已打码），用于复现与排查。"""
    if store.get(job_id) is None:
        raise HTTPException(404, "job 不存在")
    snapshot = service.job_config(store, job_id)
    if snapshot is None:
        raise HTTPException(404, "这个任务没有配置快照（早于配置快照特性的老任务）")
    return snapshot


@app.get("/api/jobs/{job_id}/report", response_class=HTMLResponse)
def get_report_html(job_id: str) -> str:
    path = store.job_path(job_id, "report.md")
    if not path.exists():
        raise HTTPException(404, "报告尚未生成")
    # frontmatter 是留给 Obsidian 之类工具读的，页面上不该当成正文第一段
    body = FRONTMATTER.sub("", path.read_text(encoding="utf-8"), count=1)
    return md.markdown(body, extensions=["tables", "fenced_code", "sane_lists"])


@app.get("/api/jobs/{job_id}/markdown", response_class=PlainTextResponse)
def get_report_markdown(job_id: str) -> str:
    path = store.job_path(job_id, "report.md")
    if not path.exists():
        raise HTTPException(404, "报告尚未生成")
    return path.read_text(encoding="utf-8")


@app.get("/api/jobs/{job_id}/bundle")
def get_bundle(job_id: str) -> JSONResponse:
    if not store.job_path(job_id, "transcript.json").exists():
        raise HTTPException(404, "契约文件尚未生成")
    return JSONResponse(service.artifacts(store, job_id).get("bundle", {}))


@app.get("/api/jobs/{job_id}/handoff", response_class=PlainTextResponse)
def get_handoff(job_id: str) -> str:
    """无模型时导出的作业单，直接甩给外部 agent 读。"""
    try:
        return service.brief_text(store, job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "这个任务没有交接包（当时配置了模型，或还没跑到理解层）") from exc


@app.post("/api/jobs/{job_id}/apply")
def apply(job_id: str, payload: AnswerPayload | None = None) -> dict:
    """收外部 agent 的答案：走请求体，或读任务目录里的 handoff/answer.json。"""
    if store.get(job_id) is None:
        raise HTTPException(404, "job 不存在")
    try:
        service.apply_external(store, job_id, payload.answer if payload else None)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, f"答案不合格：{exc}") from exc
    return {"ok": True, **job_view(store, store.get(job_id))}


@app.get("/api/jobs/{job_id}/html", response_class=HTMLResponse)
def get_report_document(job_id: str) -> HTMLResponse:
    """单文件 HTML 报告：自带完整样式，可直接预览、另存、转发。"""
    path = store.job_path(job_id, "report.html")
    if not path.exists():
        path = service.rebuild_html(store, job_id)
    if path is None or not path.exists():
        raise HTTPException(404, "报告尚未生成")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}/download/{name}")
def download(job_id: str, name: str) -> FileResponse:
    if name not in DOWNLOADS:
        raise HTTPException(400, "不支持的文件")
    if name == "report.html" and not store.job_path(job_id, name).exists():
        service.rebuild_html(store, job_id)
    path = store.job_path(job_id, f"handoff/{name}" if name in HANDOFF_FILES else name)
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=f"{store.job_dir(job_id).name}_{name}")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return resource_text("web/index.html")


def _run(job_store: Store, job_id: str) -> None:
    try:
        run_job(job_store, job_id)
    except Exception:  # noqa: BLE001 - 失败原因已写进任务状态
        pass


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
