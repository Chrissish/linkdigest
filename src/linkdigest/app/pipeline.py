"""编排器：一个 job 从链接走到报告。

每阶段写状态与耗时，失败即停并保留已产生的产物。
只做「按顺序串起来」和「把异常翻译成用户能读的失败原因」，不实现任何一步的细节。

配置从 `store.cfg` 取：Store 就是用这份配置造出来的，两者不可能对不上。
"""

from __future__ import annotations

import json
import threading
import time

from .. import ingest
from ..capabilities import ffmpeg
from ..capabilities.llm import LLMClient
from ..capabilities.speech import transcribe
from ..clean import assign_idx, normalize_segments, text_to_segments
from ..config import Settings
from ..contracts.builder import build_bundle
from ..contracts.models import MediaMeta, SourceMeta, TranscriptBundle
from ..contracts.validation import validate_bundle
from ..conventions.files import sha256_file
from ..conventions.jsonio import write_json
from ..conventions.text import script_of
from ..handoff import export_handoff
from ..ingest.classify import bundle_id_for
from ..jobs.db import Store
from ..jobs.finalize import finalize
from ..jobs.stages import append_stage, read_stages
from ..net.errors import DownloadFailure
from ..render.save import save_report
from ..understand import build_report

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
    """阶段计时与状态写库。进度百分比按 PROGRESS 表给，不按阶段猜。"""

    def __init__(self, store: Store, job_id: str, name: str) -> None:
        self.store, self.job_id, self.name = store, job_id, name
        self.started = 0.0

    def __enter__(self) -> "Stage":
        self.started = time.time()
        self.store.update(
            self.job_id, stage=self.name, progress=PROGRESS.get(self.name, 0.0), status="running"
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        append_stage(
            self.store,
            self.job_id,
            {
                "stage": self.name,
                "seconds": round(time.time() - self.started, 2),
                "ok": exc is None,
                **({"error": str(exc)} if exc else {}),
            },
        )
        return None


def _understand_with_timeout(
    cfg: Settings, bundle: TranscriptBundle, client: LLMClient
) -> tuple[Any, Any]:
    """build_report 的看门狗。

    理解阶段曾在无网络请求、无文件写入的状态下挂死过（疑似线程池丢工）——
    httpx 的请求级超时救不了「没有请求在飞」的挂死，只能靠整阶段硬上限兜底。
    超时把任务判失败（可重试）；挂死的线程随进程存活，重启才回收。
    """
    result: dict[str, Any] = {}

    def work() -> None:
        try:
            result["value"] = build_report(cfg, bundle, client)
        except BaseException as exc:  # noqa: BLE001 —— 原样转交，别在这里判成败
            result["error"] = exc

    worker = threading.Thread(target=work, daemon=True, name="understand")
    worker.start()
    worker.join(cfg.understand_timeout)
    if worker.is_alive():
        raise RuntimeError(
            f"理解阶段超过 {cfg.understand_timeout:.0f} 秒没有完成，看门狗中止"
            "（详见任务目录 llm_debug.jsonl 最后一条；这个任务可以重试）"
        )
    if "error" in result:
        raise result["error"]
    return result["value"]


def run_job(store: Store, job_id: str) -> None:
    cfg = store.cfg
    job = store.get(job_id)
    if job is None:
        return
    workdir = store.job_path(job_id, "")
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        with Stage(store, job_id, "ingest"):
            result = ingest.fetch(
                cfg,
                job.input,
                workdir,
                progress=lambda p: store.update(job_id, progress=0.05 + 0.07 * p),
            )
            source: SourceMeta = result.source
            canonical = source.canonical_url or source.url or job.input
            bundle_id = bundle_id_for(canonical)
            write_json(
                store.job_path(job_id, "source.json"),
                {"source": json.loads(source.model_dump_json()), "notes": result.notes},
            )
            store.update(
                job_id, title=source.title, platform=source.platform, duration_sec=source.duration_sec
            )
        # 目录名要用标题，所以先算出最终名字（真正的改名在收尾时做）
        final_dir = store.dir_name(store.get(job_id))

        if result.media_file is not None:
            with Stage(store, job_id, "extract"):
                wav = ffmpeg.to_wav(cfg, result.media_file, store.job_path(job_id, "audio.wav"))
                duration = ffmpeg.guard_duration(cfg, wav)
            with Stage(store, job_id, "asr"):
                store.update(job_id, progress=0.22)
                asr = transcribe(cfg, wav, language=source.lang_hint, duration=duration)
                store.update(job_id, progress=0.58)
            with Stage(store, job_id, "normalize"):
                cleaned = normalize_segments(cfg, asr.segments)
            # 引擎降级不是错误，但必须让人看见：这条会进 bundle.quality.warnings，报告里能查到
            if asr.raw.get("engine_note"):
                cleaned.warnings.append(str(asr.raw["engine_note"]))
            media = MediaMeta(
                audio_path=f"jobs/{final_dir}/audio.wav",
                audio_sha256=sha256_file(wav),
                bytes=wav.stat().st_size,
            )
            language, probability, engine = asr.language, asr.language_probability, asr.engine
            duration_sec = duration
        else:
            with Stage(store, job_id, "normalize"):
                cleaned = text_to_segments(cfg, result.text or "")
            media = None
            engine = source.extractor
            language = source.lang_hint or {
                "cjk": "zh",
                "latin": "en",
            }.get(script_of(result.text or ""), "unknown")
            probability = 1.0
            duration_sec = None

        if not cleaned.segments:
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
                segments=assign_idx(cleaned.segments),
                flags=cleaned.flags,
                warnings=cleaned.warnings,
                stats=cleaned.stats,
                language=language,
                language_probability=probability,
                asr_engine=engine,
                media=media,
            )
            problems = validate_bundle(json.loads(bundle.model_dump_json()))
            if problems:
                raise RuntimeError("契约校验失败：" + "; ".join(problems))
            write_json(store.job_path(job_id, "transcript.json"), json.loads(bundle.model_dump_json()))
            store.update(job_id, chars=bundle.quality.chars)
            if duration_sec:
                store.update(job_id, duration_sec=duration_sec)

        client = LLMClient(cfg, debug_log=store.job_path(job_id, "llm_debug.jsonl"))
        with Stage(store, job_id, "understand"):
            report, run = _understand_with_timeout(cfg, bundle, client)
            store.update(
                job_id,
                llm_model=cfg.llm_model if client.available else None,
                degraded=0 if not report.degraded else 1,
                cost_usd=client.stats.cost_usd,
            )

        with Stage(store, job_id, "report"):
            write_json(store.job_path(job_id, "report.json"), json.loads(report.model_dump_json()))
            created = store.get(job_id)
            save_report(
                store,
                job_id,
                bundle,
                report,
                extra={
                    "llm_stats": client.stats.as_dict(),
                    "mode": run.mode,
                    "job_id": job_id,
                    "created_at": created.created_at if created else None,
                },
            )
            write_json(
                store.job_path(job_id, "usage.json"),
                {
                    "llm": client.stats.as_dict(),
                    "mode": run.mode,
                    "chunks": run.chunks,
                    "dir": final_dir,
                    "stages": [s for s in read_stages(store, job_id) if s.get("stage") != "error"],
                },
            )

        if report.degraded:
            # 没有模型密钥时，把「理解」这一步交出去：导出可被外部 agent 直接消化的交接包
            with Stage(store, job_id, "handoff"):
                export_handoff(store, job_id, bundle)
        store.update(job_id, status="done", stage="done", progress=1.0)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, DownloadFailure) else f"{type(exc).__name__}: {exc}"
        append_stage(store, job_id, {"stage": "error", "error": detail})
        store.update(job_id, status="failed", error=detail)
        raise
    finally:
        # 无论成败，目录名都变得可读：时间_平台_标题_job_id
        finalize(store, job_id)


def rerun_understand(store: Store, job_id: str) -> None:
    """对已有转写产物的任务单独重跑「理解 → 报告」。

    降级（无密钥 / 调用失败）不算失败，任务状态仍是 done——所以这次重跑
    失败时也不把任务打成 failed，只记错误并保留上一版报告。
    """
    cfg = store.cfg
    if store.get(job_id) is None:
        raise RuntimeError("任务不存在")
    bundle_path = store.job_path(job_id, "transcript.json")
    if not bundle_path.exists():
        raise RuntimeError("这个任务没有转写产物，无法单独重跑理解，请重试整个链接")

    try:
        bundle = TranscriptBundle.model_validate(json.loads(bundle_path.read_text(encoding="utf-8")))
        client = LLMClient(cfg, debug_log=store.job_path(job_id, "llm_debug.jsonl"))
        with Stage(store, job_id, "understand"):
            report, run = _understand_with_timeout(cfg, bundle, client)
            store.update(
                job_id,
                llm_model=cfg.llm_model if client.available else None,
                degraded=0 if not report.degraded else 1,
                cost_usd=client.stats.cost_usd,
            )
        with Stage(store, job_id, "report"):
            write_json(store.job_path(job_id, "report.json"), json.loads(report.model_dump_json()))
            created = store.get(job_id)
            save_report(
                store,
                job_id,
                bundle,
                report,
                extra={
                    "llm_stats": client.stats.as_dict(),
                    "mode": run.mode,
                    "job_id": job_id,
                    "created_at": created.created_at if created else None,
                },
            )
            # 用量在上一轮基础上累加，重跑烧掉的 token 不从账本上消失
            usage = {
                "llm": client.stats.as_dict(),
                "mode": run.mode,
                "chunks": run.chunks,
                "dir": store.dir_name(store.get(job_id)),
                "stages": [s for s in read_stages(store, job_id) if s.get("stage") != "error"],
            }
            prev_path = store.job_path(job_id, "usage.json")
            if prev_path.exists():
                try:
                    prev = json.loads(prev_path.read_text(encoding="utf-8"))
                    old = prev.get("llm") or {}
                    cur = usage["llm"]
                    for key in ("calls", "prompt_tokens", "completion_tokens"):
                        cur[key] = cur.get(key, 0) + old.get(key, 0)
                    cur["seconds"] = cur.get("seconds", 0.0) + old.get("seconds", 0.0)
                    cur["cost_usd"] = round(cur.get("cost_usd", 0.0) + old.get("cost_usd", 0.0), 6)
                    cur["errors"] = (old.get("errors") or []) + cur.get("errors") or []
                    usage["previous_mode"] = prev.get("mode")
                except (json.JSONDecodeError, KeyError):
                    pass
            write_json(store.job_path(job_id, "usage.json"), usage)
        store.update(job_id, status="done", stage="done", progress=1.0, error=None)
    except Exception as exc:
        # 不推翻已完成的任务状态，但要让前端和阶段日志都看得见这次失败
        append_stage(store, job_id, {"stage": "error", "error": str(exc)})
        store.update(job_id, status="done", stage="understand", error=f"重新理解失败：{exc}")
        raise
