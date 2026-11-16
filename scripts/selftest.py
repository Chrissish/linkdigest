"""确定性层的自检。不依赖网络与模型，改规则后先跑这个。

    .venv/bin/python scripts/selftest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from linkdigest.asr import ASRSegment  # noqa: E402
from linkdigest.bundle import build_bundle, validate_bundle  # noqa: E402
from linkdigest.models import SourceMeta  # noqa: E402
from linkdigest.normalize import (  # noqa: E402
    clean_text,
    normalize_segments,
    text_to_segments,
)
from linkdigest.report import (  # noqa: E402
    _to_report,
    degraded_report,
    render_markdown,
    ts,
)
from linkdigest.sources import explain_error, normalize_input  # noqa: E402

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILED.append(f"{name}: got={got!r} want={want!r}")
        print(f"  FAIL {name}\n       got  {got!r}\n       want {want!r}")
    else:
        print(f"  ok   {name}")


def check_true(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILED.append(f"{name} {detail}")
        print(f"  FAIL {name} {detail}")
    else:
        print(f"  ok   {name}")


def norm(*texts: tuple[float, float, str]) -> tuple[list[dict], dict, list[str], dict]:
    return normalize_segments([ASRSegment(s, e, t) for s, e, t in texts])


print("[clean_text]")
check("中文全角标点保留", clean_text("第一,成本。"), "第一，成本。")
check("全角拉丁归一", clean_text("ＡＢＣ１２３"), "ABC123")
check("CJK 间空格移除", clean_text("抽 帧 方 案"), "抽帧方案")
check("英文词间空格保留", clean_text("hello   world  ok"), "hello world ok")
check("零宽字符清除", clean_text("抽\u200b帧"), "抽帧")
check("英文内逗号不动", clean_text("a,b"), "a,b")

print("[normalize_segments]")
segs, flags, warnings, stats = norm(
    (0.0, 2.0, "今天我们聊一个反直觉的结论。"),
    (2.0, 4.0, "请不吝点赞订阅转发打赏支持明镜与点点栏目"),
    (4.0, 6.0, "啊啊啊"),
    (6.0, 6.0, "时间戳坏掉的段"),
    (7.0, 9.0, "抽帧抽帧抽帧抽帧"),
)
texts = [s["text"] for s in segs]
check_true("幻觉段被丢弃", all("点赞" not in t for t in texts), str(texts))
check_true("单字循环被丢弃", all("啊啊" not in t for t in texts))
check("幻觉计数", flags.get("hallucination_dropped"), 2)
check_true("时间戳倒挂被修", segs[-1]["end"] > segs[-1]["start"], str(segs[-1]))
check_true("段内卡带被压缩", "抽帧抽帧抽帧抽帧" not in texts)
check("字符统计一致", stats["chars"], sum(len(t) for t in texts))

segs, flags, _, _ = norm(
    (0.0, 3.0, "第一句话讲完了。"),
    (3.1, 4.0, "好"),
    (4.0, 8.0, "第二句话没有句号"),
    (8.2, 12.0, "接着往上接"),
)
check("碎片被合并", len(segs), 2)
check_true("碎句被拼接", segs[-1]["text"].endswith("接着往上接"), segs[-1]["text"])

print("[text_to_segments]")
long_para = "".join(f"第{i}个要点说明了一些有差异的内容。" for i in range(1, 41))
segs, _, _, _ = text_to_segments(long_para)
check_true("长段被切句", len(segs) > 3, f"segments={len(segs)}")
check_true("段落锚点标记", all("para_index" in s["flags"] for s in segs))

print("[bundle]")
segs, flags, warnings, stats = norm(
    (0.0, 6.0, "第一段内容，用来验证契约。"),
    (6.0, 12.0, "第二段内容，同样验证契约。"),
)
source = SourceMeta(kind="audio", platform="rss", title="测试", extractor="selftest")
for i, s in enumerate(segs):
    s["idx"] = i
bundle = build_bundle(
    bundle_id="deadbeefdeadbeef", source=source, segments=segs, flags=flags,
    warnings=warnings, stats=stats, language="zh", language_probability=0.99,
    asr_engine="selftest", media=None,
)
payload = bundle.model_dump(mode="json")
check("契约校验通过", validate_bundle(payload), [])
check("章节非空", len(bundle.chapters) > 0, True)

bad = dict(payload)
bad["segments"] = [dict(payload["segments"][0], idx=3)]
check_true("idx 不连续被拦", validate_bundle(bad) != [])

print("[report]")
check("时间码格式", ts(3725), "01:02:05")
check("时间码短格式", ts(65), "01:05")
rep = degraded_report(bundle)
rep.tags = ["恢复训练", "跑步"]
check("降级标记", rep.degraded, True)
check_true("降级报告有内容", bool(rep.tldr and rep.sections))
md = render_markdown(bundle, rep, extra={"llm_stats": {}, "mode": "degraded", "job_id": "test01"})
check_true("markdown 以 frontmatter 开头", md.startswith("---\n"), md[:40])
check_true("frontmatter 有标题", 'title: "测试"' in md)
check_true("frontmatter 有标签键", "tags:" in md)
check_true("frontmatter 有任务号", 'job: "test01"' in md)
check_true("frontmatter 有类型", "type: linkdigest" in md)
check_true("frontmatter 收尾后是正文", "\n---\n\n# 测试\n" in md)
check_true("元信息不再散落在正文", "原语言" not in md.split("---", 2)[2])
check_true("降级提示存在", "降级模式" in md)
over = _to_report({"one_liner": "x", "tags": ["a", "b", "c", "d", "e", "f", "g"]})
check("标签截到 5 个", len(over.tags), 5)
dedup = _to_report({"one_liner": "x", "tags": ["音频", " 音频 ", "文本", "校验"]})
check("标签去重", len(dedup.tags), 3)

print("[input]")
check("shell 转义的 ? 被还原",
      normalize_input(r"https://www.youtube.com/watch\?v\=a1K2V8iDYQA"),
      "https://www.youtube.com/watch?v=a1K2V8iDYQA")
check("JSON 转义的 / 被还原",
      normalize_input(r"https:\/\/youtu.be\/dQw4w9WgXcQ"),
      "https://youtu.be/dQw4w9WgXcQ")
check("从整句里抠出链接",
      normalize_input("看这个 https://www.bilibili.com/video/BV1GJ411x7h7?p=2 挺好"),
      "https://www.bilibili.com/video/BV1GJ411x7h7?p=2")
check("尾部中文标点被剪掉", normalize_input("https://example.com/a.mp3，"), "https://example.com/a.mp3")
check("裸域名补协议", normalize_input("www.youtube.com/watch?v=xyz"), "https://www.youtube.com/watch?v=xyz")
check("HTML 实体被还原",
      normalize_input("https://mp.weixin.qq.com/s?__biz=MzA5&mid=1&amp;idx=2"),
      "https://mp.weixin.qq.com/s?__biz=MzA5&mid=1&idx=2")
try:
    normalize_input("ftp://a.b/c")
    check_true("非 http 协议被拒", False)
except ValueError:
    check_true("非 http 协议被拒", True, "")

print("[errors]")
check_true("代理 502 归因到网络",
           "代理" in explain_error("Tunnel connection failed: 502 Bad Gateway", "https://www.youtube.com/watch?v=x"))
check_true("机器人校验归因到 cookies",
           "cookies" in explain_error("Sign in to confirm you're not a bot", "https://www.youtube.com/watch?v=x"))
check_true("已删除内容有话说",
           "不可用" in explain_error("ERROR: Video unavailable", "https://www.youtube.com/watch?v=x"))

print("[naming]")
from linkdigest.store import slugify  # noqa: E402

check("英文标题压成 slug", slugify("How to Recover After a 100k!"), "How-to-Recover-After-a-100k")
check("中文标点被折叠", slugify("抽帧：方案 / 反斜杠\\与冒号:"), "抽帧-方案-反斜杠-与冒号")
check("空标题有兜底", slugify(None), "untitled")
check("超长标题被截断", len(slugify("x" * 200)), 40)

print()
# ---- 语言标签归一：平台给 BCP-47，Whisper 只认两位码 ----
from linkdigest.asr import normalize_language  # noqa: E402

check("en-US → en", normalize_language("en-US"), "en")
check("zh-Hans-CN → zh", normalize_language("zh-Hans-CN"), "zh")
check("zh_TW → zh", normalize_language("zh_TW"), "zh")
check("空值不猜", normalize_language(""), None)
check("未知码交给检测", normalize_language("klingon"), None)

# ---- 目录命名 + 外挂交接：无模型时也能出报告的完整闭环 ----
print("[jobs + handoff]")
import json  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402

from linkdigest import config  # noqa: E402
from linkdigest.handoff import BRIEF, apply_answer, export_handoff  # noqa: E402
from linkdigest.store import Store, job_dir, job_path, write_json  # noqa: E402

config.settings.data_dir = Path(tempfile.mkdtemp(prefix="linkdigest-selftest-"))
store = Store()
job = store.create_job("https://www.youtube.com/watch?v=abcdefghijk", "url")
store.update(job.id, title="How to Recover After a 100k Ultra", platform="youtube",
             stage="done", status="done", chars=30, degraded=1)
directory = store.finalize_dir(job.id)
check_true("目录名以 _job_id 结尾", directory.name.endswith("_" + job.id), directory.name)
check_true("目录名带时间前缀", re.match(r"^\d{8}-\d{4}_", directory.name) is not None, directory.name)
check_true("目录名带标题", "How-to-Recover" in directory.name, directory.name)
check_true("job_dir 能反查回可读目录", job_dir(job.id) == directory, str(job_dir(job.id)))
check_true("manifest 落盘", (directory / "manifest.json").exists())
check_true("索引落盘", (config.settings.jobs_dir / "INDEX.md").exists())
check("前缀能定位任务", store.resolve(job.id[:6]).id if store.resolve(job.id[:6]) else None, job.id)

write_json(job_path(job.id, "transcript.json"), payload)
answer_path = export_handoff(job.id, bundle)
brief = (directory / BRIEF).read_text(encoding="utf-8")
check_true("交接包 BRIEF 存在", answer_path.parent.joinpath("BRIEF.md").exists(), str(answer_path))
check_true("BRIEF 含必填契约", '"one_liner"' in brief and '"body"' in brief and '"intent"' in brief)
check_true("BRIEF 三层分开",
           all(f"### 3.{i}" in brief for i in (1, 2, 3)))
check_true("BRIEF 不再给条数配额", "≤40 字" not in brief and "5-8 条" not in brief)
template = json.loads((directory / "handoff/answer.template.json").read_text(encoding="utf-8"))
check("模板只放必填五件套", sorted(template), ["body", "intent", "one_liner", "sections", "tags"])
check_true("BRIEF 含正文与时间码", "[00:00]" in brief)
check_true("BRIEF 含应用命令", "--apply" in brief)
check_true("纯文本稿落盘", (directory / "handoff/transcript.md").exists())

try:
    apply_answer(job.id, {"tldr": []}, store)
    check_true("空答案被拒", False)
except ValueError:
    check_true("空答案被拒", True, "")

try:
    apply_answer(job.id, {"body": "只有自由正文，没有一句话摘要，也应当被接受。"}, store)
    check_true("只给 body 也能应用", True)
except ValueError as exc:
    check_true("只给 body 也能应用", False, str(exc))

answer = {
    "intent": "给准备复跑的人看：这次该不该按原计划来",
    "one_liner": "一段用来验证外部交接的测试内容",
    "tags": ["视频理解", "语音识别"],
    "tldr": ["第一段讲契约", "第二段同样讲契约"],
    "sections": [{"title": "开头", "start": "00:00", "end": "00:12", "points": ["验证渲染"]}],
    "body": "### 契约怎么落下来\n\n正文是自由层，深度由内容决定，不该被字数限制住。",
    "key_points": ["契约校验通过"],
    "quotes": [{"text": "第一段内容，用来验证契约。", "start": "00:00", "note": "原话"}],
    "appendix": "### 轮次对照\n\n| 轮次 | 结论 |\n| --- | --- |\n| 一 | 通过 |",
}
apply_answer(job.id, answer, store)
final = store.get(job.id)
check("应用后降级标记清除", final.degraded, False)
check("应用后记录来源", final.llm_model, "external-agent")
markdown_text = job_path(job.id, "report.md").read_text(encoding="utf-8")
check_true("报告已重渲染", "降级模式" not in markdown_text)
check_true("报告含外部来源标注", "外部 agent" in markdown_text)
check_true("intent 印在开头", "给准备复跑的人看" in markdown_text)
check_true("body 渲染成正文", "## 正文" in markdown_text and "深度由内容决定" in markdown_text)
check_true("appendix 自带三级标题时仍补容器",
           "## 补充" in markdown_text and "### 轮次对照" in markdown_text)
check_true("appendix 自带二级标题时不再包一层",
           "## 补充" not in render_markdown(bundle, _to_report({"appendix": "## 轮次对照\n\n| a | b |"})))
from linkdigest.report import _to_report, render_markdown  # noqa: E402

bare = render_markdown(bundle, _to_report({"appendix": "| a | b |\n| --- | --- |"}))
check_true("appendix 无标题时自动加容器", "## 补充" in bare)

html_text = job_path(job.id, "report.html").read_text(encoding="utf-8")
check_true("html 同源落盘", "<article class=\"prose\"" in html_text and "给准备复跑的人看" in html_text)
check_true("html 无占位残留", "{{" not in html_text)
check_true("html 样式内联不引外部资源",
           "<style>" in html_text and "http" not in html_text.split("</head>")[0])
check_true("html 时间码渲染", 'class="ts"' in html_text)
from linkdigest.html_report import _seek_url  # noqa: E402

check("youtube 时间码跳到秒",
      _seek_url("https://www.youtube.com/watch?v=abc", 210),
      "https://www.youtube.com/watch?v=abc&t=210s")
check("b 站时间码跳到秒",
      _seek_url("https://www.bilibili.com/video/BV1xx", 90),
      "https://www.bilibili.com/video/BV1xx?t=90")
check("未知平台不造链接", _seek_url("https://example.com/a", 10), None)
check_true("html 无 a 嵌套", not re.search(r"<a [^>]*>(?:(?!</a>).)*<a ", html_text, re.S))
check_true("html 带打印样式", "@media print" in html_text)
check_true("html 标签区在摘要之上",
           html_text.find('class="tags"') != -1 and
           html_text.find('class="tags"') < html_text.find('class="oneline"'))
check_true("html 摘要不含 intent 卡", 'class="intent"' not in html_text)
check_true("intent 收进页脚", 'class="intent-foot"' in html_text)
check("应用后状态为完成", final.status, "done")
check_true("usage 记录外部模式",
           json.loads(job_path(job.id, "usage.json").read_text(encoding="utf-8")).get("mode") == "external")

if FAILED:
    print(f"失败 {len(FAILED)} 项：")
    for item in FAILED:
        print(" -", item)
    raise SystemExit(1)
print("全部通过")
