# 详细设计

## 1. 模块与职责

| 层 / 模块 | 职责 | 关键函数 |
| --- | --- | --- |
| `conventions` | 纯工具：时间码、命名、文本度量、JSON 落盘、资源读取 | `ts()`、`parse_ts()`、`slugify()`、`read_json()` |
| `config` | 全部可调项集中一处，环境变量驱动，构造时定值 | `Settings`、`settings.ensure_dirs()` |
| `bootstrap` | 显式副作用：代理写环境、建目录 | `bootstrap()`、`install_proxy()` |
| `net` | 出口与传输：代理、HTTP、探活、cookies、失败归因 | `reachability()`、`stream_to()`、`explain_error()` |
| `capabilities.ffmpeg` | 音轨抽取与时长闸门 | `to_wav(src, dst)`、`guard_duration(path)` |
| `capabilities.speech` | ASR 端口 + 两引擎适配 | `transcribe(wav, language)`、`available_engines()` |
| `capabilities.llm` | 理解层端口 + OpenAI 兼容传输 + 用量记账 | `LLMClient.chat_json()`、`parse_json()` |
| `capabilities.ocr` | 图文源内嵌图片（可选） | `ocr_images(paths)` |
| `contracts` | 契约模型、构造、机械切章、校验、schema 导出 | `build_bundle(...)`、`validate_bundle(dict)` |
| `clean` | 规则清洗、去幻觉、切段指标 | `normalize_segments(raw)`、`text_to_segments(text)` |
| `ingest` | 链接判定与采集，归一为「音频文件 or 正文」 | `fetch(url, workdir)`、`classify(url)`、`bundle_id_for(seed)` |
| `understand` | 分块、提示词装载、map/reduce、降级 | `build_report(bundle)`、`degraded_report(bundle)` |
| `render` | Markdown / HTML 渲染与双形态落盘 | `render_markdown(...)`、`render_html(...)`、`save_report(...)` |
| `handoff` | 无密钥时的交接包导出与答案回收（三层契约：必填 / 不可违反 / 可选） | `export_handoff(job_id, bundle)`、`apply_answer(job_id, payload)` |
| `jobs` | SQLite 状态 + 可读产物路径 + 阶段日志 + 索引 + 收尾 | `Store.resolve()`、`job_dir()`、`finalize()`、`refresh_index()` |
| `app.pipeline` | 阶段编排、进度、错误处理 | `run_job(job_id, store)` |
| `app.service` | 用例：建任务、应用答案、补渲染、清理 | `create_job()`、`apply_external()`、`prune()` |
| `api` / `cli` | 交付形态：HTTP 路由 / 终端排版 | `app`、`main()` |

依赖方向单向（详见 ARCHITECTURE 的分层图）：`app → {ingest, clean, contracts, understand, render, handoff, jobs}`，
`handoff → {render, understand, jobs}`，`understand → {capabilities.llm, contracts}`，
`ingest → {net, contracts}`，`capabilities → net|config`，`conventions` 不依赖任何层。
`contracts.models` 不被任何下层反向依赖，是纯数据。

非代码资源不在这张表里：提示词在 `resources/prompts/`，版式在 `resources/templates/`，
报错话术在 `resources/messages/`，幻觉黑名单在 `resources/asr/`，网页在 `resources/web/`。

## 2. 契约：Transcript Bundle

字段语义见 `schemas/transcript_bundle.schema.json`。三个容易踩的点：

1. **`segments[].idx` 必须连续从 0 递增**，`validate_bundle` 会拦。下游切块按 idx 定位。
2. **图文源的 `start`/`end` 是段落下标**（0,1,2…），并带 `flags: ["para_index"]`。
   这样图文与音视频共用同一结构，代价是时间轴无意义——下游渲染时按 `flags` 判断，不显示时间码。
3. **`quality.flags` 是计数字典**，不是布尔。它同时是清洗日志和调参依据：
   `repetition_collapsed` 高 → 音频有回声或模型卡带；`hallucination_dropped` 高 → 大量静音。

契约变更是破坏性的。加字段走 minor（下游忽略未知字段），改语义走 `bundle_version` 升位。

## 3. job 状态机

```
queued → ingest → download* → extract → asr → normalize → bundle → understand → report → done
                    ↑                                                              
              （仅 yt-dlp 路径；RSS/直链/本地跳过）
任一阶段抛异常 → failed（保留已产出文件，error 落库）
```

- `progress` 是单调递增的粗粒度锚点（见 `pipeline.PROGRESS`），不追求精确——问进度的人要的是「还在动」。
- `stages.jsonl` 记录每阶段真实耗时，`usage.json` 汇总，用于后续成本回归。
- 失败不自动重试。重试会掩盖真正的问题（cookie 过期、模型没装），交给用户判断。

## 4. 数据库

```sql
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,            -- uuid4 hex[:12]
  input TEXT NOT NULL,            -- URL 或本地路径
  input_kind TEXT,                -- url | file
  status TEXT,                    -- queued | running | done | failed
  stage TEXT,                     -- 见 STAGES
  progress REAL,
  title TEXT, platform TEXT,
  duration_sec REAL, chars INTEGER,
  llm_model TEXT, degraded INTEGER, cost_usd REAL,
  error TEXT,
  created_at TEXT, updated_at TEXT
);
```

单表足够。任务历史只是展示，不做查询语言。产物全在文件系统，DB 只存索引与状态——
这样备份/迁移只需拷目录。

## 5. 清洗规则表

| 规则 | 触发条件 | 计数 key |
| --- | --- | --- |
| 空白丢弃 | 清洗后空串 / 全标点 | `empty_dropped` |
| 单字循环 | 长度 ≤2 且字符全同（"啊啊啊"） | `hallucination_dropped` |
| 幻觉黑名单 | 归一化后命中内置列表或 `data/hallucinations.txt` | `hallucination_dropped` |
| 段内卡带 | 同一词连续 ≥4 次 | `repetition_collapsed` |
| 跨段卡带 | 相邻段归一化文本相同 | `repetition_collapsed` |
| 时间戳倒挂 | `end ≤ start+0.05` 或与上段重叠 >0.5s | `clamp_fixed` |
| 碎片合并 | 长度 <4 字符且时长 <1.2s | `merged` |
| 碎句拼接 | 上段无句末标点、长度 <60、间隔 <1s | `merged` |

文本归一顺序固定：NFKC → 去零宽 → 全角空格 → 多空格折叠 → 移除 CJK 间空格。
顺序错了会出现「中文之间残留空格」或英文单词被粘合。

黑名单可扩展：往 `data/hallucinations.txt` 每行写一条，无需改代码。
比较用 `_key()`（去标点、小写），所以大小写和标点差异不会漏。

## 6. 分块与提示词

**切块**：按字符预算（`LLM_CHUNK_CHARS`，默认 6000）贪心装箱，行格式 `[mm:ss] 文本`。
时间码入 prompt 是为了让模型能返回值，而不是让它学会读时间。

**map**：每块独立、可并行（`ThreadPoolExecutor(4)`）。输出固定 schema：`summary/points/data/quotes/terms`。
单块失败不致命——记 `stats.errors`、返回空摘要，报告里会体现覆盖缺口。

**reduce**：输入是全部块摘要 + 机械章节表。要求模型输出 `sections` 且时间范围必须落在给定章节内，
这是防幻觉的硬约束：模型可以合并章节，但不能凭空造时间。

**预算**：6000 字/块 ≈ 6k-9k token；reduce 输入约 `块数 × 200` 字。1 小时内容约 15 块，
总成本一美分级。超长内容（>4h）建议先切分——见 `ASR_MAX_SECONDS`。

**降级**：`degraded_report()` 只做结构：按章节取前三段、按长度筛要点、按是否含数字挑金句。
`degraded=True` 会渲染成顶部警告条。这条路径必须永远可用，它是测试基线。

## 7. HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | ffmpeg / ASR 引擎 / 模型就绪状态，前端启动即调 |
| POST | `/api/jobs` | `{url}` → job |
| POST | `/api/upload` | multipart 文件 → job（本地文件同一条管道） |
| GET | `/api/jobs` | 历史列表 |
| GET | `/api/jobs/{id}` | 状态 + 阶段耗时 + 产物存在标记 |
| GET | `/api/jobs/{id}/report` | Markdown 渲染成 HTML |
| GET | `/api/jobs/{id}/markdown` | 原始 Markdown |
| GET | `/api/jobs/{id}/bundle` | 契约 JSON |
| GET | `/api/jobs/{id}/download/{name}` | 白名单文件下载 |

前端 1.2s 轮询，不做 SSE：轮询在这个粒度上更省事，失败重连天然幂等。

## 8. 扩展点

**加平台**：在 `ingest/platforms.py` 的 `PLATFORMS` 加域名，若要专门路线就在 `ingest/router.py`
里选路径、并按需新增一个 `ingest/<平台>.py`。
优先级：官方 RSS/直链 > HTML 捞音频 > yt-dlp > 无头浏览器截图兜底（未实现）。

**加 ASR 引擎**：写一个 `capabilities/speech/<引擎>.py`，实现
`transcribe(wav, language, duration) -> ASRResult` 与 `engine_id`，在 `_REGISTRY` 里登记一行。
不要在上层写 `if engine == ...`。

**换模型厂商**：`capabilities/llm/openai.py` 换成任何提供 `complete(payload) -> dict` 的传输类即可；
协议不同（非 OpenAI 兼容）也只影响这一个文件。

**说话人分离**：`Segment.speaker` 字段已预留。接入点在 `clean` 之后、`build_bundle` 之前，
输出 `list[(start, end, speaker)]` 与段做时间重叠匹配即可，契约不用改。

**A2UI（二期）**：`Report` 已经是结构化 JSON，映射到 A2UI 组件树是一次纯转换：

| Report 字段 | A2UI 组件 |
| --- | --- |
| `tags` | `TagGroup`，放在摘要之上；≤5 个，点击即按标签过滤同库内容 |
| `intent` | 页脚或侧栏元信息（读者与目的），据此决定各组件的默认展开状态 |
| `one_liner` / `tldr` | `Text` / `List` 卡片 |
| `body` | `Markdown` 块，可折叠；长内容惰性渲染 |
| `sections` | 可折叠 `Card` 列表，`start/end` 作为跳转锚点 |
| `data_points` | `Table` |
| `quotes` | `Quote` + 「跳到原片」按钮（action 回传时间码） |
| `open_questions` | `Checklist`（用户勾选追问） |
| `appendix` | 原样渲染的 `Markdown` 容器（表格 / 代码 / 时间线） |

关键是 `sections[].start` 已经带真实秒数，A2UI 里的 action 可以直接驱动播放器 seek——
这才是「信息互通效率」的落地方式，而不是换个好看的皮。

## 9. 待办（按价值排序）

1. **缓存复用**：同一 `audio_sha256` 不重复转写。改一行 `bundle_id` 命中逻辑即可。
2. **字幕轨优先**：B站/YouTube 有官方字幕时直接取，跳过 ASR（快 10 倍且更准）。
3. **时长切分**：>4h 内容按章节切片并行转写再拼接。
4. **批量**：一次粘贴多个链接，按序排队。
5. **报告口径**：同一作者/系列的历史报告做对比（新增 vs 重复观点），这是差异化点。

## 10. 环境坑与对策（都是实测踩过的）

| 现象 | 原因 | 对策 |
| --- | --- | --- |
| `open() got an unexpected keyword argument 'metadata_errors'` | PyAV 19 移除了该参数，faster-whisper 1.2.x 仍在传 | 不传文件路径，自己读 PCM 成 `np.ndarray` 喂模型（`asr._load_pcm`） |
| `pip install -e .` 报 `EEXIST: mkdir ...tmp...` | pip 在自定义 TMPDIR 下建临时目录冲突 | 不做 editable 安装，往 site-packages 写 `linkdigest.pth` 指向 `src`（`scripts/setup.sh` 已内置） |
| `No module named 'setuptools'` | Python ≥3.12 的 venv 默认不带 setuptools | `scripts/setup.sh` 先装 setuptools/wheel |
| `Form data requires "python-multipart"` | FastAPI 的 UploadFile 依赖 | 已加入依赖清单，别漏 |
| `paddlepaddle` 装不上 | 无 cp313 wheel | OCR 用 `rapidocr`（onnxruntime），或退到 Python 3.11 |
| PyPI 慢到超时 | 直连 pypi.org 常 5-10s/请求 | 走清华镜像 `-i https://pypi.tuna.tsinghua.edu.cn/simple` |
| HF 模型下不动 | huggingface.co 不通 | `export HF_ENDPOINT=https://hf-mirror.com` |
| ASR 同音错字（"抽帧"→"抽证"） | 小模型能力所限，规则无法修 | 换 `large-v3`；或维护术语表做后纠（二期） |

**实测基线**（M 系列 CPU，96 秒中文 TTS 音频，`ASR_MODEL=small`）：

| 阶段 | 耗时 |
| --- | --- |
| 抽音轨 | 0.08s |
| ASR | 15.5s（约 6 倍实时） |
| 清洗 + 落盘 | 0.03s |
| map + reduce（假模型） | 0.09s |

即 `small` 模型 1 小时音频约 10 分钟；`large-v3` 约 25-40 分钟，质量显著更好。
"转写 1 小时音频"是 MVP 的绝对瓶颈，任何优化都应先动这里。
