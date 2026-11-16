"""采集层：把任意链接归一成「一个音频文件或一段正文」。

按职责分文件，改一处不动全链：

    platforms.py     平台表、正则、扩展名（纯数据）
    normalize.py     用户输入还原成干净链接
    classify.py      平台判定 / 类型判定 / 规范 URL
    router.py        路由与降级顺序（唯一的对外入口 fetch）
    local.py         本地文件
    article.py       正文抽取（trafilatura）
    feed.py          播客 RSS（feedparser）
    html_audio.py    页面里的音频直链
    media_download.py yt-dlp 适配
    podcast/         三级路线：页面元数据 → 反查 RSS → enclosure
"""

from .result import IngestResult
from .router import fetch

__all__ = ["IngestResult", "fetch"]
