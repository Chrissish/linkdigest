"""编排器：一个 job 从链接走到报告。每阶段写状态与耗时，失败即停并保留已产生的产物。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import sources
from .asr import transcribe
from .bundle import build_bundle, save_bundle, validate_bundle
from .config import settings
from .handoff import export_handoff
from .llm import LLMClient
from .media import guard_duration, to_wav
from .models import MediaMeta, SourceMeta
from .normalize import assign_idx, normalize_segments, text_to_segments
from .report import build_report, save_report
from .store import Store, append_stage, job_dir_name, job_path, read_json, write_json

PROGRESS = {
    "ingest": 0.05,
    "download": 0.12,
    "extract": 0.18,
    "asr": 0.30,
    "normalize": 0.62,
    "bundle": 0.68,
    "understand": 0.74,
    "report": 0.95,
    "handoff": 0.98,
    "done": 1.0,
}


class Stage:
    def __init__(self, store: Store, job_id: str, name: str) -> None:
        self.store, self.job_id, self.name = store, job_id, name
        self.started = 0.0

    def __enter__(self) -> "Stage":
        self.started = time.time()
        self.store.update(self.job_id, stage=self.name, progress=PROGRESS.get(self.name, 0.0), status="running")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed = round(time.time() - self.started, 2)
        append_stage(self.job_id, {"stage": self.name, "seconds": elapsed, "ok": exc is None,
                                   **({"error": str(exc)} if exc else {})})
        return None


def run_job(job_id: str, store: Store) -> None:
    job = store.get(job_id)
    if job is None:
        return
    workdir = job_path(job_id, "")
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        with Stage(store, job_id, "ingest"):
            result = sources.fetch(job.input, workdir, progress=lambda p: store.update(
                job_id, progress=0.05 + 0.07 * p))
            source: SourceMeta = result.source
            canonical = source.canonical_url or source.url or job.input
            bundle_id = sources.bundle_id_for(canonical)
            write_json(job_path(job_id, "source.json"),
                       {"source": json.loads(source.model_dump_json()), "notes": result.notes})
            store.update(job_id, title=source.title, platform=source.platform,
                         duration_sec=source.duration_sec)
        # 目录名要用标题，所以先算出最终名字（真正的改名在收尾时做）
        final_dir = job_dir_name(store.get(job_id))

        if result.media_file is not None:
            with Stage(store, job_id, "extract"):
                wav = to_wav(Path(result.media_file), job_path(job_id, "audio.wav"))
                duration = guard_duration(wav)
            with Stage(store, job_id, "asr"):
                store.update(job_id, progress=0.22)
                asr = transcribe(wav, language=source.lang_hint, duration=duration)
                store.update(job_id, progress=0.58)
            with Stage(store, job_id, "normalize"):
                segments, flags, warnings, stats = normalize_segments(asr.segments)
            media = MediaMeta(
                audio_path=f"jobs/{final_dir}/audio.wav",
                audio_sha256=sources.sha256_file(wav),
                bytes=wav.stat().st_size,
            )
            language, prob, engine = asr.language, asr.language_probability, asr.engine
            duration_sec = duration
        else:
            with Stage(store, job_id, "normalize"):
                segments, flags, warnings, stats = text_to_segments(result.text or "")
            media = None
            engine = source.extractor
            inferred = source.lang_hint
            if not inferred:
                from .normalize import script_of

                inferred = {"cjk": "zh", "latin": "en"}.get(script_of(result.text or ""), "unknown")
            language, prob = inferred, 1.0
            duration_sec = None

        if not segments:
            if media is not None:
                raise RuntimeError(
                    f"音频里没有识别出人声：{duration:.0f} 秒内 ASR 没产出可用文本。"
                    "纯音乐、纯音效或整段静音会这样；也可能是语言不在 Whisper 覆盖范围内。"
                    f"（引擎 {engine}）"
                )
            raise RuntimeError("正文抽取后没有可用文本：页面可能是图片、视频封面或需要登录的壳页。")

        with Stage(store, job_id, "bundle"):
            bundle = build_bundle(
                bundle_id=bundle_id,
                source=source,
                segments=assign_idx(segments),
                flags=flags,
                warnings=warnings,
                stats=stats,
                language=language,
                language_probability=prob,
                asr_engine=engine,
                media=media,
            )
            payload = json.loads(bundle.model_dump_json())
            problems = validate_bundle(payload)
            if problems:
                raise RuntimeError("契约校验失败：" + "; ".join(problems))
            save_bundle(bundle, job_id)
            store.update(job_id, chars=bundle.quality.chars)
            if duration_sec:
                store.update(job_id, duration_sec=duration_sec)

        client = LLMClient()
        with Stage(store, job_id, "understand"):
            report, mode = build_report(bundle, client)
            store.update(job_id, llm_model=settings.llm_model if client.available else None,
                         degraded=0 if not report.degraded else 1,
                         cost_usd=client.stats.cost_usd)

        with Stage(store, job_id, "report"):
            write_json(job_path(job_id, "report.json"), json.loads(report.model_dump_json()))
            created = store.get(job_id)
            save_report(job_id, bundle, report,
                        extra={"llm_stats": client.stats.as_dict(), "mode": mode,
                               "job_id": job_id, "created_at": created.created_at if created else None})
            write_json(job_path(job_id, "usage.json"),
                       {"llm": client.stats.as_dict(), "mode": mode, "dir": final_dir,
                        "stages": _stages(job_id)})

        if report.degraded:
            # 没有模型密钥时，把「理解」这一步交出去：导出可被外部 agent 直接消化的交接包
            with Stage(store, job_id, "handoff"):
                export_handoff(job_id, bundle)
        store.update(job_id, status="done", stage="done", progress=1.0)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, sources.DownloadFailure) else f"{type(exc).__name__}: {exc}"
        append_stage(job_id, {"stage": "error", "error": detail})
        store.update(job_id, status="failed", error=detail)
        raise
    finally:
        # 无论成败，目录名都变得可读：时间_平台_标题_job_id
        store.finalize_dir(job_id)


def _stages(job_id: str) -> list[dict[str, Any]]:
    from .store import read_stages

    return [s for s in read_stages(job_id) if s.get("stage") not in {"error"}]


def load_artifacts(job_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, key in [("transcript.json", "bundle"), ("report.json", "report"), ("usage.json", "usage"),
                      ("source.json", "source")]:
        path = job_path(job_id, name)
        if path.exists():
            out[key] = read_json(path)
    md = job_path(job_id, "report.md")
    if md.exists():
        out["markdown"] = md.read_text(encoding="utf-8")
    return out
