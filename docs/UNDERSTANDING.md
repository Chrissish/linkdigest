# 理解层技术流（Understanding Agent）

配置了模型 API 之后，`understand` 层如何把一份 `Transcript Bundle` 变成一份 `Report`。
全链路总览见 [ARCHITECTURE.md](ARCHITECTURE.md)，本文只展开「理解 agent」这一层——
它的路径选择、上下文预算、传输可靠性、提示词装配与失败策略。

## 1. 入口与产物

| | |
| --- | --- |
| 谁调用 | `app/pipeline.py`：新建任务走 `run_job()`，已有转写重跑走 `rerun_understand()`（`POST /api/jobs/{id}/understand`，不重新下载与转写） |
| 唯一输入 | `TranscriptBundle`（`transcript.json`，schema v1.0，来自确定性层） |
| 唯一输出 | `Report`（`report.json` → 渲染出 `report.md` / `report.html`） |
| 过程留痕 | `llm_debug.jsonl`（每次请求/响应）、`stages.jsonl`（阶段日志）、`usage.json`（用量与路径） |

理解层内部有**三条执行路径**，按优先级自动选择，选择结果记在 `path` 字段
（frontmatter、usage.json、报告页脚三处可见）：

```
                 ┌─ 配置了 API key？
Bundle ─▶ 判定 ──┤
                 ├─ 是 ─▶ 全文塞得下？──┬─ 是 ─▶ ① direct   单次直读      path=direct
                 │                     └─ 否 ─▶ ② map-reduce 分块并行汇总  path=map-reduce
                 │                              （① 失败也自动落到这里）
                 └─ 否 ─▶ ③ handoff    导出作业单交给外部 agent（见 ARCHITECTURE 的 ADR-7）
                          （② 汇总失败时另有一层 ④ degraded 抽取式兜底，标注 degraded）
```

## 2. 路径选择：单次直读的判据（`capabilities/llm/context.py`）

直读不是「塞得下就塞」，是一个三步估算：

**① 窗口推断。** 官方 API 不暴露窗口大小（`/models` 只给 id），按模型名从内置家族表
子串匹配（先命中先得）：`deepseek` 64K、`claude` 200K、`gemini` 1M、`gpt-4o` 128K、
`qwen`/`kimi`/`mistral` 32K……未知名一律回落 **32K** 保守值——宁可错走 map-reduce，
也不能把超限请求打出去吃 400。手填逃生口：`LLM_CONTEXT_TOKENS`。

**② token 估算。** 按语种构成加权：CJK ≈ 0.6 token/字、拉丁 ≈ 0.29 token/字（对齐
DeepSeek 分词器公开口径）。手填逃生口：`LLM_CHARS_PER_TOKEN`。

**③ 预算闸门。**

```
预算 = (窗口 − 4500 输出预留 − 2048 提示词预留) × 0.65 质量护栏
```

4500 是一次直读要写完整报告（正文 + 全部可选层）的输出预留；2048 是 spec 契约 +
元信息 + 章节锚点的开销；0.65 是质量护栏——**塞得下 ≠ 该塞满**，注意力稀释的
超长内容交给 map-reduce 的分块摘要更稳。估算值 = `estimate_tokens(提示词全文)`，
不超过预算才走直读。

实测参考：DeepSeek 64K 窗口预算约 38K tokens，41 分钟英文播客的提示词约 14.9K →
直读；3 小时中文播客（约 40K tokens）→ map-reduce。

## 3. 传输与可靠性（`capabilities/llm/__init__.py`）

`LLMClient` 是理解层唯一的出网通道，OpenAI 兼容协议，端口定义在
`capabilities/llm/base.py`，换厂商只动 transport 适配：

- **JSON 模式**：`response_format: json_object`，解析失败先剥代码围栏、再截
  首尾大括号重试；
- **输出预算自愈**：`chat_json()` 连续 3 次拿不到合法 JSON 时把 `max_tokens` 逐次
  翻倍（上限 16000）。带思考的模型会先烧推理再吐正文，预算紧了 content 被截断，
  翻倍预算比换提示词管用；`finish_reason=length` 同理触发；
- **max_tokens 口径**：reduce 稿 6000、直读 9000（直读正文长得多，口径紧了会白烧
  一次重试）、争议层补抽 1200；
- **全程记账**：`stats` 累加 calls / prompt_tokens / completion_tokens / seconds /
  cost_usd / errors，逐请求写进 `llm_debug.jsonl`；`usage.json` 里 `llm` 是本任务
  累计、`last` 是最后一次运行的明细。

## 4. 路径①：单次直读（`understand/mapreduce.py :: _direct_report`）

```
full_text(bundle)          全文带锚点：[mm:ss] 或 [段N]，口径与分块一致
        +
direct.user.md 模板        元信息（标题/平台/作者/时长/语言/字数）+ 机械章节切分
        ↓
system = spec_text()       由 templates/spec.md 渲染的产出契约（见第 6 节）
        ↓
chat_json(max_tokens=9000) 一次调用出完整 answer JSON
        ↓
report_from_payload()      类型收口 → Report
```

直读的优势是模型通读全文：上下文连贯、正文可以写满（实测 41 分钟内容正文 4-5 千字，
而 map-reduce 约 2-3 千字）、只花一次调用的钱。

可选层兜底与直读共用（见第 5 节的 `_reduce_payload`），直读失败（超限 / 网络 /
JSON 连续解析失败）自动退回 map-reduce，错误记入 `stats.errors`，不浪费这次会话。

## 5. 路径②：map-reduce（`_map_chunk` + `_reduce_payload`）

```
chunk_bundle(cfg, bundle)      按 llm_chunk_chars（默认 6000 字）装箱，
                               每块带 start/end 与锚点前缀
        ↓
ThreadPoolExecutor ≤4 并行     每块一次 map 调用（map.system.md）出摘要
        ↓
digest_block() 拼接            块摘要按序拼接（截到 4×chunk_chars）
        + chapters_block()     机械章节锚点
        ↓
reduce.user.md                 元信息 + 摘要拼盘 + 章节锚点
        ↓
chat_json(max_tokens=6000)     汇总成完整报告
```

**失败策略**：单块失败跳过该块并记错误（其余块照常）；reduce 整体失败 →
`degraded_report()` 抽取式兜底并标注 `degraded`。原则与全局一致：
能降级就降级并标注，绝不静默产出半份内容。

**两层补漏**（`_reduce_payload`，直读与 map-reduce 共用）：

1. **可选层全空守卫**：正文可观而 `tldr / data_points / quotes / terms /
   controversies` 整层留空——这属于漏不是简，把自检要求说透再问一次完整 JSON；
2. **争议层单层守卫**（`_recover_controversies`）：争议层过滤后为空、而正文里
   出现 ≥2 个争议标记（「有争议」「未证实」「缺乏研究」「说法不一」……）时，
   只把正文喂回去做一次 1200 tokens 的小抽取，不重跑全文。抽取口径收得很窄：
   只收「两方说法冲突」与「明确被指未证实」，不收开放的不确定性（那是
   `open_questions` 的事），上限 6 条。

**payload 收口**（`understand/payload.py`）是模型 JSON 进 `Report` 的最后一道闸：

- `_line()`：`{text, start}` 对象混进字符串数组时拆成一句话附时间码；
- `_prose()`：dict / list / 字符串化字面量混进 Markdown 字段（appendix）时压成
  `###` 标题 + 列表，渲染层永远不会吐出 `{'标题': [...]}` 字面量；
- `_is_body_repeat()`：appendix 与正文的字符二元组覆盖率 ≥0.7 时判为「正文重排」
  整体丢弃。

## 6. 提示词装配：spec.md 是单一事实源

理解层的 system prompt 不是手写的，是从 `resources/templates/spec.md` 渲染出来的
（`contracts/spec.py :: spec_text()`），与 handoff 作业单 `BRIEF.md` 第 3 节共用同一份——
**体例规则只允许改 spec.md 一处**，管线与外部 agent 的口径才不会分叉。占位符
`{{timecode_rule}}` 按「有无时间轴」换口径（视频用真实 `mm:ss`，文章留空）。

| 提示词资源 | 用在哪 | 内容 |
| --- | --- | --- |
| `map.system.md` / `map.user.md` | 路径② 每块 | 单块摘要的角色与格式 |
| `reduce.user.md` | 路径② 汇总 | 元信息 + 块摘要拼盘 + 章节锚点 |
| `direct.user.md` | 路径① | 元信息 + 全文 + 章节锚点 |
| `controversies.user.md` | 争议层补抽 | 只收冲突与未证实，不收开放不确定性 |
| `templates/spec.md` | ①②③ 共用 | 产出契约：必填五件套 + 不可违反 + 可选层 |

system prompt 由 `ROLE + spec_text() + DELIVERY` 三段拼成；user prompt 由模板
`str.format()` 填充。改提示词去 `resources/prompts/`，不碰 Python。

## 7. 产物与可观测性

| 产物 | 内容 |
| --- | --- |
| `report.json` | 结构化 Report（`schemas/` 有机读定义） |
| `report.md` / `report.html` | 渲染稿，frontmatter 与页脚带 `path`（direct / map-reduce） |
| `usage.json` | `llm` 本任务累计 + `last` 最后一次运行明细 + `path` |
| `llm_debug.jsonl` | 每次请求/响应的模型、max_tokens、finish_reason、tokens、耗时 |
| `stages.jsonl` | understand / report 阶段日志 |

报告页脚会写「模型调用 N 次 / tokens · 路径 直读全文」——理解层怎么跑完的，
不用翻日志就能看到。

## 8. 失败矩阵（理解部分）

| 失败 | 处理 |
| --- | --- |
| 未配置 key | 走 handoff 路径（ADR-7），不进理解层 |
| 直读请求超限 / 网络 / JSON 连续 3 次解析失败 | 记错误，自动退回 map-reduce |
| 单块 map 失败 | 跳过该块并记错误，其余块照常 |
| reduce 失败 | `degraded_report()` 兜底，报告标注 degraded |
| 输出被 `finish=length` 截断 | `chat_json` 自动翻倍 max_tokens 重试（≤16000） |
| 可选层 / 争议层整层为空 | 两层守卫分别补救（见第 5 节） |

## 9. 成本实测（2026-11，DeepSeek 64K，41 分钟英文播客）

| 路径 | 调用 | tokens | 成本 | 耗时 |
| --- | --- | --- | --- | --- |
| 直读 | 1 | 14,991 + 5,009 | $0.0053 | ~35s |
| map-reduce（早期口径） | 多次 | 更多 | ~$0.008+ | 更长 |

直读路径省 30-40% token，且正文更长。瓶颈仍不在理解层——ASR 才是绝对大头
（见 ARCHITECTURE 的成本预算表）。
