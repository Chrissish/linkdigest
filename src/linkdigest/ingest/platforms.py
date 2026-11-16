"""平台表与正则。纯数据：改支持范围只改这个文件，逻辑在 classify / ingest 里。"""

from __future__ import annotations

import re

# —— 域名 → 平台名 ——
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

VIDEO_PLATFORMS = {
    "youtube",
    "bilibili",
    "vimeo",
    "x",
    "douyin",
    "xiaohongshu",
    "tiktok",
    "kuaishou",
    "soundcloud",
    "weibo",
}
ARTICLE_PLATFORMS = {"wechat", "zhihu"}
AUDIO_PLATFORMS = {"rss", "xiaoyuzhou", "spotify", "apple_podcast", "ximalaya", "netease_music"}
# 有专门解析路线、不走 yt-dlp 的平台
PODCAST_PLATFORMS = {"spotify", "apple_podcast"}
# 页面里能直接捞到音频直链的平台
HTML_AUDIO_PLATFORMS = {"xiaoyuzhou", "ximalaya", "generic"}

MEDIA_EXT = {".mp4", ".webm", ".mkv", ".mov"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"} | MEDIA_EXT
TEXT_EXT = {".txt", ".md", ".markdown", ".html", ".htm"}

AUDIO_URL_RE = re.compile(r"https?://[^\s\"'<>]+?\.(?:mp3|m4a|aac|ogg|wav)(?:\?[^\s\"'<>]*)", re.I)

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
SPOTIFY_OEMBED = "https://open.spotify.com/oembed"

# 用户从终端/文档里复制链接时，常带上 shell 转义（\? \= \&）或 JSON 转义（\/），
# 还会夹着整句说明文字。这几条正则负责把链接还原干净。
URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'（）【】「」《》]+", re.I)
SHELL_ESCAPES = re.compile(r"\\([?&=%#/:.~+=@!$'\"()\[\]{}*;|<>^,-])")
TRAIL_PUNCT = ").,;:!?。，、；：！？）】」》\"'"
BARE_DOMAIN = re.compile(r"^[\w-]+(?:\.[\w-]+)+(?:[/?#].*)?$")

SHORT_LINK_HOSTS = ("spotify.link", "spotify.app.link", "apple.co")
