"""外挂交接：没有模型密钥时，把「理解」这一步交出去。

确定性层（下载 / 抽音轨 / ASR / 清洗 / 契约）跑完就没有任何外部依赖了，
剩下的归纳与跨语言理解可以交给任意一个会读文件的 agent（本机会话、编辑器里的
Copilot、浏览器里的对话窗口都行）。这里只做两件事：

1. export_handoff() 写一份自包含的作业单，agent 只读它就能干活；
2. apply_answer() 收它的 JSON，校验、渲染成 report.md，并把任务标成完成。

契约分三层：必填四件套（intent / one_liner / sections / body）供机器渲染，
不可违反的五条证据纪律保证可回查，其余字段全部可选、无配额。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .models import Report, TranscriptBundle
from .store import Store, job_dir, job_path, write_json

BRIEF = "handoff/BRIEF.md"
ANSWER = "handoff/answer.json"
TEMPLATE = "handoff/answer.template.json"
TRANSCRIPT_MD = "handoff/transcript.md"
INLINE_LIMIT = 40000  # 超过这个字数就切片，BRIEF 里只放目录


def ts(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _meta_table(bundle: TranscriptBundle, job_id: str, directory: Path) -> str:
    src = bundle.source
    duration = src.duration_sec or (bundle.segments[-1].end if bundle.segments else None)
    rows = [
        ("标题", src.title or "（没抓到标题）"),
        ("平台", src.platform),
        ("作者", src.author or "未知"),
        ("发布", src.published_at or "未知"),
        ("源链接", src.canonical_url or src.url or "（本地文件）"),
        ("时长 / 篇幅", ts(duration) if duration else f"{bundle.quality.chars} 字"),
        ("原文语言", f"{bundle.language.detected}（置信度 {bundle.language.probability:.2f}）"),
        ("转写引擎", bundle.quality.asr_engine),
        ("质量", f"{bundle.quality.segment_count} 段 / {bundle.quality.chars} 字"
                 f" / 平均置信度 {bundle.quality.avg_confidence if bundle.quality.avg_confidence is not None else 'n/a'}"),
        ("任务目录", f"`{directory}`"),
        ("任务 id", f"`{job_id}`"),
    ]
    lines = ["| 字段 | 值 |", "| --- | --- |"]
    lines += [f"| {k} | {str(v).replace('|', '／')} |" for k, v in rows]
    return "\n".join(lines)


def _chapters_block(bundle: TranscriptBundle) -> str:
    if not bundle.chapters:
        return "（无预先切分，按内容自行划分 3-8 个小节）"
    return "\n".join(f"- {c.title}：{ts(c.start)}–{ts(c.end)}" for c in bundle.chapters)


def _transcript_lines(bundle: TranscriptBundle) -> list[str]:
    timeline = not any("para_index" in s.flags for s in bundle.segments[:1])
    lines: list[str] = []
    for seg in bundle.segments:
        anchor = f"[{ts(seg.start)}]" if timeline else f"[段{seg.idx}]"
        lines.append(f"{anchor} {seg.text}")
    return lines


def _parts(lines: list[str], budget: int = INLINE_LIMIT) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) > budget and buf:
            parts.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        parts.append("\n".join(buf))
    return parts


def _required_schema() -> dict[str, Any]:
    """必填五件套。template.json 只放这些——塞满字段会鼓励凑数。"""
    return {
        "intent": "这份报告给谁看、要回答什么问题（一句话，可留空）",
        "one_liner": "一句话说清这是什么、讲了什么",
        "tags": ["3-5 个内容标签词，名词或短语，供知识库归类，最多 5 个"],
        "sections": [
            {
                "title": "小节标题",
                "start": "mm:ss",
                "end": "mm:ss",
                "points": ["这段的要点，有几句写几句"],
            }
        ],
        "body": "自由 Markdown 正文：源内容的平直整理，不做评价。想写多长写多长，从 ### 起用标题。",
    }


def _optional_fields() -> list[tuple[str, str]]:
    """可选字段：有则写，无则空，没有配额。"""
    return [
        ("tldr", "核心结论，条数随内容，每条一句话"),
        ("key_points", "最重要的信息，条数随内容"),
        ("data_points", '[{"label", "value", "start"}] 只放结论性数字：阈值、剂量、期限、判定标准。描述性数字不要放'),
        ("quotes", '[{"text", "start", "note"}] 值得原样保留的话，text 保持原语言'),
        ("controversies", "材料里互相矛盾或未证实的说法"),
        ("open_questions", "材料没回答但重要的问题"),
        ("terms", '{"原文术语": "中文解释"}'),
        ("appendix", "通用字段装不下的东西：代码、公式、动作清单、人物表。不要放时间线或章节表（`sections` 已有），不要复述数据点"),
    ]


def build_brief(bundle: TranscriptBundle, job_id: str, directory: Path) -> str:
    """自包含作业单：读它一个文件就能产出 answer.json。"""
    lines = _transcript_lines(bundle)
    timeline = not any("para_index" in s.flags for s in bundle.segments[:1])
    parts = _parts(lines)
    inline = sum(len(p) for p in parts) <= INLINE_LIMIT
    project = PROJECT_ROOT
    py = project / ".venv" / "bin" / "python"
    py_cmd = str(py) if py.exists() else "python"

    out: list[str] = []
    out.append(f"# 交接单 · {bundle.source.title or '未命名内容'}")
    out.append("")
    out.append("> 这份文件是作业单，不是报告。读它的对象是一个 AI agent。")
    out.append("> 本机没有配置模型密钥，确定性层已经把这条件内容变成了带时间码的文本；")
    out.append("> 「理解 + 归纳 + 跨语言」这一步交给你。写完 JSON，报告自动生成。")
    out.append("")
    out.append("## 0. 一句话任务")
    out.append("")
    out.append("读完本文件第 4 节的转写正文，自己判断这条件内容值得怎么讲，")
    out.append("按第 3 节的契约把结果写到：")
    out.append("")
    out.append("```text")
    out.append(str(directory / ANSWER))
    out.append("```")
    out.append("")
    out.append("然后运行下面这条命令（或让用户运行），报告就出来了：")
    out.append("")
    out.append("```bash")
    out.append(f"cd {project} && {py_cmd} -m linkdigest.cli {job_id} --apply")
    out.append("```")
    out.append("")
    out.append("第 3 节里，只有「必填」和「不可违反」是硬约束——那是机器渲染和可回查性要求的。")
    out.append("写多深、分几节、用哪些可选字段，都是你的判断，没有配额。")
    out.append("")
    out.append("## 1. 这条件内容是什么")
    out.append("")
    out.append(_meta_table(bundle, job_id, directory))
    out.append("")
    out.append("## 2. 材料在哪")
    out.append("")
    out.append("以下路径都相对任务目录（就是上面那个 `任务目录`）：")
    out.append("")
    out.append("| 文件 | 用途 |")
    out.append("| --- | --- |")
    out.append("| `handoff/BRIEF.md` | 本文件：元信息 + 产出契约 + 正文 |")
    out.append("| `handoff/transcript.md` | 同一份正文的纯文本版，方便整段引用 |")
    out.append("| `transcript.json` | 机器契约，需要精确到秒的时间码时查这个 |")
    out.append("| `report.md` | 当前降级版（抽取式，无归纳、无跨语言）；可当骨架，内容要重写 |")
    out.append("")
    if not inline:
        out.append(f"正文共 {len(parts)} 片，按顺序读完再动笔：")
        out.append("")
        for i in range(len(parts)):
            out.append(f"- `handoff/part-{i + 1:02d}.md`")
        out.append("")

    out.append("## 3. 要产出什么")
    out.append("")
    out.append("分三层，别混着看：机器靠「必填」渲染和定位，「不可违反」是可回查的前提，")
    out.append("「可选」里没有配额——写不出来就不写。")
    out.append("")
    out.append("### 3.1 必填")
    out.append("")
    out.append("```json")
    out.append(json.dumps(_required_schema(), ensure_ascii=False, indent=2))
    out.append("```")
    out.append("")
    out.append("- `intent`：可以留空。但如果用户提过具体目的（「我想知道该不该照着练」、")
    out.append("  「这套方法能不能用到我们团队」），写进来——它会写进报告元信息，也是你做取舍的依据。")
    out.append("- `tags`：3-5 个内容标签词，名词或短语（「恢复训练」「马拉松」「成本估算」）。")
    out.append("  最多 5 个，宁缺毋滥；写内容真正讲到的东西，不写平台名这类元信息。")
    out.append("- `sections`：`start`/`end` 是回查锚点，报告里每个小节都能据此找回原片位置。")
    out.append("  时间范围要和内容对得上，但不必把内容切成等长的块：天然是一整段的，就写一个。")
    if not timeline:
        out.append("  **这条内容是文章，没有时间轴，`start`/`end` 留空即可**，`sections` 只用来分节。")
    out.append("- `body`：这份报告的主体，写源内容的**平直整理**，不是内容评析。")
    out.append("  按内容自身的结构走：它讲了什么、按什么顺序推进、给出哪些具体做法、数字、判断、案例。")
    out.append("  只陈述源内容说了什么。不排名、不打分、不判优劣，不写「最硬 / 最弱 / 站不住 / 骗人 / 可惜 / 失败案例」这类评语；")
    out.append("  也不写「该拿走什么」「听众应该……」这种使用建议。")
    out.append("  证据不足、前后矛盾、没给出处的说法一律只进 `controversies` / `open_questions`，")
    out.append("  正文里不夹批注、不提前替它下结论。要体现内容之间的落差，用事实并置，不加形容词。")
    out.append("  长度由内容决定——内容够，写三四千字不算多；内容薄，写三百字也不算少。")
    out.append("  **这是整份产出里最值钱的部分，别省。**")
    out.append("")
    out.append("### 3.2 不可违反")
    out.append("")
    out.append("1. 只用正文里的信息。不补外部知识，不推测没出现的人名、数字、结论。")
    out.append("2. 报告语言一律简体中文。原话（`quotes`）保留原文，必要时在 `note` 里给中文意思。")
    out.append("3. 时间码只用正文里出现过的值，格式 `mm:ss`（超过一小时用 `h:mm:ss`）。"
               if timeline else
               "3. 这条内容是文章，没有时间轴，所有 `start`/`end` 字段留空或省略。")
    out.append("4. 出现互相矛盾的说法写进 `controversies`，不要替它下结论；")
    out.append("   材料没回答但重要的问题写进 `open_questions`。")
    out.append("5. 写完把文件落到 `" + ANSWER + "`（路径相对上面那个任务目录）。")
    out.append("")
    out.append("这五条不是风格建议，是这条件内容可被回查的前提。破了，报告就没法核对。")
    out.append("")
    out.append("### 3.3 可选：按内容判断，不要凑")
    out.append("")
    out.append("| 字段 | 装什么 |")
    out.append("| --- | --- |")
    for name, desc in _optional_fields():
        out.append(f"| `{name}` | {desc} |")
    out.append("")
    out.append("没有就留空数组 / 空对象，或者干脆不写这个键——都合法。")
    out.append("遇到什么形状装不下，放 `appendix`，别硬塞进 `points`。")
    out.append("")
    out.append("一条 90 秒的短视频不需要 5 条 `tldr` 和 8 条 `key_points`；")
    out.append("一段 3 小时的播客也不该被压成 3 条。条数跟着内容走，不跟着模板走。")
    out.append("")
    out.append("### 3.4 去重：同一个事实只写一遍")
    out.append("")
    out.append("报告只有一份读者，重复陈述等于浪费他的时间。以下几处最容易撞车：")
    out.append("")
    out.append("- `sections` 自带时间码，**不要**在 `appendix` 里再画一张时间线、时间结构表、章节表。")
    out.append("- `data_points` 只收**结论性**数字：阈值、剂量、期限、判定标准、可复用的比例。")
    out.append("  正文里顺带出现的描述性数字（身高体重、场次、当日跑量、优惠码、观看数）不进表，")
    out.append("  写进 `points` 或 `body` 就够。")
    out.append("- 同一个数字不要在 `key_points` 和 `data_points` 各讲一遍：")
    out.append("  原则写 `key_points`，数值写 `data_points`，两边都写就是重复。")
    out.append("- `tldr` 写内容的主要结论，`body` 写内容怎么展开，`sections` 写定位。三者不互相复述措辞。")
    out.append("- `appendix` 只装通用字段真的装不下的东西：代码、公式、动作清单、人物表。")
    out.append("  它不能是前文任何一节的重排版本。")
    out.append("")
    out.append("拿不准的时候删掉，不要留着凑完整。空字段渲染时不会出现，少写不会有洞。")
    out.append("")
    out.append("### 3.5 正文口径：整理，不是评论")
    out.append("")
    out.append("`body` 是给没看过原件的人看的内容整理，不是影评，也不是内容质量诊断。")
    out.append("读者要的是「这条内容讲了什么」，不是「你怎么看这条内容」。判断性的东西已经有地方放：")
    out.append("`controversies` 放矛盾与未证实，`open_questions` 放材料没回答的，`key_points` 放要点。")
    out.append("")
    out.append("| 不要写 | 改成 |")
    out.append("| --- | --- |")
    out.append("| 这是全片最硬的一段 | 直接写这一段给了什么：判据、数值、动作 |")
    out.append("| 论证站不住，只有个人经历 | 依据是他自己的经历和一次假想场景 |")
    out.append("| 这段只有口号，没有剂量 | 这一段给了方向，没有给动作、频率或周期 |")
    out.append("| 可惜这个论点被八卦拖累了 | 他在同一段引用了第三方的私人生活细节 |")
    out.append("| 最有说服力也最不严谨 | 他给出的三条原因分别是…… |")
    out.append("| 听众应该按这个权重分配注意力 | （删掉，正文不给使用建议） |")
    out.append("")
    out.append("允许写的是结构性描述：内容分几段、时间码怎么对应、哪一条是转折、哪一处没有展开。")
    out.append("这些是「它在讲什么」，不是「它讲得好不好」。两者的分界是：删掉形容词之后，")
    out.append("句子还能不能从原文核对出来。核不出来的，就是你的判断，不该出现在正文里。")
    out.append("")
    out.append("## 4. 转写正文")
    out.append("")
    out.append("预先切分（可合并，但时间范围别跨太远）：")
    out.append("")
    out.append(_chapters_block(bundle))
    out.append("")
    if inline:
        for i, part in enumerate(parts, 1):
            if len(parts) > 1:
                out.append(f"### 第 {i}/{len(parts)} 片")
                out.append("")
            out.append("```text")
            out.append(part)
            out.append("```")
            out.append("")
    else:
        out.append("正文较长，已切片，见 `handoff/part-*.md`。")
        out.append("")
    out.append("---")
    out.append("")
    out.append("_生成时间 " + datetime.now().astimezone().strftime("%Y-%m-%d %H:%M") +
               "；确定性层全开源，本文件不含任何模型输出。_")
    return "\n".join(out) + "\n"


def export_handoff(job_id: str, bundle: TranscriptBundle) -> Path:
    """写 handoff/ 目录，返回 answer.json 的期望路径。"""
    directory = job_dir(job_id)
    handoff_dir = directory / "handoff"
    handoff_dir.mkdir(parents=True, exist_ok=True)

    lines = _transcript_lines(bundle)
    parts = _parts(lines)
    if len(parts) > 1:
        for i, part in enumerate(parts, 1):
            (handoff_dir / f"part-{i:02d}.md").write_text(
                f"# {bundle.source.title or '未命名内容'} · 第 {i}/{len(parts)} 片\n\n{part}\n",
                encoding="utf-8",
            )

    transcript_md = [
        f"# {bundle.source.title or '未命名内容'}",
        "",
        f"_{bundle.source.platform} · {bundle.quality.segment_count} 段 / "
        f"{bundle.quality.chars} 字 · 原语言 {bundle.language.detected}_",
        "",
    ]
    for chapter in bundle.chapters:
        transcript_md.append(f"## {chapter.title}（{ts(chapter.start)}–{ts(chapter.end)}）")
        transcript_md.append("")
    transcript_md.extend(["## 全文", ""])
    transcript_md.extend(lines)
    (directory / TRANSCRIPT_MD).write_text("\n".join(transcript_md) + "\n", encoding="utf-8")

    (directory / BRIEF).write_text(build_brief(bundle, job_id, directory), encoding="utf-8")
    (directory / TEMPLATE).write_text(
        json.dumps(_required_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return directory / ANSWER


def read_answer(job_id: str, path: Path | None = None) -> dict[str, Any]:
    target = path or job_path(job_id, ANSWER)
    if not target.exists():
        raise FileNotFoundError(
            f"没有找到 {target.name}。先让 agent 按 {BRIEF} 产出答案，"
            f"或把已有答案放到 {target.parent.name}/{target.name}。"
        )
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("answer.json 顶层必须是对象")
    return payload


def apply_answer(job_id: str, payload: dict[str, Any], store: Store | None = None,
                 source: str = "external-agent") -> Report:
    """把外部 agent 的 JSON 变成正式报告：校验 → 渲染 → 状态改为完成。"""
    from .report import _to_report, save_report
    from .store import read_json

    store = store or Store()
    job = store.get(job_id)
    if job is None:
        raise KeyError(f"job {job_id} 不存在")
    bundle_path = job_path(job_id, "transcript.json")
    if not bundle_path.exists():
        raise FileNotFoundError("这个任务还没有 transcript.json，先跑完确定性层")
    bundle = TranscriptBundle.model_validate(read_json(bundle_path))

    report = _to_report(payload)
    if not report.one_liner and not report.body:
        raise ValueError(
            "答案太空：`one_liner`（一句话）和 `body`（正文）至少要给一个，"
            "否则报告顶部没有任何可读内容。字段名是否与 BRIEF.md 第 3.1 节一致？"
        )
    report.degraded = False
    report.notes = [f"理解层由外部 agent 完成（{source}），本机未调用模型。"] + [
        n for n in report.notes if "降级" not in n and "LLM_API_KEY" not in n
    ]
    if not report.sections:
        report.notes.append("未提供 sections，报告没有时间码锚点，无法逐节回查原片。")

    write_json(job_path(job_id, "report.json"), json.loads(report.model_dump_json()))
    save_report(job_id, bundle, report,
                extra={"mode": "external", "llm_stats": {},
                       "job_id": job.id, "created_at": job.created_at})

    usage_path = job_path(job_id, "usage.json")
    usage = read_json(usage_path) if usage_path.exists() else {}
    usage.update({"mode": "external", "external": {"by": source,
                                                   "at": datetime.now().astimezone().isoformat(timespec="seconds")}})
    write_json(usage_path, usage)

    store.update(job_id, degraded=0, llm_model=source, status="done", stage="done", progress=1.0)
    store.finalize_dir(job_id)
    return report
