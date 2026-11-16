"""正文抽取：trafilatura，不下载图片本体，只留 URL 供 OCR 阶段取用。"""

from __future__ import annotations

import json

from ..contracts.models import SourceMeta
from ..net.http import get_text
from .result import IngestResult


def extract_text(html: str, url: str | None = None) -> str:
    """给本地 HTML 文件也用同一条抽取路径。抽不出来就返回空串，由调用方决定怎么报错。"""
    import trafilatura

    return (
        trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            output_format="txt",
            url=url,
        )
        or ""
    )


def ingest(url: str) -> IngestResult:
    import trafilatura

    html = get_text(url)
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        include_images=True,
        output_format="json",
        with_metadata=True,
        url=url,
    )
    if not extracted:
        raise RuntimeError("正文抽取为空（可能需要 cookie 或页面为反爬壳）")

    data = json.loads(extracted)
    text = data.get("text") or ""
    images = [data["image"]] if data.get("image") else []
    if len(text) < 120:
        raise RuntimeError(f"正文过短（{len(text)} 字），疑似抓取失败")

    meta = SourceMeta(
        kind="article",
        platform="wechat" if "mp.weixin.qq.com" in url else "article",
        url=url,
        canonical_url=data.get("url") or url,
        title=data.get("title"),
        author=data.get("author"),
        published_at=data.get("date"),
        extractor="trafilatura",
        extra={"images": images},
    )
    return IngestResult(source=meta, text=text, images=images)
