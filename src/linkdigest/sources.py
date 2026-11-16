"""采集层：把任意链接归一成「一个音频文件或一段正文」。

设计原则：视频和音频走同一条路 —— 只取音轨，永不抓帧、永不做画面理解。
失败降级顺序固定，不做无边界重试。
"""

from __future__ import annotations

import hashlib
import html as htmllib
import os
import re
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit

import httpx

from .config import settings
from .models import SourceMeta

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".mp4", ".webm", ".mkv", ".mov"}
AUDIO_URL_RE = re.compile(r"https?://[^\s\"'<>]+?\.(?:mp3|m4a|aac|ogg|wav)(?:\?[^\s\"'<>]*)", re.I)

# 用户从终端/文档里复制链接时，常带上 shell 转义（\? \= \&）或 JSON 转义（\/），
# 还会夹着整句说明文字。这两条正则负责把链接还原干净。
URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'（）【】「」《》]+", re.I)
SHELL_ESCAPES = re.compile(r"\\([?&=%#/:.~+=@!$'\"()\[\]{}*;|<>^,-])")
TRAIL_PUNCT = ").,;:!?。，、；：！？）】」》\"'"
BARE_DOMAIN = re.compile(r"^[\w-]+(?:\.[\w-]+)+(?:[/?#].*)?$")

# Spotify 链接有六种写法（/episode /show /track、带 /intl-xx/ 前缀、带 ?si= 追踪串），
# 先把 kind + 22 位 base62 ID 抓出来，后面全部按 ID 说话。
SPOTIFY_ID = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z-]+/)?(?P<kind>episode|show|track|album|playlist|artist)"
    r"/(?P<id>[A-Za-z0-9]{22})",
    re.I,
)
APPLE_PODCAST_ID = re.compile(r"id(?P<show>\d{6,})(?:.*?[?&]i=(?P<episode>\d{8,}))?", re.S)
ITUNES_SEARCH = "https://itunes.apple.com/search"
ITUNES_LOOKUP = "https://itunes.apple.com/lookup"

PLATFORMS = {
    # —— 国际 ——
    "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    "vimeo": ("vimeo.com",),
    "x": ("twitter.com", "x.com"),
    "tiktok": ("tiktok.com",),
    "soundcloud": ("soundcloud.com", "snd.sc"),
    "spotify": ("spotify.com", "spotify.link"),
    "apple_podcast": ("podcasts.apple.com", "podcasts.apple.co"),
    # —— 国内 ——
    "bilibili": ("bilibili.com", "b23.tv"),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "kuaishou": ("kuaishou.com", "gifshow.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "wechat": ("mp.weixin.qq.com",),
    "weibo": ("weibo.com", "weibo.cn", "t.cn"),
    "zhihu": ("zhihu.com",),
    "xiaoyuzhou": ("xiaoyuzhoufm.com",),
    "ximalaya": ("ximalaya.com",),
    "netease_music": ("music.163.com", "163cn.tv"),
    "qq_music": ("y.qq.com", "c6.y.qq.com"),
}

# 有专门解析路线、不走 yt-dlp 的平台
PODCAST_PLATFORMS = {"spotify", "apple_podcast"}


@dataclass(slots=True)
class IngestResult:
    source: SourceMeta
    media_file: Path | None = None
    text: str | None = None
    images: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def detect_platform(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for name, domains in PLATFORMS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
    if not host:
        return "local"
    if "feed" in url or url.endswith((".xml", ".rss")):
        return "rss"
    return "generic"


def classify(url: str) -> str:
    """kind: video | audio | article | local"""
    path = Path(url).expanduser()
    if path.exists() and path.is_file():
        return "local"
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in AUDIO_EXT:
        return "video" if suffix in {".mp4", ".webm", ".mkv", ".mov"} else "audio"
    platform = detect_platform(url)
    if platform in {"youtube", "bilibili", "vimeo", "x", "douyin", "xiaohongshu",
                    "tiktok", "kuaishou", "soundcloud", "weibo"}:
        return "video"
    if platform in {"wechat", "zhihu"}:
        return "article"
    if platform in {"rss", "xiaoyuzhou", "spotify", "apple_podcast", "ximalaya", "netease_music"}:
        return "audio"
    # 未知域名默认按文章处理：正文抽取比 yt-dlp 泛解析更可能成功
    return "article"


def canonicalize(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.hostname in {"youtu.be"}:
        return f"https://www.youtube.com/watch?v={parts.path.strip('/')}"
    if parts.hostname and parts.hostname.endswith("youtube.com") and parts.path == "/watch":
        m = re.search(r"[?&]v=([\w-]{6,})", url)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
    if parts.hostname and parts.hostname.endswith("bilibili.com"):
        m = re.search(r"(BV[0-9A-Za-z]{8,}|av\d+)", url)
        if m:
            return f"https://www.bilibili.com/video/{m.group(1)}"
    # Spotify 的 ?si= 是分享追踪串、/intl-xx/ 是语言前缀，同一集会有多种写法，
    # 不清掉就会算出不同的 bundle_id，同一内容重复入库。
    if host.endswith("spotify.com"):
        m = SPOTIFY_ID.search(url)
        if m:
            return f"https://open.spotify.com/{m.group('kind')}/{m.group('id')}"
    return url.split("#")[0]


def bundle_id_for(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def normalize_input(raw: str) -> str:
    """把用户实际粘贴的内容还原成一个干净链接或本地路径。

    处理的真实情况：终端复制来的 `?v\\=xxx`、JSON 里的 `https:\\/\\/`、
    带书名号/引号的整句「看这个 https://… 挺好」、以及末尾的中文标点。
    """
    text = unicodedata.normalize("NFKC", (raw or "").strip())
    text = text.strip().strip("'\"`").strip()
    if not text:
        raise ValueError("输入为空")

    local = Path(text).expanduser()
    if local.exists():
        return str(local)

    text = htmllib.unescape(text)
    text = SHELL_ESCAPES.sub(r"\1", text)
    text = text.replace("\\", "").replace("\u200b", "").replace("\u2060", "")

    m = URL_IN_TEXT.search(text)
    if m:
        url = m.group(0).rstrip(TRAIL_PUNCT)
    else:
        url = text.rstrip(TRAIL_PUNCT)
        if BARE_DOMAIN.match(url):
            url = "https://" + url

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"无法识别的输入：{raw[:80]}（需要 http/https 链接或已存在的本地文件路径）")
    return url


class DownloadFailure(RuntimeError):
    """采集失败，消息面向用户：说清原因和下一步，不用「未找到文件」这种废信息。"""


def explain_error(exc: BaseException | str, url: str) -> str:
    """把底层异常翻成一句人能动手的中文。"""
    msg = str(exc)
    low = msg.lower()
    host = urlsplit(url).hostname or url

    def has(*words: str) -> bool:
        return any(w in low for w in words)

    if has("[drm]", "drm protection", "widevine", "playready", "fairplay"):
        return (f"{host} 的音轨带 DRM 加密（Widevine/FairPlay），不属于可下载范畴："
                "Spotify、Apple Music、网易云、QQ音乐的音乐内容都在这一列，任何工具都解不开。"
                "播客例外——播客的音源在节目自己的 RSS 里，本程序会自动去源头取；"
                "如果这条链接是音乐单曲，只能手动上传音频文件。")
    if has("tunnel connection failed", "proxyerror", "proxy error", "407"):
        return (f"网络不可达：到 {host} 的连接被本机代理拒绝（Proxy 502）。"
                "HTTP_PROXY/HTTPS_PROXY 指向的代理没有放行这个域名——"
                "在能直连该站点的环境启动服务，或在代理里放行它。")
    if has("requested format is not available", "n challenge", "only images are available"):
        runtime = settings.js_runtime or "未检测到"
        return (f"{host} 只返回了封面图，拿不到音轨：YouTube 的 n 挑战没解开。"
                f"需要 JS 运行时（当前 {runtime}）和 yt-dlp-ejs 组件。"
                "确认 `.venv/bin/pip install yt-dlp-ejs`，且 node/deno 可用；"
                "如果 NODE_OPTIONS 里塞了 --require 的 shim（IDE、沙箱常这么干），"
                "node 会在权限模式下一启动就退出，本程序已自动摘掉该变量。")
    if has("sign in to confirm", "not a bot", "confirm you're not a bot", "cookies-from-browser"):
        info = cookies_summary()
        if info.get("present") and info.get("logged_in"):
            return (f"{host} 要求登录校验，但 cookies.txt 里确实有登录态——"
                    "这次是出口 IP 被判定成机器人。换一个出口（家宽/住宅代理）重试，"
                    "或降低请求频率；带登录态反复失败时，是 IP 的问题，不是 cookies 的问题。")
        state = ""
        if info.get("present"):
            state = f"\n当前 {info.get('path')}：{info.get('hint') or '没有可用的登录态。'}"
        else:
            state = f"\n当前没有 cookies.txt（期望位置 {settings.data_dir / 'cookies.txt'}）。"
        return (f"{host} 要求登录校验（平台把当前出口 IP 判成了机器人）。"
                "有效的做法只有两个：\n"
                "  1) 导出 cookies.txt：在**已登录且正在播放视频**的窗口里导出（要用能带上 HttpOnly 的工具，"
                "如 Get cookies.txt LOCALLY），存到 "
                f"{settings.data_dir / 'cookies.txt'}，或用 COOKIES_FILE 指路径；\n"
                "  2) 复用浏览器登录态：.env 写 COOKIES_FROM_BROWSER=chrome（或 safari/edge），"
                "并给终端「完全磁盘访问权限」，重启服务。"
                f"{state}")
    if has("video unavailable", "private video", "has been removed", "members-only",
           "this video is not available", "account has been terminated"):
        return f"内容不可用：{host} 上的这条内容被删除、私有或仅限会员。"
    if has("not available in your country", "geo restricted", "geo-restriction"):
        return f"地区限制：{host} 判定当前网络所在地区不可播放，需要可用地区的出口。"
    if has("unsupported url"):
        return f"不支持这个链接：{host} 的页面结构不在已接入的解析范围内，可改为上传音频文件。"
    if has("http error 403", "403 forbidden"):
        return f"被平台拒绝（403）：{host} 判定为自动抓取，通常需要 cookies 或降低请求频率。"
    if has("http error 404", "404 not found"):
        return f"链接不存在（404）：{host} 上没有这个页面，检查链接是否完整。"
    if has("timed out", "timeout"):
        return f"连接超时：{host} 没在限定时间内响应，网络不稳或站点在限速。"
    if has("name or service not known", "nodename nor servname", "temporary failure in name resolution",
           "getaddrinfo", "connection reset", "connection refused", "network is unreachable",
           "ssl", "certificate"):
        return f"网络层失败：连不上 {host}（{low.splitlines()[0][:90]}）。检查 DNS、代理和本机网络。"
    if has("no video formats found", "requested format not available"):
        return f"没有可用音轨：{host} 这个页面里没有可直接下载的音频格式。"
    if has("larger than max-filesize", "max_filesize"):
        return "文件超过体积上限（MAX_DOWNLOAD_MB），调大后重试。"
    first = re.sub(r"\x1b\[[0-9;]*m", "", msg).strip().splitlines()
    return f"采集失败：{first[0][:200] if first else '未知原因'}"


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
        clean = [re.sub(r"\x1b\[[0-9;]*m", "", x).strip() for x in self.lines if x.strip()]
        return " / ".join(clean[-n:]) if clean else ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _clean_node_env():
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


def _download(url: str, workdir: Path, progress: Callable[[float], None] | None = None) -> tuple[Path, dict]:
    from yt_dlp import YoutubeDL

    # 先花几秒确认能不能连上：连不上就没必要让 yt-dlp 反复重试拖上几分钟。
    probe = reachability(url, timeout=min(8.0, settings.fetch_timeout))
    if not probe.get("ok"):
        raise DownloadFailure(probe.get("error") or f"连不上 {probe.get('host')}")

    outtmpl = str(workdir / "source.%(ext)s")
    state: dict = {"filename": None}
    logger = _YdlLogger()

    def hook(d):
        if d.get("status") == "downloading" and progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(min(0.99, (d.get("downloaded_bytes") or 0) / total))
        elif d.get("status") == "finished":
            state["filename"] = d.get("filename")

    opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "retries": 1,
        "fragment_retries": 1,
        "socket_timeout": 15,
        "max_filesize": settings.max_download_mb * 1024 * 1024,
        "progress_hooks": [hook],
        "logger": logger,
    }
    cookies_path = werkzeug_safe_cookies()
    if cookies_path:
        opts["cookiefile"] = cookies_path
    elif settings.cookies_from_browser:
        opts["cookiesfrombrowser"] = (settings.cookies_from_browser,)

    # YouTube 的 n 挑战要在 JS 里跑：没有运行时，画质/音轨都拿不到，
    # 只返回一串 storyboard 封面图，然后报「Requested format is not available」。
    if settings.js_runtime:
        runtime = "deno" if "deno" in Path(settings.js_runtime).name else "node"
        opts["js_runtimes"] = {runtime: {"path": settings.js_runtime}}

    try:
        with _clean_node_env(), YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001 - 统一转成可读原因
        raise DownloadFailure(explain_error(exc, url)) from exc

    if not info:
        raise DownloadFailure(explain_error(logger.tail() or "解析结果为空", url))

    candidates = [p for p in sorted(workdir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
                  if not p.name.endswith((".part", ".ytdl"))]
    if not candidates:
        extractor = info.get("extractor_key") or info.get("extractor") or "generic"
        formats = info.get("formats") or []
        detail = logger.tail() or f"解析器 {extractor} 返回了元数据但没有可下载格式（formats={len(formats)}）"
        raise DownloadFailure(explain_error(detail, url) + f"〔解析器 {extractor}〕")
    if candidates[0].stat().st_size < 1024:
        raise DownloadFailure(
            f"只下到 {candidates[0].stat().st_size} 字节的占位文件，通常是平台返回了错误页或验证页。"
            + (f"（{logger.tail()}）" if logger.tail() else "")
        )
    return candidates[0], info


PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def current_proxy() -> str | None:
    """当前进程实际会用的代理（每次现读环境，避免被启动时的快照骗到）。"""
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(key)
        if value:
            return value
    return None


def _force_env_proxy(proxy: str) -> None:
    for key in PROXY_KEYS:
        os.environ[key] = proxy


def _clear_env_proxy() -> None:
    for key in PROXY_KEYS:
        os.environ.pop(key, None)


def _normalize_proxy(value: str) -> str:
    value = value.strip()
    return value if "://" in value else f"http://{value}"


def proxy_candidates() -> list[str]:
    """所有值得一试的出口：环境变量里的（可能已失效）+ macOS 系统代理。"""
    out: list[str] = []
    for raw in (current_proxy(), settings.system_proxy):
        if not raw:
            continue
        proxy = _normalize_proxy(raw)
        if proxy not in out:
            out.append(proxy)
    return out


def _probe(anchor: str, timeout: float, proxy: str | None) -> dict:
    """单次探测。proxy=None 表示绕过环境变量直连。"""
    import time

    started = time.time()
    try:
        kwargs = {"timeout": timeout, "follow_redirects": True, "headers": {"User-Agent": UA}}
        client = httpx.Client(proxy=proxy, **kwargs) if proxy else httpx.Client(trust_env=False, **kwargs)
        with client:
            resp = client.get(anchor)
        return {"ok": resp.status_code < 400, "status": resp.status_code,
                "elapsed_ms": int((time.time() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        message = explain_error(exc, anchor)
        if proxy and ("超时" in message or "网络层失败" in message):
            parsed = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
            message += (f"（失败的是到代理 {proxy} 的连接：先确认它在监听 "
                        f"`nc -z {parsed.hostname or '127.0.0.1'} {parsed.port or 7890}`）")
        return {"ok": False, "error": message, "elapsed_ms": int((time.time() - started) * 1000)}


def reachability(url: str, timeout: float = 8.0) -> dict:
    """先直连，再依次试各个候选代理；谁通用谁，并把结果写回环境变量。

    真实场景逼出来的顺序：很多人机器上开着 TUN/增强模式，直连就通了，
    而 shell 里那个 `HTTP_PROXY=127.0.0.1:7987` 早就是残留，端口根本没监听；
    也有反过来的：直连被墙、只有代理能出，而代理只开在「系统代理」里、
    不往环境变量里导。三种情况都必须能自己走通。
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    anchor = f"{parts.scheme or 'https'}://{parts.netloc or host}/"
    candidates = proxy_candidates()
    attempts: list[dict] = []

    direct = _probe(anchor, timeout, None)
    direct["via"] = "直连"
    attempts.append(direct)
    if direct["ok"]:
        note = ""
        stale = [c for c in candidates if c == current_proxy()]
        if stale:
            _clear_env_proxy()
            note = f"已忽略失效代理 {stale[0]}，本次走直连"
        return {"host": host, "ok": True, "mode": "direct", "status": direct.get("status"),
                "elapsed_ms": direct["elapsed_ms"], "attempts": attempts,
                "proxy": {}, "candidates": candidates, "note": note}

    errors = [f"直连失败：{direct.get('error')}"]
    for candidate in candidates:
        proxied = _probe(anchor, timeout, candidate)
        proxied["via"] = f"代理 {candidate}"
        attempts.append(proxied)
        if proxied["ok"]:
            _force_env_proxy(candidate)
            origin = "系统代理" if candidate == settings.system_proxy else "环境变量"
            return {"host": host, "ok": True, "mode": "proxy", "status": proxied.get("status"),
                    "elapsed_ms": proxied["elapsed_ms"], "attempts": attempts,
                    "proxy": {k: os.environ.get(k) for k in PROXY_KEYS if os.environ.get(k)},
                    "candidates": candidates,
                    "note": f"直连不通，已切到{origin} {candidate}"}
        errors.append(f"代理 {candidate} 也失败：{proxied.get('error')}")

    return {"host": host, "ok": False, "mode": "none", "error": "；".join(errors), "attempts": attempts,
            "elapsed_ms": int(sum(a.get("elapsed_ms", 0) for a in attempts)),
            "proxy": {k: os.environ.get(k) for k in PROXY_KEYS if os.environ.get(k)},
            "candidates": candidates}


def werkzeug_safe_cookies() -> str | None:
    """支持用户自备 cookies.txt（难啃平台必需）。"""
    candidate = os.getenv("COOKIES_FILE")
    if candidate and Path(candidate).exists():
        return candidate
    default = settings.data_dir / "cookies.txt"
    return str(default) if default.exists() else None


GOOGLE_AUTH = ("SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID")
# YouTube 认这一套。注意 __Secure-3PSID 单独出现不算登录——Chrome 未登录访问也会拿到它，
# 早期版本把任何 __Secure-* 都当成「已登录」，结果排查时被这条假阳性带偏很久。
YOUTUBE_AUTH = ("SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO")
VISITOR_ONLY = ("VISITOR_INFO1_LIVE", "VISITOR_PRIVACY_METADATA", "__Secure-YENID", "__Secure-YNID",
                "__Secure-BUCKET", "__Secure-ROLLOUT_TOKEN", "PREF", "SOCS", "YSC", "GPS")


def _cookie_rows(path: Path) -> list[tuple[str, str]]:
    """(domain, name) 列表。Netscape 格式里 HttpOnly 行带 `#HttpOnly_` 前缀，不能当注释丢掉。"""
    rows: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 6:
            rows.append((fields[0].lstrip("."), fields[5]))
    return rows


def cookies_summary(path: str | Path | None = None) -> dict:
    """cookies.txt 到底能不能过登录校验——分开报两件事：浏览器登录过没有、YouTube 认不认。"""
    discovered = str(path) if path else werkzeug_safe_cookies()
    if not discovered:
        return {"present": False}
    target = Path(discovered)
    if not target.exists():
        return {"present": False, "path": discovered}
    try:
        rows = _cookie_rows(target)
    except OSError as exc:
        return {"present": True, "path": discovered, "error": str(exc)}

    google = {name for domain, name in rows if domain.endswith("google.com")
              or domain.endswith("google.com.hk") or domain.endswith("google.co.jp")}
    youtube = {name for domain, name in rows if "youtube" in domain}
    google_auth = sorted(google.intersection(GOOGLE_AUTH))
    youtube_auth = sorted(youtube.intersection(YOUTUBE_AUTH))
    visitor = sorted(youtube.intersection(VISITOR_ONLY))

    hint = ""
    if not youtube_auth:
        if google_auth:
            hint = ("有 Google 会话，但 youtube.com 名下缺 SID/HSID/SSID/APISID/SAPISID/LOGIN_INFO 这几个"
                    "HttpOnly 认证 cookie——YouTube 依旧当你是访客。重导时要选「包含 HttpOnly / 全部 domain」，"
                    "并在已经登录、正在播放视频的那个窗口里导出。"
                    f"（当前 youtube 名下只有访客标记：{', '.join(visitor[:4]) or '无'}）")
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


def login_state(url: str = "https://www.youtube.com/", path: str | Path | None = None) -> dict:
    """拿 cookies 真打一次站点，看服务端认不认——比任何本地猜测都可靠。"""
    import http.cookiejar

    discovered = str(path) if path else werkzeug_safe_cookies()
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
        with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": UA}) as client:
            resp = client.get(url, cookies=cookies)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": explain_error(exc, url)}
    body = resp.text
    match = re.search(r'"LOGGED_IN":(true|false)', body)
    return {
        "ok": resp.status_code < 400,
        "status": resp.status_code,
        "logged_in": (match.group(1) == "true") if match else None,
        "cookies_sent": len(cookies),
        "via": current_proxy() or "直连",
    }


def _fetch_html(url: str) -> str:
    with httpx.Client(timeout=settings.fetch_timeout, follow_redirects=True, headers={"User-Agent": UA}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def _ingest_rss(url: str) -> IngestResult:
    import feedparser

    feed = feedparser.parse(url)
    if not feed.entries:
        raise RuntimeError("RSS 中没有条目")
    entry = feed.entries[0]
    audio_url = next(
        (e.get("href") for e in entry.get("enclosures", []) if (e.get("type") or "").startswith("audio")),
        None,
    ) or next((e.get("href") for e in entry.get("links", []) if "audio" in (e.get("type") or "")), None)
    meta = SourceMeta(
        kind="audio",
        platform="rss",
        url=url,
        canonical_url=entry.get("link") or url,
        title=entry.get("title"),
        author=feed.feed.get("title"),
        published_at=entry.get("published"),
        extractor=f"feedparser/{feed.version or 'rss'}",
        extra={"audio_url": audio_url},
    )
    if not audio_url:
        raise RuntimeError("该 RSS 条目没有音频 enclosure")
    return IngestResult(source=meta, notes=["RSS 直取音频，跳过解析"])


def _ingest_article(url: str) -> IngestResult:
    import trafilatura

    html = _fetch_html(url)
    extracted = trafilatura.extract(
        html, include_comments=False, include_tables=True, include_images=True,
        output_format="json", with_metadata=True, url=url,
    )
    if not extracted:
        raise RuntimeError("正文抽取为空（可能需要 cookie 或页面为反爬壳）")
    import json

    data = json.loads(extracted)
    title = data.get("title")
    author = data.get("author")
    text = data.get("text") or ""
    images = data.get("image") and [data["image"]] or []
    if len(text) < 120:
        raise RuntimeError(f"正文过短（{len(text)} 字），疑似抓取失败")
    meta = SourceMeta(
        kind="article",
        platform="wechat" if "mp.weixin.qq.com" in url else "article",
        url=url,
        canonical_url=data.get("url") or url,
        title=title,
        author=author,
        published_at=data.get("date"),
        extractor="trafilatura",
        extra={"images": images},
    )
    return IngestResult(source=meta, text=text, images=images)


def _ingest_html_audio(url: str) -> IngestResult | None:
    """播客网页（小宇宙等）：直接从 HTML 里捞音频直链。"""
    try:
        html = _fetch_html(url)
    except Exception:
        return None
    match = AUDIO_URL_RE.search(html)
    if not match:
        return None
    audio_url = match.group(0)
    title = None
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
    meta = SourceMeta(
        kind="audio",
        platform=detect_platform(url),
        url=url,
        canonical_url=url,
        title=title,
        extractor="html-audio-scrape",
        extra={"audio_url": audio_url},
    )
    return IngestResult(source=meta, notes=["从页面 HTML 直接提取音频直链"])


def _stream_to(url: str, target: Path, retry: bool = True) -> Path:
    """下载一个直链到本地。所有「已经有直链」的路径都走这里，不再各写一遍。

    失败时先让 reachability() 重新挑一次出口再重试一次：环境变量里的代理经常是
    残留（端口没监听），而真正能出网的是直连或系统代理。只重试一次，不无限重试。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.stream("GET", url, timeout=settings.fetch_timeout, follow_redirects=True,
                          headers={"User-Agent": UA}) as resp:
            resp.raise_for_status()
            with target.open("wb") as fh:
                for chunk in resp.iter_bytes(1 << 16):
                    fh.write(chunk)
    except Exception as exc:  # noqa: BLE001
        if retry:
            probe = reachability(url, timeout=min(6.0, settings.fetch_timeout))
            if probe.get("ok"):
                return _stream_to(url, target, retry=False)
            raise DownloadFailure(probe.get("error") or explain_error(exc, url)) from exc
        raise DownloadFailure(explain_error(exc, url)) from exc
    size = target.stat().st_size
    if size < 1024:
        target.unlink(missing_ok=True)
        raise DownloadFailure(f"只下到 {size} 字节的占位文件：{url} 可能重定向到了错误页或要求登录。")
    if size > settings.max_download_mb * 1024 * 1024:
        target.unlink(missing_ok=True)
        raise DownloadFailure("文件超过体积上限（MAX_DOWNLOAD_MB），调大后重试。")
    return target


def _audio_suffix(url: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in AUDIO_EXT - {".mp4", ".webm", ".mkv", ".mov"} else ".mp3"


# —— 播客解析：绕过播放器，去源头拿文件 ————————————————————————————
#
# 关键事实：Spotify / Apple Podcasts 的网页本身永远给不出音轨。
# Spotify 是 Widevine DRM 加密流，Apple 只有播放器壳，页面里连 mp3 地址都没有。
# 但播客生态里「平台只是分发渠道，RSS 才是源头」——同一集音频在节目自己的
# feed 里有公开的 <enclosure> 直链。所以固定三级路线：
#
#   1) 页面 → 元数据（标题 / 节目名 / 封面 / 时长）   oEmbed、iTunes Lookup，免密钥
#   2) 元数据 → 反查原始 RSS                         iTunes Search API，免密钥
#   3) RSS → enclosure 直链 → 下载
#
# 死角只有一个：Spotify 独家（Exclusive）节目没有公开 RSS。这种情况必须明确
# 报错让人去上传音频，不能拿简介或 30 秒试听凑一份假报告。


@dataclass(slots=True)
class PodcastTarget:
    title: str | None = None
    show: str | None = None
    published_at: str | None = None
    duration_sec: float | None = None
    thumbnail: str | None = None
    audio_url: str | None = None
    feed_url: str | None = None
    route: str = ""


def _http_json(url: str, timeout: float = 12.0, retry: bool = True) -> dict:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": UA, "Accept": "application/json"}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:  # noqa: BLE001
        if retry:
            probe = reachability(url, timeout=min(6.0, timeout))
            if probe.get("ok"):
                return _http_json(url, timeout=timeout, retry=False)
            raise DownloadFailure(probe.get("error") or explain_error(exc, url)) from exc
        raise DownloadFailure(explain_error(exc, url)) from exc


def _title_key(title: str | None) -> str:
    text = unicodedata.normalize("NFKC", title or "").lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _title_score(a: str | None, b: str | None) -> float:
    """标题相似度。播客单集标题跨平台常被改写（加 ft.、加集号、截断），必须模糊比。"""
    ka, kb = _title_key(a), _title_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    if ka in kb or kb in ka:
        return 0.95
    return SequenceMatcher(None, ka, kb).ratio()


def _itunes_episodes(term: str, limit: int = 12) -> list[dict]:
    data = _http_json(f"{ITUNES_SEARCH}?term={quote(term)}&entity=podcastEpisode&media=podcast&limit={limit}")
    return list(data.get("results") or [])


def _itunes_feeds(term: str, limit: int = 6) -> list[str]:
    data = _http_json(f"{ITUNES_SEARCH}?term={quote(term)}&entity=podcast&media=podcast&limit={limit}")
    feeds: list[str] = []
    for row in data.get("results") or []:
        feed = row.get("feedUrl")
        if feed and feed not in feeds:
            feeds.append(feed)
    return feeds


def _entry_audio(entry: dict) -> str | None:
    for enc in entry.get("enclosures") or []:
        href = enc.get("href")
        if href and ((enc.get("type") or "").startswith("audio") or href.split("?")[0].endswith((".mp3", ".m4a", ".ogg", ".opus"))):
            return href
    for link in entry.get("links") or []:
        if link.get("href") and "audio" in (link.get("type") or ""):
            return link["href"]
    return None


def _feed_match(feed_url: str, title: str | None, threshold: float = 0.6) -> dict | None:
    """在 feed 里按标题找那一集。取不到就直接返回 None，不猜最新一集。"""
    import feedparser

    feed = feedparser.parse(feed_url)
    best, best_score = None, 0.0
    for entry in feed.entries or []:
        score = _title_score(title, entry.get("title"))
        if score > best_score:
            best, best_score = entry, score
    if best is None or best_score < threshold or not _entry_audio(best):
        return None
    return best


def _expand_short(url: str) -> str:
    """spotify.link / b23.tv 这类短链先跟一次重定向，拿到真实页面。"""
    host = (urlsplit(url).hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in ("spotify.link", "spotify.app.link", "apple.co")):
        return url
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
            return str(client.get(url).url) or url
    except Exception:  # noqa: BLE001 - 短链失败就按原链接继续，后面的解析会给出具体原因
        return url


def _spotify_target(url: str) -> PodcastTarget:
    match = SPOTIFY_ID.search(url)
    if not match:
        raise DownloadFailure(
            "没能从这条 Spotify 链接里认出内容 ID。支持 /episode/、/show/、/track/ 三种页面，"
            "确认链接是 open.spotify.com 下的完整地址。"
        )
    kind = match.group("kind").lower()
    if kind == "show":
        raise DownloadFailure(
            "这是 Spotify 的节目主页，不是一个单集，无法确定要解析哪一集。"
            "打开具体某一集，复制带 /episode/ 的链接（手机端点「分享 → 复制链接」即可）。"
        )
    if kind in {"track", "album", "playlist", "artist"}:
        raise DownloadFailure(
            f"Spotify 的 {kind} 属于音乐内容，音轨是 Widevine 加密流，任何工具都拿不到——"
            "这不是解析器能力问题。本程序处理的是播客和可下载音视频，音乐请手动上传音频文件。"
        )

    meta = _http_json(f"https://open.spotify.com/oembed?url={quote(url, safe='')}")
    target = PodcastTarget(
        title=(meta.get("title") or "").strip() or None,
        thumbnail=meta.get("thumbnail_url"),
        route="spotify-oembed",
    )
    if not target.title:
        raise DownloadFailure("Spotify oEmbed 没返回标题，这条单集可能已下架或被设为私密。")

    # 第一跳：按单集标题直接在播客库里搜（iTunes Search 覆盖了绝大多数公开播客）
    best, score = None, 0.0
    for row in _itunes_episodes(target.title):
        current = _title_score(target.title, row.get("trackName"))
        if current > score:
            best, score = row, current
    if best and score >= 0.72:
        audio = best.get("episodeUrl") or best.get("previewUrl")
        if audio:
            target.audio_url = audio
            target.title = best.get("trackName") or target.title
            target.show = best.get("collectionName")
            target.feed_url = best.get("feedUrl")
            target.published_at = best.get("releaseDate")
            millis = best.get("trackTimeMillis")
            target.duration_sec = millis / 1000 if isinstance(millis, (int, float)) else None
            target.route = "spotify-oembed → itunes-search → enclosure"
            return target

    # 第二跳：标题被平台改写过时，退到「搜节目 → 解析 feed → 在 feed 里模糊匹配」
    for feed_url in _itunes_feeds(target.title)[:4]:
        entry = _feed_match(feed_url, target.title)
        if not entry:
            continue
        target.audio_url = _entry_audio(entry)
        target.title = entry.get("title") or target.title
        target.feed_url = feed_url
        target.published_at = entry.get("published")
        target.route = "spotify-oembed → itunes-podcast → rss-enclosure"
        return target

    raise DownloadFailure(
        f"已取到元数据《{target.title}》，但在公开播客库里没找到对应音源。"
        "最可能是 Spotify 独家节目（Exclusive）——这类节目没有公开 RSS，音频只存在于 Spotify 的加密流里。"
        "可行的下一步：在 Spotify 客户端下载该集后导出音频上传，或用其它平台（Apple Podcasts / 小宇宙 / 官网）"
        "上同一个节目的公开链接。"
    )


def _apple_target(url: str) -> PodcastTarget:
    match = APPLE_PODCAST_ID.search(url)
    if not match:
        raise DownloadFailure("Apple Podcasts 链接里没有找到 id，确认形式为 /podcast/名称/id123456789?i=1000600000000。")
    episode_id, show_id = match.group("episode"), match.group("show")

    def from_row(row: dict) -> PodcastTarget | None:
        audio = row.get("episodeUrl") or row.get("previewUrl")
        if not audio:
            return None
        millis = row.get("trackTimeMillis")
        return PodcastTarget(
            title=row.get("trackName"),
            show=row.get("collectionName"),
            published_at=row.get("releaseDate"),
            duration_sec=millis / 1000 if isinstance(millis, (int, float)) else None,
            thumbnail=row.get("artworkUrl600") or row.get("artworkUrl100"),
            audio_url=audio,
            feed_url=row.get("feedUrl"),
            route="apple-lookup → enclosure",
        )

    if episode_id:
        data = _http_json(f"{ITUNES_LOOKUP}?id={episode_id}&entity=podcastEpisode&media=podcast")
        for row in data.get("results") or []:
            target = from_row(row)
            if target:
                return target

    # 只给了节目 id：iTunes lookup 会返回该节目最近一集，取它并在 notes 里说明
    data = _http_json(f"{ITUNES_LOOKUP}?id={show_id}&entity=podcastEpisode&media=podcast&limit=1")
    for row in data.get("results") or []:
        target = from_row(row)
        if target:
            target.route = "apple-lookup → 节目最新一集"
            return target
    raise DownloadFailure(f"Apple Podcasts 查不到 id={episode_id or show_id} 的音频，节目可能已下架或仅限订阅。")


def _ingest_podcast(url: str, workdir: Path, platform: str) -> IngestResult:
    url = _expand_short(url)
    target = _spotify_target(url) if platform == "spotify" else _apple_target(url)
    if not target.audio_url:
        raise DownloadFailure("解析到了元数据但没有音频直链，换个链接或上传音频文件。")
    media = _stream_to(target.audio_url, workdir / f"source{_audio_suffix(target.audio_url)}")
    meta = SourceMeta(
        kind="audio",
        platform=platform,
        url=url,
        canonical_url=canonicalize(url),
        title=target.title,
        author=target.show,
        published_at=target.published_at,
        duration_sec=target.duration_sec,
        extractor=target.route,
        extra={"thumbnail": target.thumbnail, "audio_url": target.audio_url, "feed_url": target.feed_url},
    )
    notes = [f"音源跳过了播放器，从原始 RSS 取 enclosure：{target.route}"]
    if "最新一集" in target.route:
        notes.append("链接指向节目主页而不是单集，已取最新一集；要精确解析请在链接里带上 ?i=单集ID。")
    return IngestResult(source=meta, media_file=media, notes=notes)


def fetch(url: str, workdir: Path, progress: Callable[[float], None] | None = None) -> IngestResult:
    url = normalize_input(url)
    workdir.mkdir(parents=True, exist_ok=True)
    local = Path(url).expanduser()
    if local.exists() and local.is_file():
        if local.suffix.lower() in {".txt", ".md", ".markdown", ".html", ".htm"}:
            raw = local.read_text(encoding="utf-8", errors="ignore")
            text = raw
            if local.suffix.lower() in {".html", ".htm"}:
                import trafilatura

                text = trafilatura.extract(raw, include_comments=False, include_tables=True) or ""
            meta = SourceMeta(
                kind="article",
                platform="local",
                url=None,
                canonical_url=None,
                title=local.stem,
                extractor="filesystem-text",
                extra={"filename": local.name},
            )
            return IngestResult(source=meta, text=text, notes=["本地文本直接进管道"])
        meta = SourceMeta(
            kind="local",
            platform="local",
            url=None,
            canonical_url=None,
            title=local.stem,
            extractor="filesystem",
            extra={"filename": local.name},
        )
        return IngestResult(source=meta, media_file=local)

    platform = detect_platform(url)
    article_error: str | None = None

    # 播客平台单独一条路：页面给不出音轨，必须去原始 RSS 取。
    if platform in PODCAST_PLATFORMS:
        return _ingest_podcast(url, workdir, platform)

    if platform == "wechat" or (platform == "generic" and not Path(urlsplit(url).path).suffix):
        try:
            return _ingest_article(url)
        except Exception as exc:
            article_error = explain_error(exc, url)

    if platform == "rss":
        result = _ingest_rss(url)
        audio_url = result.source.extra.get("audio_url")
        result.media_file = _stream_to(audio_url, workdir / f"source{_audio_suffix(audio_url)}")
        return result

    scraped = _ingest_html_audio(url) if platform in {"xiaoyuzhou", "ximalaya", "generic"} else None
    if scraped and scraped.source.extra.get("audio_url"):
        audio_url = scraped.source.extra["audio_url"]
        scraped.media_file = _stream_to(audio_url, workdir / f"source{_audio_suffix(audio_url)}")
        if article_error:
            scraped.notes.append(article_error)
        return scraped

    try:
        media, info = _download(url, workdir, progress)
    except DownloadFailure as exc:
        if article_error:
            raise DownloadFailure(f"{article_error}；按音视频解析也不成立：{exc}") from exc
        raise

    meta = SourceMeta(
        kind="video" if platform != "generic" else classify(url),
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
    return IngestResult(source=meta, media_file=media,
                        notes=[article_error] if article_error else [])
