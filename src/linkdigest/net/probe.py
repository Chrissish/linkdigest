"""探活：先直连，再依次试候选代理；谁通用谁，并把结果写回环境变量。

真实场景逼出来的顺序：很多人机器上开着 TUN/增强模式，直连就通了，
而 shell 里那个 `HTTP_PROXY=127.0.0.1:7987` 早就是残留，端口根本没监听；
也有反过来的：直连被墙、只有代理能出，而代理只开在「系统代理」里、
不往环境变量里导。三种情况都必须能自己走通。
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlsplit

from ..config import settings
from . import http, proxy
from .errors import explain_error


def _probe(anchor: str, timeout: float, upstream: str | None) -> dict:
    started = time.time()
    try:
        with http.client(timeout=timeout, proxy=upstream, direct=upstream is None) as session:
            response = session.get(anchor)
        return {
            "ok": response.status_code < 400,
            "status": response.status_code,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        message = explain_error(exc, anchor)
        if upstream and ("超时" in message or "网络层失败" in message):
            parsed = urlsplit(proxy.normalize(upstream))
            message += (
                f"（失败的是到代理 {upstream} 的连接：先确认它在监听 "
                f"`nc -z {parsed.hostname or '127.0.0.1'} {parsed.port or 7890}`）"
            )
        return {"ok": False, "error": message, "elapsed_ms": int((time.time() - started) * 1000)}


def reachability(url: str, timeout: float = 8.0) -> dict:
    parts = urlsplit(url)
    host = parts.hostname or ""
    anchor = f"{parts.scheme or 'https'}://{parts.netloc or host}/"
    candidates = proxy.candidates(settings.system_proxy)
    attempts: list[dict] = []

    direct = _probe(anchor, timeout, None)
    direct["via"] = "直连"
    attempts.append(direct)
    if direct["ok"]:
        note = ""
        stale = [c for c in candidates if c == proxy.current()]
        if stale:
            proxy.clear()
            note = f"已忽略失效代理 {stale[0]}，本次走直连"
        return {
            "host": host,
            "ok": True,
            "mode": "direct",
            "status": direct.get("status"),
            "elapsed_ms": direct["elapsed_ms"],
            "attempts": attempts,
            "proxy": {},
            "candidates": candidates,
            "note": note,
        }

    errors = [f"直连失败：{direct.get('error')}"]
    for candidate in candidates:
        proxied = _probe(anchor, timeout, candidate)
        proxied["via"] = f"代理 {candidate}"
        attempts.append(proxied)
        if proxied["ok"]:
            proxy.apply(candidate)
            origin = "系统代理" if candidate == settings.system_proxy else "环境变量"
            return {
                "host": host,
                "ok": True,
                "mode": "proxy",
                "status": proxied.get("status"),
                "elapsed_ms": proxied["elapsed_ms"],
                "attempts": attempts,
                "proxy": _env_snapshot(),
                "candidates": candidates,
                "note": f"直连不通，已切到{origin} {candidate}",
            }
        errors.append(f"代理 {candidate} 也失败：{proxied.get('error')}")

    return {
        "host": host,
        "ok": False,
        "mode": "none",
        "error": "；".join(errors),
        "attempts": attempts,
        "elapsed_ms": int(sum(a.get("elapsed_ms", 0) for a in attempts)),
        "proxy": _env_snapshot(),
        "candidates": candidates,
    }


def _env_snapshot() -> dict[str, str]:
    return {key: os.environ[key] for key in proxy.PROXY_KEYS if os.environ.get(key)}
