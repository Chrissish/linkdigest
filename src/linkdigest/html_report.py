"""报告的第二形态：单文件、阅读优先的 HTML。

和 `report.md` 同源（同一个 Report 对象），差别只在排版，不在内容：
md 给终端、版本库、diff；html 给人读、给打印、发给没装这套工具的人。
CSS 全部内联、不引用任何外部资源，双击就能看，也能直接当附件发出去。

风格取向是「文档」而不是「应用」：默认浅色纸面（长文阅读和打印都更稳），
跟随系统切深色；节标题做成小号标签式，让正文里的 `###` 才是视觉主体。
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import markdown as md

from .models import Report, TranscriptBundle
from .report import ts

TEMPLATE = Path(__file__).with_name("templates") / "report.html"

# 外部 agent 写的正文可能带原始 HTML，本地工具也按最小权限处理
_DANGEROUS = re.compile(
    r"<\s*(script|iframe|object|embed|link|meta)\b[^>]*>|</\s*(script|iframe|object|embed)\s*>"
    r"|\son[a-z]+\s*=\s*\"[^\"]*\"|\son[a-z]+\s*=\s*'[^']*'"
    r"|javascript:",
    re.IGNORECASE,
)


def _esc(text: object) -> str:
    return html.escape(str(text), quote=False)


def _md(text: str) -> str:
    if not text:
        return ""
    rendered = md.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists"],
        output_format="html5",
    )
    return _DANGEROUS.sub("", rendered)


def _seek_url(url: str | None, seconds: float | None) -> str | None:
    """能给到秒级定位的平台，把时间码变成可点的链接。"""
    if not url or seconds is None:
        return None
    parts = urlsplit(url)
    host = parts.netloc.lower()
    query = dict(parse_qsl(parts.query))
    if "youtube.com" in host or "youtu.be" in host:
        query["t"] = f"{int(seconds)}s"
    elif "bilibili.com" in host:
        query["t"] = str(int(seconds))
    else:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _stamp(seconds: float | None, url: str | None, timeline: bool) -> str:
    if not timeline or seconds is None:
        return ""
    label = ts(seconds)
    href = _seek_url(url, seconds)
    if href:
        return f'<a class="ts" href="{html.escape(href)}" target="_blank" rel="noopener">{label}</a>'
    return f'<span class="ts">{label}</span>'


def _source_line(bundle: TranscriptBundle) -> str:
    url = bundle.source.canonical_url or bundle.source.url
    if not url:
        return ""
    safe = html.escape(url)
    return f'    <p class="source">源：<a href="{safe}" target="_blank" rel="noopener">{_esc(url)}</a></p>'


def _meta(bundle: TranscriptBundle, duration: float | None, timeline: bool) -> str:
    items: list[str] = []
    if bundle.source.author:
        items.append(f'<span class="chip">{_esc(bundle.source.author)}</span>')
    if timeline and duration:
        items.append(f'<span class="chip"><b>{ts(duration)}</b></span>')
    else:
        items.append(f'<span class="chip"><b>{bundle.quality.chars}</b> 字</span>')
    items.append(f'<span class="chip">原语言 <b>{_esc(bundle.language.detected)}</b></span>')
    items.append(f'<span class="chip">{_esc(bundle.quality.asr_engine)}</span>')
    if bundle.quality.avg_confidence is not None:
        items.append(f'<span class="chip">置信度 <b>{bundle.quality.avg_confidence:.3f}</b></span>')
    return "\n      ".join(items)


def _tags(report: Report) -> str:
    if not report.tags:
        return ""
    items = "\n      ".join(f'<span class="tag">{_esc(t)}</span>' for t in report.tags[:5])
    return f'  <div class="tags">\n      {items}\n  </div>'


def _lead(report: Report) -> str:
    out: list[str] = []
    if report.one_liner:
        out.append(f'<p class="oneline">{_esc(report.one_liner)}</p>')
    if report.degraded:
        out.append(
            '<aside class="degraded">降级模式：未调用模型，以下为抽取式结果。'
            "结构完整，但不具备归纳与跨语言能力。</aside>"
        )
    return "\n".join(out)


def _nav(report: Report, url: str | None, timeline: bool) -> str:
    out: list[str] = []
    if report.tldr:
        out.append('<section class="block">')
        out.append("  <h2>速览</h2>")
        out.append('  <ol class="tldr">')
        out.extend(f"    <li>{_esc(item)}</li>" for item in report.tldr)
        out.append("  </ol>")
        out.append("</section>")
    # 文章类没有时间码可跳，节数又少时，目录只是把下面的内容再说一遍
    if report.sections and (timeline or len(report.sections) >= 4):
        out.append('<section class="block">')
        out.append("  <h2>分段</h2>")
        out.append('  <ol class="toc">')
        for i, section in enumerate(report.sections, 1):
            stamp = _stamp(section.start, url, timeline)
            out.append("    <li>")
            if stamp:
                out.append(f"      {stamp}")
            out.append(f'      <a class="toc-title" href="#seg-{i}">{_esc(section.title)}</a>')
            out.append("    </li>")
        out.append("  </ol>")
        out.append("</section>")
    return "\n".join(out)


def _main(report: Report, url: str | None, timeline: bool) -> str:
    out: list[str] = []
    if report.body:
        out.append('<section class="block">')
        out.append('  <article class="prose">')
        out.append(_md(report.body))
        out.append("  </article>")
        out.append("</section>")

    if report.sections:
        out.append('<section class="block">')
        out.append("  <h2>分段解读</h2>")
        for i, section in enumerate(report.sections, 1):
            stamp = _stamp(section.start, url, timeline)
            out.append(f'  <section class="seg" id="seg-{i}">')
            out.append('    <div class="seg-head">')
            out.append(f"      <h3>{_esc(section.title)}</h3>")
            if stamp:
                out.append(f"      {stamp}")
            out.append("    </div>")
            if section.points:
                out.append("    <ul>")
                out.extend(f"      <li>{_esc(p)}</li>" for p in section.points)
                out.append("    </ul>")
            out.append("  </section>")
        out.append("</section>")

    if report.key_points:
        out.append('<section class="block">')
        out.append("  <h2>关键信息</h2>")
        out.append('  <ol class="bullets">')
        out.extend(f"    <li>{_esc(p)}</li>" for p in report.key_points)
        out.append("  </ol>")
        out.append("</section>")

    if report.data_points:
        out.append('<section class="block">')
        out.append("  <h2>数据点</h2>")
        out.append('  <table class="grid">')
        head = "<tr><th>指标</th><th>数值</th>" + ('<th class="pos">位置</th>' if timeline else "") + "</tr>"
        out.append(f"    <thead>{head}</thead>")
        out.append("    <tbody>")
        for d in report.data_points:
            cells = f"<td>{_esc(d.label)}</td><td class=\"num\">{_esc(d.value)}</td>"
            if timeline:
                cells += f'<td class="pos">{_stamp(d.start, url, timeline) or "—"}</td>'
            out.append(f"      <tr>{cells}</tr>")
        out.append("    </tbody>")
        out.append("  </table>")
        out.append("</section>")

    if report.quotes:
        out.append('<section class="block">')
        out.append("  <h2>值得记的原话</h2>")
        for q in report.quotes:
            out.append('  <figure class="quote">')
            out.append(f"    <p>{_esc(q.text)}</p>")
            stamp = _stamp(q.start, url, timeline)
            if q.note or stamp:
                out.append('    <div class="quote-foot">')
                if q.note:
                    out.append(f'      <span class="note">{_esc(q.note)}</span>')
                if stamp:
                    out.append(f"      {stamp}")
                out.append("    </div>")
            out.append("  </figure>")
        out.append("</section>")

    if report.controversies:
        out.append('<section class="block">')
        out.append("  <h2>存疑与争议</h2>")
        out.append('  <ul class="bullets">')
        out.extend(f"    <li>{_esc(c)}</li>" for c in report.controversies)
        out.append("  </ul>")
        out.append("</section>")

    if report.open_questions:
        out.append('<section class="block">')
        out.append("  <h2>未回答的问题</h2>")
        out.append('  <ul class="bullets">')
        out.extend(f"    <li>{_esc(q)}</li>" for q in report.open_questions)
        out.append("  </ul>")
        out.append("</section>")

    if report.terms:
        out.append('<section class="block">')
        out.append("  <h2>术语</h2>")
        out.append('  <dl class="terms">')
        for key, value in list(report.terms.items())[:24]:
            out.append(f"    <div><dt>{_esc(key)}</dt><dd>{_esc(value)}</dd></div>")
        out.append("  </dl>")
        out.append("</section>")
    return "\n".join(out)


def _appendix(report: Report) -> str:
    if not report.appendix:
        return ""
    body = _md(report.appendix)
    # 附录自带 ### 标题时不再包一层 h2，避免出现两个标题层
    heading = "" if re.match(r"\s*<h[1-6]", body) else "  <h2>补充</h2>\n"
    return f'<section class="block appendix">\n{heading}  <div class="prose">\n{body}\n  </div>\n</section>'


def _footer(bundle: TranscriptBundle, report: Report, extra: dict) -> str:
    lines: list[str] = []
    stats = [
        f"清洗 {bundle.quality.segment_count} 段 / {bundle.quality.chars} 字",
        f"置信度 {bundle.quality.avg_confidence if bundle.quality.avg_confidence is not None else 'n/a'}",
    ]
    if bundle.quality.flags:
        stats.append("动作 " + ", ".join(f"{k}={v}" for k, v in bundle.quality.flags.items()))
    llm = extra.get("llm_stats") or {}
    if llm.get("calls"):
        stats.append(f"模型 {llm.get('calls')} 次 / {llm.get('prompt_tokens')}+{llm.get('completion_tokens')} tokens")
    lines.append(f"    <p>{_esc(' · '.join(stats))}</p>")
    if report.intent:
        lines.append(f'    <p class="intent-foot">给谁看 / 要回答什么：{_esc(report.intent)}</p>')

    notes = [n for n in report.notes if n]
    if notes:
        lines.append('    <div class="warn">')
        if bundle.quality.warnings:
            lines.append("      <ul>")
            lines.extend(f"        <li>{_esc(w)}</li>" for w in bundle.quality.warnings)
            lines.append("      </ul>")
        for note in notes:
            lines.append(f"      <p>{_esc(note)}</p>")
        lines.append("    </div>")
    elif bundle.quality.warnings:
        lines.append('    <div class="warn">')
        lines.append("      <ul>")
        lines.extend(f"        <li>{_esc(w)}</li>" for w in bundle.quality.warnings)
        lines.append("      </ul>")
        lines.append("    </div>")

    lines.append(f"    <p>{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')} · linkdigest</p>")
    return "\n".join(lines)


def render_html(bundle: TranscriptBundle, report: Report, extra: dict | None = None) -> str:
    extra = extra or {}
    timeline = not any("para_index" in s.flags for s in bundle.segments[:1])
    duration = bundle.source.duration_sec or (bundle.segments[-1].end if bundle.segments else None)
    url = bundle.source.canonical_url or bundle.source.url
    kicker_parts = [bundle.source.platform]
    if bundle.source.published_at:
        kicker_parts.append(str(bundle.source.published_at))

    template = TEMPLATE.read_text(encoding="utf-8")
    values = {
        "LANG": "zh-CN",
        "TITLE": _esc(bundle.source.title or "未命名内容"),
        "DESC": _esc(report.one_liner or bundle.source.title or ""),
        "KICKER": _esc(" · ".join(kicker_parts)),
        "META": _meta(bundle, duration, timeline),
        "SOURCE": _source_line(bundle),
        "TAGS": _tags(report),
        "LEAD": _lead(report),
        "NAV": _nav(report, url, timeline),
        "MAIN": _main(report, url, timeline),
        "APPENDIX": _appendix(report),
        "FOOTER": _footer(bundle, report, extra),
    }
    for key, value in values.items():
        template = template.replace("{{%s}}" % key, value)
    return template
