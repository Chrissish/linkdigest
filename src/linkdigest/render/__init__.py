"""渲染层：一个 Report，两种形态。渲染不读磁盘、不联网，纯函数。"""

from .html import render_html, seek_url
from .markdown import render_markdown
from .save import save_report

__all__ = ["render_html", "render_markdown", "save_report", "seek_url"]
