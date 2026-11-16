"""Netscape cookies.txt 的读取与体检。

两件事分开报：「浏览器登录过没有」（本地判断）与「目标站认不认」（真打一次站点）。
混在一起是排查时最常见的误判来源——本地有 SID 不等于 YouTube 不把你当机器人。
"""

from __future__ import annotations

import http.cookiejar
import re
from pathlib import Path
from urllib.parse import urlsplit

from ..config import Settings
from . import http as transport
from . import proxy
from .errors import explain_error

GOOGLE_AUTH = ("SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID")
# YouTube 认这一套。注意 __Secure-3PSID 单独出现不算登录——Chrome 未登录访问也会拿到它，
# 早期版本把任何 __Secure-* 都当成「已登录」，结果排查时被这条假阳性带偏很久。
YOUTUBE_AUTH = ("SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO")
VISITOR_ONLY = (
    "VISITOR_INFO1_LIVE",
    "VISITOR_PRIVACY_METADATA",
    "__Secure-YENID",
    "__Secure-YNID",
    "__Secure-BUCKET",
    "__Secure-ROLLOUT_TOKEN",
    "PREF",
    "SOCS",
    "YSC",
    "GPS",
)


def cookie_file(cfg: Settings) -> str | None:
    """用户自备的 cookies.txt 优先（难啃平台必需），否则用数据目录里那份。"""
    configured = cfg.cookies_file
    if configured is not None and configured.exists():
        return str(configured)
    default = cfg.cookies_path
    return str(default) if default.exists() else None


def cookie_rows(path: Path) -> list[tuple[str, str]]:
    """(domain, name) 列表。Netscape 格式里 HttpOnly 行带 `#HttpOnly_` 前缀，不能当注释丢掉。"""
    rows: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 6:
            rows.append((fields[0].lstrip("."), fields[5]))
    return rows


def summary(cfg: Settings, path: str | Path | None = None) -> dict:
    """分开报两件事：浏览器登录过没有、YouTube 认不认。"""
    discovered = str(path) if path else cookie_file(cfg)
    if not discovered:
        return {"present": False}
    target = Path(discovered)
    if not target.exists():
        return {"present": False, "path": discovered}
    try:
        rows = cookie_rows(target)
    except OSError as exc:
        return {"present": True, "path": discovered, "error": str(exc)}

    google = {
        name
        for domain, name in rows
        if domain.endswith("google.com") or domain.endswith("google.com.hk") or domain.endswith("google.co.jp")
    }
    youtube = {name for domain, name in rows if "youtube" in domain}
    google_auth = sorted(google.intersection(GOOGLE_AUTH))
    youtube_auth = sorted(youtube.intersection(YOUTUBE_AUTH))
    visitor = sorted(youtube.intersection(VISITOR_ONLY))

    hint = ""
    if not youtube_auth:
        if google_auth:
            hint = (
                "有 Google 会话，但 youtube.com 名下缺 SID/HSID/SSID/APISID/SAPISID/LOGIN_INFO 这几个"
                "HttpOnly 认证 cookie——YouTube 依旧当你是访客。重导时要选「包含 HttpOnly / 全部 domain」，"
                "并在已经登录、正在播放视频的那个窗口里导出。"
                f"（当前 youtube 名下只有访客标记：{', '.join(visitor[:4]) or '无'}）"
            )
        else:
            hint = "没有登录态，只是访客 cookies。"
    return {
        "present": True,
        "path": discovered,
        "lines": len(rows),
        "logged_in": bool(youtube_auth),
        "auth_names": youtube_auth[:6],
        "google_session": bool(len(google_auth) >= 3),
        "hint": hint,
    }


def login_state(
    cfg: Settings, url: str = "https://www.youtube.com/", path: str | Path | None = None
) -> dict:
    """拿 cookies 真打一次站点，看服务端认不认——比任何本地猜测都可靠。"""
    discovered = str(path) if path else cookie_file(cfg)
    if not discovered or not Path(discovered).exists():
        return {"ok": False, "error": "没有 cookies.txt"}
    jar = http.cookiejar.MozillaCookieJar(discovered)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"cookies.txt 读不出来：{exc}"}
    host = urlsplit(url).hostname or ""
    cookies = {c.name: c.value for c in jar if host.endswith(c.domain.lstrip(".")) or c.domain == ""}
    try:
        with transport.client(cfg, timeout=15) as session:
            response = session.get(url, cookies=cookies)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": explain_error(exc, url, cfg)}
    match = re.search(r'"LOGGED_IN":(true|false)', response.text)
    return {
        "ok": response.status_code < 400,
        "status": response.status_code,
        "logged_in": (match.group(1) == "true") if match else None,
        "cookies_sent": len(cookies),
        "via": proxy.current() or "直连",
    }
