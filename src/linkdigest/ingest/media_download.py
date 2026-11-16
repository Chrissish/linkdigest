"""yt-dlp 适配器：只取音轨，永不抓帧。

它只负责「把这个页面变成本地音频文件 + 一份元数据」。平台判定、降级顺序、
错误解释都在调用方，所以换掉 yt-dlp 只需要另写一个签名相同的函数。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from ..config import Settings
from ..contracts.models import SourceMeta
from ..conventions.text import strip_ansi
from ..net import cookies
from ..net.errors import DownloadFailure, explain_error
from ..net.probe import reachability


class _YdlLogger:
    """接住 yt-dlp 的日志：失败时要靠它说清到底发生了什么。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        self.lines.append(msg)

    def warning(self, msg: str) -> None:
        self.lines.append(msg)

    def error(self, msg: str) -> None:
        self.lines.append(msg)

    def tail(self, n: int = 3) -> str:
        clean = [strip_ansi(x).strip() for x in self.lines if x.strip()]
        return " / ".join(clean[-n:]) if clean else ""


@contextmanager
def _clean_node_env() -> Iterator[None]:
    """临时摘掉 NODE_OPTIONS。

    yt-dlp 解 n 挑战时用 `node --experimental-permission`（v23.5 前）起子进程，
    这个模式默认禁止读任意文件。而 IDE / 沙箱 / 安全工具常往 NODE_OPTIONS 里塞
    `--require=.../some-shim.cjs`，node 启动时找不到那个文件就直接退出，
    表现为「n challenge solving failed」，看起来像缺 JS 运行时，实际是被自己的环境卡死。
    """
    saved = os.environ.pop("NODE_OPTIONS", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["NODE_OPTIONS"] = saved


def _options(
    cfg: Settings, workdir: Path, logger: _YdlLogger, progress: Callable[[float], None] | None
) -> dict:
    def hook(payload: dict) -> None:
        if payload.get("status") == "downloading" and progress:
            total = payload.get("total_bytes") or payload.get("total_bytes_estimate") or 0
            if total:
                progress(min(0.99, (payload.get("downloaded_bytes") or 0) / total))

    options: dict = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "source.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "retries": 1,
        "fragment_retries": 1,
        "socket_timeout": 15,
        "max_filesize": cfg.max_download_mb * 1024 * 1024,
        "progress_hooks": [hook],
        "logger": logger,
    }
    cookie_path = cookies.cookie_file(cfg)
    if cookie_path:
        options["cookiefile"] = cookie_path
    elif cfg.cookies_from_browser:
        options["cookiesfrombrowser"] = (cfg.cookies_from_browser,)

    # YouTube 的 n 挑战要在 JS 里跑：没有运行时，画质/音轨都拿不到，
    # 只返回一串 storyboard 封面图，然后报「Requested format is not available」。
    if cfg.js_runtime:
        runtime = "deno" if "deno" in Path(cfg.js_runtime).name else "node"
        options["js_runtimes"] = {runtime: {"path": cfg.js_runtime}}
    return options


def download(
    cfg: Settings, url: str, workdir: Path, progress: Callable[[float], None] | None = None
) -> tuple[Path, dict]:
    from yt_dlp import YoutubeDL

    # 先花几秒确认能不能连上：连不上就没必要让 yt-dlp 反复重试拖上几分钟。
    probe = reachability(cfg, url, timeout=min(8.0, cfg.fetch_timeout))
    if not probe.get("ok"):
        raise DownloadFailure(probe.get("error") or f"连不上 {probe.get('host')}")

    workdir.mkdir(parents=True, exist_ok=True)
    logger = _YdlLogger()
    try:
        with _clean_node_env(), YoutubeDL(_options(cfg, workdir, logger, progress)) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001 - 统一转成可读原因
        raise DownloadFailure(explain_error(exc, url, cfg)) from exc

    if not info:
        raise DownloadFailure(explain_error(logger.tail() or "解析结果为空", url, cfg))

    candidates = [
        path
        for path in sorted(workdir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
        if not path.name.endswith((".part", ".ytdl"))
    ]
    if not candidates:
        extractor = info.get("extractor_key") or info.get("extractor") or "generic"
        formats = info.get("formats") or []
        detail = logger.tail() or f"解析器 {extractor} 返回了元数据但没有可下载格式（formats={len(formats)}）"
        raise DownloadFailure(explain_error(detail, url, cfg) + f"〔解析器 {extractor}〕")
    if candidates[0].stat().st_size < 1024:
        raise DownloadFailure(
            f"只下到 {candidates[0].stat().st_size} 字节的占位文件，通常是平台返回了错误页或验证页。"
            + (f"（{logger.tail()}）" if logger.tail() else "")
        )
    return candidates[0], info


def source_meta(info: dict, url: str, platform: str, kind: str) -> SourceMeta:
    """把 yt-dlp 的 info 映射成契约里的来源元信息。"""
    return SourceMeta(
        kind=kind,
        platform=platform,
        url=url,
        canonical_url=info.get("webpage_url") or url,
        title=info.get("title"),
        author=info.get("uploader") or info.get("channel"),
        published_at=info.get("upload_date"),
        duration_sec=info.get("duration"),
        lang_hint=info.get("language"),
        extractor=f"yt-dlp/{info.get('extractor_key') or info.get('extractor') or 'generic'}",
        extra={"thumbnail": info.get("thumbnail"), "view_count": info.get("view_count")},
    )


def probe_extractor(cfg: Settings, url: str) -> dict:
    """只解析不下载：诊断「这个链接为什么跑不动」时，看解析器认不认。"""
    from yt_dlp import YoutubeDL

    try:
        with YoutubeDL(
            {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True,
             "socket_timeout": 15}
        ) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        return {
            "extractor": info.get("extractor_key") or info.get("extractor"),
            "title": info.get("title"),
            "has_formats": bool(info.get("formats")),
        }
    except Exception as exc:  # noqa: BLE001 - 诊断端点不该因为解析失败而 500
        return {"extract_error": explain_error(exc, url, cfg)}


__all__ = ["download", "probe_extractor", "source_meta"]
