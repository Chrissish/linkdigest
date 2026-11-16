"""直链下载。所有「已经有音频直链」的路径都走这里，不再各写一遍。

失败时先让 reachability() 重新挑一次出口再重试一次：环境变量里的代理经常是
残留（端口没监听），而真正能出网的是直连或系统代理。只重试一次，不无限重试。
"""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from . import http
from .errors import DownloadFailure, explain_error
from .probe import reachability


def get_json(cfg: Settings, url: str, timeout: float = 12.0, retry: bool = True) -> dict:
    """取 JSON，失败时换一个出口再试一次（同样的道理：残留代理比真断网更常见）。"""
    try:
        return http.get_json(cfg, url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        if retry:
            probe = reachability(cfg, url, timeout=min(6.0, timeout))
            if probe.get("ok"):
                return get_json(cfg, url, timeout=timeout, retry=False)
            raise DownloadFailure(probe.get("error") or explain_error(exc, url, cfg)) from exc
        raise DownloadFailure(explain_error(exc, url, cfg)) from exc


def stream_to(cfg: Settings, url: str, target: Path, retry: bool = True) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with http.client(cfg, timeout=cfg.fetch_timeout) as session:
            with session.stream("GET", url) as response:
                response.raise_for_status()
                with target.open("wb") as fh:
                    for chunk in response.iter_bytes(1 << 16):
                        fh.write(chunk)
    except Exception as exc:  # noqa: BLE001
        if retry:
            probe = reachability(cfg, url, timeout=min(6.0, cfg.fetch_timeout))
            if probe.get("ok"):
                return stream_to(cfg, url, target, retry=False)
            raise DownloadFailure(probe.get("error") or explain_error(exc, url, cfg)) from exc
        raise DownloadFailure(explain_error(exc, url, cfg)) from exc

    size = target.stat().st_size
    if size < 1024:
        target.unlink(missing_ok=True)
        raise DownloadFailure(f"只下到 {size} 字节的占位文件：{url} 可能重定向到了错误页或要求登录。")
    if size > cfg.max_download_mb * 1024 * 1024:
        target.unlink(missing_ok=True)
        raise DownloadFailure("文件超过体积上限（MAX_DOWNLOAD_MB），调大后重试。")
    return target
