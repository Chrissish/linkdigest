"""确定性层的自检。不依赖网络与模型，改规则后先跑这个。

    .venv/bin/python scripts/selftest.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from linkdigest.app import service as app_service  # noqa: E402
from linkdigest.capabilities.speech import ASRSegment, normalize_language  # noqa: E402
from linkdigest.clean import clean_text, normalize_segments, text_to_segments  # noqa: E402
from linkdigest.config import Settings, load  # noqa: E402
from linkdigest.contracts.builder import build_bundle  # noqa: E402
from linkdigest.contracts.models import SourceMeta  # noqa: E402
from linkdigest.contracts.validation import validate_bundle  # noqa: E402
from linkdigest.conventions.jsonio import write_json  # noqa: E402
from linkdigest.conventions.naming import slugify  # noqa: E402
from linkdigest.conventions.timecode import ts  # noqa: E402
from linkdigest.handoff import BRIEF, apply_answer, export_handoff  # noqa: E402
from linkdigest.ingest.normalize import normalize_input  # noqa: E402
from linkdigest.jobs.db import Store  # noqa: E402
from linkdigest.jobs.finalize import finalize  # noqa: E402
from linkdigest.net.errors import explain_error  # noqa: E402
from linkdigest.render.html import seek_url  # noqa: E402
from linkdigest.render.markdown import render_markdown  # noqa: E402
from linkdigest.understand.degraded import degraded_report  # noqa: E402
from linkdigest.understand.payload import report_from_payload  # noqa: E402

# 整个自检共用这一份配置；需要落盘的分支再 model_copy 换 data_dir
cfg = load()

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


def norm(*items: tuple[float, float, str]):
    return normalize_segments(cfg, [ASRSegment(s, e, t) for s, e, t in items])


print("[clean_text]")
check("中文全角标点保留", clean_text("第一,成本。"), "第一，成本。")
check("全角拉丁归一", clean_text("ＡＢＣ１２３"), "ABC123")
check("CJK 间空格移除", clean_text("抽 帧 方 案"), "抽帧方案")
check("英文词间空格保留", clean_text("hello   world  ok"), "hello world ok")
check("零宽字符清除", clean_text("抽\u200b帧"), "抽帧")
check("英文内逗号不动", clean_text("a,b"), "a,b")

print("[normalize_segments]")
result = norm(
    (0.0, 2.0, "今天我们聊一个反直觉的结论。"),
    (2.0, 4.0, "请不吝点赞订阅转发打赏支持明镜与点点栏目"),
    (4.0, 6.0, "啊啊啊"),
    (6.0, 6.0, "时间戳坏掉的段"),
    (7.0, 9.0, "抽帧抽帧抽帧抽帧"),
)
texts = [s["text"] for s in result.segments]
check_true("幻觉段被丢弃", all("点赞" not in t for t in texts), str(texts))
check_true("单字循环被丢弃", all("啊啊" not in t for t in texts))
check("幻觉计数", result.flags.get("hallucination_dropped"), 2)
check_true(
    "时间戳倒挂被修", result.segments[-1]["end"] > result.segments[-1]["start"], str(result.segments[-1])
)
check_true("段内卡带被压缩", "抽帧抽帧抽帧抽帧" not in texts)
check("字符统计一致", result.stats["chars"], sum(len(t) for t in texts))

result = norm(
    (0.0, 3.0, "第一句话讲完了。"),
    (3.1, 4.0, "好"),
    (4.0, 8.0, "第二句话没有句号"),
    (8.2, 12.0, "接着往上接"),
)
check("碎片被合并", len(result.segments), 2)
check_true("碎句被拼接", result.segments[-1]["text"].endswith("接着往上接"), result.segments[-1]["text"])

print("[text_to_segments]")
long_para = "".join(f"第{i}个要点说明了一些有差异的内容。" for i in range(1, 41))
result = text_to_segments(cfg, long_para)
check_true("长段被切句", len(result.segments) > 3, f"segments={len(result.segments)}")
check_true("段落锚点标记", all("para_index" in s["flags"] for s in result.segments))

print("[contracts]")
result = norm(
    (0.0, 6.0, "第一段内容，用来验证契约。"),
    (6.0, 12.0, "第二段内容，同样验证契约。"),
)
source = SourceMeta(kind="audio", platform="rss", title="测试", extractor="selftest")
for i, seg in enumerate(result.segments):
    seg["idx"] = i
bundle = build_bundle(
    bundle_id="deadbeefdeadbeef",
    source=source,
    segments=result.segments,
    flags=result.flags,
    warnings=result.warnings,
    stats=result.stats,
    language="zh",
    language_probability=0.99,
    asr_engine="selftest",
    media=None,
)
payload = bundle.model_dump(mode="json")
check("契约校验通过", validate_bundle(payload), [])
check("章节非空", len(bundle.chapters) > 0, True)

bad = dict(payload)
bad["segments"] = [dict(payload["segments"][0], idx=3)]
check_true("idx 不连续被拦", validate_bundle(bad) != [])

print("[render markdown]")
check("时间码格式", ts(3725), "01:02:05")
check("时间码短格式", ts(65), "01:05")
report = degraded_report(bundle)
report.tags = ["恢复训练", "跑步"]
check("降级标记", report.degraded, True)
check_true("降级报告有内容", bool(report.tldr and report.sections))
md = render_markdown(bundle, report, extra={"llm_stats": {}, "mode": "degraded", "job_id": "test01"})
check_true("markdown 以 frontmatter 开头", md.startswith("---\n"), md[:40])
check_true("frontmatter 有标题", 'title: "测试"' in md)
check_true("frontmatter 有标签键", "tags:" in md)
check_true("frontmatter 有任务号", 'job: "test01"' in md)
check_true("frontmatter 有类型", "type: linkdigest" in md)
check_true("frontmatter 收尾后是正文", "\n---\n\n# 测试\n" in md)
check_true("元信息不再散落在正文", "原语言" not in md.split("---", 2)[2])
check_true("降级提示存在", "降级模式" in md)
over = report_from_payload({"one_liner": "x", "tags": ["a", "b", "c", "d", "e", "f", "g"]})
check("标签截到 5 个", len(over.tags), 5)
dedup = report_from_payload({"one_liner": "x", "tags": ["音频", " 音频 ", "文本", "校验"]})
check("标签去重", len(dedup.tags), 3)

print("[input]")
check(
    "shell 转义的 ? 被还原",
    normalize_input(r"https://www.youtube.com/watch\?v\=a1K2V8iDYQA"),
    "https://www.youtube.com/watch?v=a1K2V8iDYQA",
)
check(
    "JSON 转义的 / 被还原",
    normalize_input(r"https:\/\/youtu.be\/dQw4w9WgXcQ"),
    "https://youtu.be/dQw4w9WgXcQ",
)
check(
    "从整句里抠出链接",
    normalize_input("看这个 https://www.bilibili.com/video/BV1GJ411x7h7?p=2 挺好"),
    "https://www.bilibili.com/video/BV1GJ411x7h7?p=2",
)
check("尾部中文标点被剪掉", normalize_input("https://example.com/a.mp3，"), "https://example.com/a.mp3")
check("裸域名补协议", normalize_input("www.youtube.com/watch?v=xyz"), "https://www.youtube.com/watch?v=xyz")
check(
    "HTML 实体被还原",
    normalize_input("https://mp.weixin.qq.com/s?__biz=MzA5&mid=1&amp;idx=2"),
    "https://mp.weixin.qq.com/s?__biz=MzA5&mid=1&idx=2",
)
try:
    normalize_input("ftp://a.b/c")
    check_true("非 http 协议被拒", False)
except ValueError:
    check_true("非 http 协议被拒", True, "")

print("[errors]")
check_true(
    "代理 502 归因到网络",
    "代理" in explain_error("Tunnel connection failed: 502 Bad Gateway", "https://www.youtube.com/watch?v=x", cfg),
)
check_true(
    "机器人校验归因到 cookies",
    "cookies" in explain_error("Sign in to confirm you're not a bot", "https://www.youtube.com/watch?v=x", cfg),
)
check_true(
    "已删除内容有话说",
    "不可用" in explain_error("ERROR: Video unavailable", "https://www.youtube.com/watch?v=x", cfg),
)

print("[naming]")
check("英文标题压成 slug", slugify("How to Recover After a 100k!"), "How-to-Recover-After-a-100k")
check("中文标点被折叠", slugify("抽帧：方案 / 反斜杠\\与冒号:"), "抽帧-方案-反斜杠-与冒号")
check("空标题有兜底", slugify(None), "untitled")
check("超长标题被截断", len(slugify("x" * 200)), 40)

print("[language]")
check("en-US → en", normalize_language("en-US"), "en")
check("zh-Hans-CN → zh", normalize_language("zh-Hans-CN"), "zh")
check("zh_TW → zh", normalize_language("zh_TW"), "zh")
check("空值不猜", normalize_language(""), None)
check("未知码交给检测", normalize_language("klingon"), None)

print("[config + paths]")
from linkdigest import __version__  # noqa: E402
from linkdigest.config.env import (  # noqa: E402
    PROJECT_ROOT,
    env_float,
    env_int,
    find_project_root,
    resolve_path,
    user_data_dir,
)

check_true(
    "源码树能认出仓库根",
    PROJECT_ROOT is not None and (PROJECT_ROOT / "pyproject.toml").is_file(),
    str(PROJECT_ROOT),
)
# 模拟 pip 安装后的布局：site-packages 层级里没有 pyproject.toml
fake_sp = Path(tempfile.mkdtemp(prefix="linkdigest-sp-"))
fake_pkg = fake_sp / "lib" / "python3.12" / "site-packages" / "linkdigest" / "config"
fake_pkg.mkdir(parents=True)
check("安装态认不出仓库根", find_project_root(fake_pkg), None)
check_true("安装态数据目录落在用户目录", user_data_dir().name == "linkdigest", str(user_data_dir()))

anchor = Path(tempfile.mkdtemp(prefix="linkdigest-anchor-"))
check("相对路径锚在给定目录", resolve_path("./data", anchor), (anchor / "data").resolve())
# macOS 的 /tmp 是 /private/tmp 的符号链接，resolve() 会展开，所以右边也要 resolve
check("绝对路径原样保留", resolve_path("/var/tmp/x", anchor), Path("/var/tmp/x").resolve())

os.environ["LINK_DIGEST_SELFTEST_INT"] = "4h"
os.environ["LINK_DIGEST_SELFTEST_FLOAT"] = "abc"
check("坏整数回落默认", env_int("LINK_DIGEST_SELFTEST_INT", 7), 7)
check("坏小数回落默认", env_float("LINK_DIGEST_SELFTEST_FLOAT", 0.5), 0.5)
del os.environ["LINK_DIGEST_SELFTEST_INT"]
del os.environ["LINK_DIGEST_SELFTEST_FLOAT"]

os.environ["COOKIES_FILE"] = "/tmp/ck-probe.txt"
# 模型本身不读环境，映射发生在 load()——这才让 Settings 可以纯粹按参数构造
check("COOKIES_FILE 收进 Settings", load().cookies_file, Path("/tmp/ck-probe.txt").resolve())
del os.environ["COOKIES_FILE"]
check("版本号只有一处定义", cfg.pipeline_version, __version__)
check("默认 ASR 模型与示例一致", Settings(data_dir=anchor).asr_model, "small")

print("[config model + override]")
from linkdigest.config import override as config_override  # noqa: E402


def raises(fn) -> bool:
    try:
        fn()
    except Exception:  # noqa: BLE001 - 这里只关心「有没有拦住」
        return True
    return False


base = Settings(data_dir=anchor, llm_api_key="sk-probe")
check_true("配置不可变（frozen）", raises(lambda: setattr(base, "asr_model", "x")))
check_true("越界温度被拒", raises(lambda: Settings(data_dir=anchor, llm_temperature=9)))


def error_text(fn) -> str:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - 只取文案
        return str(exc)
    return ""


check_true(
    "越界报错说中文且带下限",
    "不能小于 60" in error_text(lambda: config_override.apply(base, ["asr.max_seconds=10"])),
    error_text(lambda: config_override.apply(base, ["asr.max_seconds=10"])),
)
check_true("非法 base_url 被拒", raises(lambda: Settings(data_dir=anchor, llm_base_url="api.openai.com")))

overridden = config_override.apply(base, ["asr.model=large-v3"])
check("覆盖生效", overridden.asr_model, "large-v3")
check("原对象不受影响", base.asr_model, "small")
check("环境变量名也能当键", config_override.apply(base, ["ASR_MODEL=medium"]).asr_model, "medium")
check_true("非白名单键被拒", raises(lambda: config_override.apply(base, ["data_dir=/tmp/x"])))
check("清空密钥即降级", config_override.apply(base, ["llm.api_key="]).llm_ready, False)
check("快照给密钥打码", base.snapshot()["llm_api_key"], "***")
check_true("快照不含明文密钥", "sk-probe" not in json.dumps(base.snapshot(), ensure_ascii=False))

# 白名单、环境变量别名、模型字段三者必须对得上：写错键名不会抛异常，只会静默失效
fields = set(Settings.model_fields)
mapped = {field for field, _ in config_override._FIELDS.values()}
check_true("覆盖键都指向真实字段", mapped <= fields, sorted(mapped - fields))
check_true(
    "每个可覆盖键都有环境变量名",
    set(config_override._FIELDS) <= set(config_override._ENV_ALIASES.values()),
    sorted(set(config_override._FIELDS) - set(config_override._ENV_ALIASES.values())),
)
env_example = (PROJECT_ROOT / ".env.example") if PROJECT_ROOT else None
if env_example and env_example.exists():
    documented = env_example.read_text(encoding="utf-8")
    undocumented = [key for key in config_override._ENV_ALIASES if key not in documented]
    check_true("可覆盖项都在 .env.example 里", not undocumented, undocumented)

print("[jobs + handoff]")
cfg = cfg.model_copy(update={"data_dir": Path(tempfile.mkdtemp(prefix="linkdigest-selftest-"))})
store = Store(cfg)
job = app_service.create_job(store, "https://www.youtube.com/watch?v=abcdefghijk")
store.update(
    job.id,
    title="How to Recover After a 100k Ultra",
    platform="youtube",
    stage="done",
    status="done",
    chars=30,
    degraded=1,
)
directory = finalize(store, job.id)
check_true("目录名以 _job_id 结尾", directory.name.endswith("_" + job.id), directory.name)
check_true("目录名带时间前缀", re.match(r"^\d{8}-\d{4}_", directory.name) is not None, directory.name)
check_true("目录名带标题", "How-to-Recover" in directory.name, directory.name)
check_true("job_dir 能反查回可读目录", store.job_dir(job.id) == directory, str(store.job_dir(job.id)))
check("store 绑定目录解析", store.job_path(job.id, "x"), directory / "x")
check_true("manifest 落盘", (directory / "manifest.json").exists())
check_true("配置快照随任务落盘", (directory / "config.json").exists())
check_true("索引落盘", (store.jobs_dir / "INDEX.md").exists())
check("前缀能定位任务", store.resolve(job.id[:6]).id if store.resolve(job.id[:6]) else None, job.id)

write_json(store.job_path(job.id, "transcript.json"), payload)
answer_path = export_handoff(store, job.id, bundle)
brief = (directory / BRIEF).read_text(encoding="utf-8")
check_true("交接包 BRIEF 存在", answer_path.parent.joinpath("BRIEF.md").exists(), str(answer_path))
check_true("BRIEF 含必填契约", '"one_liner"' in brief and '"body"' in brief and '"intent"' in brief)
check_true("BRIEF 三层分开", all(f"### 3.{i}" in brief for i in (1, 2, 3)))
check_true("BRIEF 不再给条数配额", "≤40 字" not in brief and "5-8 条" not in brief)
check_true("BRIEF 无未填占位符", "{{" not in brief)
template = json.loads((directory / "handoff/answer.template.json").read_text(encoding="utf-8"))
check("模板只放必填五件套", sorted(template), ["body", "intent", "one_liner", "sections", "tags"])
check_true("BRIEF 含正文与时间码", "[00:00]" in brief)
check_true("BRIEF 含应用命令", "--apply" in brief)
check_true("纯文本稿落盘", (directory / "handoff/transcript.md").exists())

try:
    apply_answer(store, job.id, {"tldr": []})
    check_true("空答案被拒", False)
except ValueError:
    check_true("空答案被拒", True, "")

try:
    apply_answer(store, job.id, {"body": "只有自由正文，没有一句话摘要，也应当被接受。"})
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
    "data_points": [{"label": "验证阈值", "value": "30 分钟", "start": "00:05"}],
    "quotes": [{"text": "第一段内容，用来验证契约。", "start": "00:00", "note": "原话"}],
    "appendix": "### 轮次对照\n\n| 轮次 | 结论 |\n| --- | --- |\n| 一 | 通过 |",
}
apply_answer(store, job.id, answer)
final = store.get(job.id)
check("应用后降级标记清除", final.degraded, False)
check("应用后记录来源", final.llm_model, "external-agent")
markdown_text = store.job_path(job.id, "report.md").read_text(encoding="utf-8")
check_true("报告已重渲染", "降级模式" not in markdown_text)
check_true("报告含外部来源标注", "外部 agent" in markdown_text)
check_true("intent 印在开头", "给准备复跑的人看" in markdown_text)
check_true("body 渲染成正文", "## 正文" in markdown_text and "深度由内容决定" in markdown_text)
check_true(
    "md 分段索引在正文之前",
    "## 分段索引" in markdown_text
    and markdown_text.find("## 分段索引") < markdown_text.find("## 正文"),
)
check_true("数据点不进页面", "## 数据点" not in markdown_text and "验证阈值" not in markdown_text)
check_true(
    "appendix 自带三级标题时仍补容器", "## 补充" in markdown_text and "### 轮次对照" in markdown_text
)
check_true(
    "appendix 自带二级标题时不再包一层",
    "## 补充" not in render_markdown(bundle, report_from_payload({"appendix": "## 轮次对照\n\n| a | b |"})),
)

bare = render_markdown(bundle, report_from_payload({"appendix": "| a | b |\n| --- | --- |"}))
check_true("appendix 无标题时自动加容器", "## 补充" in bare)

print("[injection]")
from linkdigest.capabilities.llm import LLMStats  # noqa: E402
from linkdigest.understand.chunking import chunk_bundle  # noqa: E402

# 同一份数据、同一个函数，只换配置：结果必须跟着变，否则「注入」就是假的
long_text = "".join(f"第{i}个要点说明了一些有差异的内容，用来撑满分块预算。" for i in range(1, 121))
long_result = text_to_segments(cfg, long_text)
for i, seg in enumerate(long_result.segments):
    seg["idx"] = i
long_bundle = build_bundle(
    bundle_id="cafebabecafebabe",
    source=source,
    segments=long_result.segments,
    flags=long_result.flags,
    warnings=long_result.warnings,
    stats=long_result.stats,
    language="zh",
    language_probability=0.99,
    asr_engine="selftest",
    media=None,
)
tight = len(chunk_bundle(cfg.model_copy(update={"llm_chunk_chars": 500}), long_bundle))
loose = len(chunk_bundle(cfg.model_copy(update={"llm_chunk_chars": 6000}), long_bundle))
check_true("分块预算按传入配置生效", tight > loose >= 1, f"500字→{tight} 块 / 6000字→{loose} 块")

stats = LLMStats(price_in=1.0, price_out=2.0)
stats.prompt_tokens, stats.completion_tokens = 1_000_000, 500_000
check("成本按注入单价算", stats.cost_usd, 2.0)

print("[render html]")
html_text = store.job_path(job.id, "report.html").read_text(encoding="utf-8")
check_true("html 同源落盘", '<article class="prose"' in html_text and "给准备复跑的人看" in html_text)
check_true("html 无占位残留", "{{" not in html_text)
check_true(
    "html 样式内联不引外部资源",
    "<style>" in html_text and "http" not in html_text.split("</head>")[0],
)
check_true("html 时间码渲染", 'class="ts"' in html_text)
check(
    "youtube 时间码跳到秒",
    seek_url("https://www.youtube.com/watch?v=abc", 210),
    "https://www.youtube.com/watch?v=abc&t=210s",
)
check(
    "b 站时间码跳到秒",
    seek_url("https://www.bilibili.com/video/BV1xx", 90),
    "https://www.bilibili.com/video/BV1xx?t=90",
)
check("未知平台不造链接", seek_url("https://example.com/a", 10), None)
check_true("html 无 a 嵌套", not re.search(r"<a [^>]*>(?:(?!</a>).)*<a ", html_text, re.S))
check_true("html 带打印样式", "@media print" in html_text)
check_true(
    "html 标签区在摘要之上",
    html_text.find('class="tags"') != -1 and html_text.find('class="tags"') < html_text.find('class="oneline"'),
)
check_true("html 摘要不含 intent 卡", 'class="intent"' not in html_text)
check_true("intent 收进页脚", 'class="intent-foot"' in html_text)
check_true(
    "html 分段索引在正文之前",
    "分段索引" in html_text and html_text.find("分段索引") < html_text.find("<h2>正文</h2>"),
)
check_true("html 无数据点表", "数据点" not in html_text and "验证阈值" not in html_text)
check("应用后状态为完成", final.status, "done")
check_true(
    "usage 记录外部模式",
    json.loads(store.job_path(job.id, "usage.json").read_text(encoding="utf-8")).get("mode") == "external",
)

print("[ingest routing]")
from linkdigest.ingest.classify import classify, detect_platform  # noqa: E402

check("youtube 判为视频", classify("https://www.youtube.com/watch?v=x"), "video")
check("公众号判为文章", classify("https://mp.weixin.qq.com/s/abc"), "article")
check("spotify 判为音频", classify("https://open.spotify.com/episode/abc"), "audio")
check("未知域名按文章", classify("https://example.com/post"), "article")
check("短链认得平台", detect_platform("https://b23.tv/xyz"), "bilibili")

print("[asr 适配器]")
# 本机通常只装了一个引擎，所以桩掉上游模块，只验适配契约：吃 PCM、算置信度、不编造概率。
import types  # noqa: E402
import wave  # noqa: E402

from linkdigest.capabilities.speech.mlx import MlxWhisperEngine  # noqa: E402

_seen: dict = {}


def _stub_transcribe(audio, **kwargs):
    _seen["audio"] = audio
    _seen["kwargs"] = kwargs
    return {
        "language": "zh",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": " 甲 ", "avg_logprob": -0.5},
            {"start": 2.0, "end": 4.0, "text": "乙", "avg_logprob": -1.0},
            {"start": 4.0, "end": 5.0, "text": "丙", "avg_logprob": None},
        ],
    }


_probe_module = types.ModuleType("mlx_whisper")
_probe_module.transcribe = _stub_transcribe
sys.modules["mlx_whisper"] = _probe_module
_probe_wav = Path(tempfile.mkdtemp()) / "probe.wav"
with wave.open(str(_probe_wav), "wb") as _fh:
    _fh.setnchannels(1)
    _fh.setsampwidth(2)
    _fh.setframerate(16000)
    _fh.writeframes(b"\x00\x00" * 16000)  # 1 秒静音

_mlx_dir = Path(tempfile.mkdtemp())
(_mlx_dir / "config.json").write_text("{}", encoding="utf-8")
(_mlx_dir / "weights.safetensors").write_bytes(b"")  # 只为过存在性检查，不真加载
_mlx = MlxWhisperEngine(cfg.model_copy(update={"asr_model": str(_mlx_dir)}))
_out = _mlx.transcribe(_probe_wav, "zh", 0.0)

check("mlx 模型名映射到 HF 仓库", MlxWhisperEngine(
    cfg.model_copy(update={"asr_model": "large-v3-turbo"})
).repo, "mlx-community/whisper-large-v3-turbo")
check("mlx engine_id", MlxWhisperEngine(
    cfg.model_copy(update={"asr_model": "large-v3-turbo"})
).engine_id, "mlx-whisper:mlx-community/whisper-large-v3-turbo")
check_true("mlx 收到 PCM 而不是路径", not isinstance(_seen["audio"], (str, Path)))
check("mlx 只传本地路径给上游（不触发下载）", _seen["kwargs"]["path_or_hf_repo"], str(_mlx_dir))
check("ASR_MODEL 写目录时 identity 用目录名", _mlx.engine_id, f"mlx-whisper:{_mlx_dir.name}")
check_true("mlx 不向 mlx_whisper 透传 device/compute_type", "device" not in _seen["kwargs"])
check("mlx 置信度换算与 faster 同源", _out.segments[0].confidence, 0.607)
check("mlx 缺 avg_logprob 记 None", _out.segments[2].confidence, None)
check("mlx 段文本去空白", _out.segments[0].text, "甲")
check("mlx 不编造语言概率", _out.language_probability, 0.0)
check("mlx duration 用末段补齐", _out.duration, 5.0)
_probe_module.transcribe = lambda audio, **kw: {"language": None, "segments": []}
_empty = _mlx.transcribe(_probe_wav, None, 9.0)
check("mlx 空结果 language 回落", _empty.language, "unknown")
check("mlx 空结果 duration 回落到入参", _empty.duration, 9.0)
del sys.modules["mlx_whisper"]

print("[asr 选引擎]")
import importlib.util  # noqa: E402

import linkdigest.capabilities.speech as _speech  # noqa: E402

_orig = (_speech.mlx_supported, _speech.available_engines, _speech._mlx_probe)

_speech.mlx_supported = lambda: True
_speech.available_engines = lambda: ["faster-whisper", "mlx-whisper"]
check("Apple Silicon 上 mlx 排第一", _speech._ordered_engines()[0], "mlx-whisper")
check("auto 解析到 mlx", _speech.resolve_engine(cfg), "mlx-whisper")

_speech.mlx_supported = lambda: False
check("非 Apple Silicon 保持登记顺序", _speech._ordered_engines()[0], "faster-whisper")

_speech.mlx_supported = lambda: True
_probe_dir = Path(tempfile.mkdtemp())
(_probe_dir / "config.json").write_text("{}", encoding="utf-8")
check_true(
    "只有 config.json 不算模型目录",
    MlxWhisperEngine(cfg.model_copy(update={"asr_model": str(_probe_dir)})).model_path is None,
)
(_probe_dir / "weights.safetensors").write_bytes(b"")
check(
    "配置路径命中本地模型",
    MlxWhisperEngine(cfg.model_copy(update={"asr_model": str(_probe_dir)})).model_path,
    _probe_dir,
)

# 模型集中放外置盘：ASR_MODEL 只写名字，靠 ASR_MODEL_DIR 定位
_model_root = Path(tempfile.mkdtemp())
for _sub in ("whisper-large-v3-turbo", "small"):
    (_model_root / _sub).mkdir()
    (_model_root / _sub / "config.json").write_text("{}", encoding="utf-8")
    (_model_root / _sub / "weights.safetensors").write_bytes(b"")
_dir_cfg = cfg.model_copy(update={"asr_model": "large-v3-turbo", "asr_model_dir": _model_root})
check("ASR_MODEL_DIR 按仓库名定位模型", MlxWhisperEngine(_dir_cfg).model_path,
      _model_root / "whisper-large-v3-turbo")
check("走 ASR_MODEL_DIR 时 engine_id 仍记仓库名", MlxWhisperEngine(_dir_cfg).engine_id,
      "mlx-whisper:mlx-community/whisper-large-v3-turbo")
check("ASR_MODEL_DIR 也认配置名当子目录", MlxWhisperEngine(
    cfg.model_copy(update={"asr_model": "small", "asr_model_dir": _model_root})
).model_path, _model_root / "small")

_probe_missing, _probe_reason = _speech._mlx_probe(
    cfg.model_copy(update={"asr_model": str(tempfile.mkdtemp())})
)
check("本地没权重 → 探针判不可用", _probe_missing, None)
check_true("原因写清不代下模型", "不代下" in _probe_reason, _probe_reason)

_speech._mlx_probe = lambda c: (None, "本地没有权重")
if importlib.util.find_spec("faster_whisper"):
    _fallback, _note = _speech.select_engine(cfg)
    check("auto 探针失败回落到 faster", type(_fallback).__name__, "FasterWhisperEngine")
    check_true("回落原因可读", "已回落到 faster-whisper" in _note, _note)

try:
    _speech.select_engine(cfg.model_copy(update={"asr_engine": "mlx-whisper"}))
    check("显式 mlx 不可用应报错", "没抛", "抛错")
except RuntimeError as _exc:
    check_true("显式 mlx 报错并指路 auto", "改成 auto" in str(_exc), str(_exc))


class _FakeMlx:
    name = "mlx-whisper"

    def transcribe(self, *a, **kw):
        raise AssertionError("不该走到真转写")


_speech._mlx_probe = lambda c: (_FakeMlx(), "")
_selected, _note = _speech.select_engine(cfg)
check("探针通过就用 mlx", _selected.name, "mlx-whisper")
check("首选跑不写降级说明", _note, "")

_speech.mlx_supported, _speech.available_engines, _speech._mlx_probe = _orig

if FAILED:
    print(f"失败 {len(FAILED)} 项：")
    for item in FAILED:
        print(" -", item)
    raise SystemExit(1)
print("全部通过")
