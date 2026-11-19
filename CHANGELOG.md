# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.5.0] - 2026-11-20

### 新增

- **单次直读路径**：上下文窗口够用时一次调用通读全文出完整报告，省 30-40% token
  且正文更长（41 分钟播客实测 1 次调用 1.5 万+5 千 tokens、约 $0.005、35 秒）。
  窗口按模型名家族表推断（deepseek 64K / claude 200K / gemini 1M，未知名回落 32K），
  token 按语种构成加权估算（CJK 0.6/字、拉丁 0.29/字），超预算或调用失败自动退回
  map-reduce。手填逃生口 `LLM_CONTEXT_TOKENS` / `LLM_CHARS_PER_TOKEN`。
- 理解层技术流文档：[docs/UNDERSTANDING.md](docs/UNDERSTANDING.md)——三条执行路径、
  直读判据、传输自愈、补漏守卫、提示词装配。ARCHITECTURE 分层图 / 数据流图补直读
  分支，新增 ADR-13。
- 争议层补抽：模型把「存疑与争议」整层留空而正文明确写出争议时，只把正文喂回去
  做一次小抽取（≤1200 tokens），不重跑全文。口径只收「两方说法冲突」与「明确被指
  未证实」，不收开放的不确定性（那是 open_questions 的事），上限 6 条。
- 报告 frontmatter 与页脚带 `path`（direct / map-reduce），页脚区分「本轮」与
  「累计」用量；`POST /api/jobs/{id}/understand` 重跑也记账。

### 修复

- 「补充」区渲染出 `{'标题': [...]}` 字面量：模型把 dict / list 塞进本该是 Markdown
  的 appendix 字段，原来直接 `str()` 落盘。现在统一压成 `###` 标题 + 列表，
  字符串化的 JSON / Python 字面量也会还原；与正文七成以上重复的附录判为「正文
  重排」整体丢弃。
- 直读输出预算写死 6000 tokens，长内容撞 `finish=length` 被截断后自动翻倍重试，
  白烧一次调用。现在直读传 9000，reduce 保持 6000。
- spec 收紧三条：正文写了「有争议 / 未证实 / 缺乏研究」就必须进 `controversies`；
  转写给出的头衔不许照抄放大（ASR 把硬件负责人读成 CEO 会跟着写实）；
  `appendix` 必须是 Markdown 文本，不要输出 JSON 结构。

## [0.4.1] - 2026-11-19

### 新增

- 前端中栏报告改为直接内嵌独立报告页（iframe）：一份结构化渲染器两处共用，
  速览卡、引用块、可点击时间码在页面内和移动端 tab 下都可用。高度按内容自适应
  并跟随变化，`?v=<更新时间>` 防止重跑后看到缓存的旧报告。

### 修复

- 可选层被模型整层省略：0.4.0 的 spec 把「可选」「拿不准就删」表述得过松，同一
  输入会随机产出没有速览 / 数据点 / 原话 / 术语的报告。现在省略的判定收紧为
  「正文里有没有对应素材」，且正文超过 600 字而可选层全空时自动带自检要求重问
  一次模型。
- handoff 只在降级任务导出：改为每个 done 任务无条件导出（整链与重跑理解两条
  路径都覆盖）。此前 API 配好后 handoff 反而消失了，其实是从没生成过。
- 重跑理解不写 `cost_usd` 回任务库。
- 前端改动刷新无效：静态资源没有 `Cache-Control`，浏览器启发式缓存直接用旧
  `app.js`。现在 `/` 与 `/static/*` 强制协商缓存（304 几乎零开销），`app.js`
  引用带内容哈希版本号，文件一变 URL 即变。

## [0.4.0] - 2026-11-18

### 新增

- `JOB_WORKERS`：同时在跑的任务数上限（默认 5）。瓶颈通常在 ASR——mlx-whisper 走
  Metal，多任务转写会抢 GPU，调太大反而互相拖慢。
- 粘贴框支持一次提交多个链接：按换行 / 分号等分隔符拆分，逐个建任务进队列。
- 支持 `creators.spotify.com` / `podcasters.spotify.com` 的单集链接：页面里挖出
  anchor.fm 的 RSS，取 enclosure 音频直链；出口代理不可达时重探并重试。
- `LLM_THINKING`：DeepSeek 系思考模式开关。0=关闭（结构化摘要建议关，带思考的模型
  会先烧推理 token，长转写分块下容易把 max_tokens 烧穿）；1=开启；不设=跟厂商默认。
  单任务覆盖键 `llm.thinking` 同步开放。
- 失败任务可「重新理解」：只要转写产物在（比如卡死在理解阶段的），复用转写只重跑
  模型理解与报告，不重新下载与转写。前端失败行出对应按钮。
- 理解阶段看门狗 `UNDERSTAND_TIMEOUT`（默认 30 分钟）：线程挂死时 httpx 的请求级
  超时救不了「没有请求在飞」的僵局，整阶段硬上限兜底，到点判失败、可重试。
- 服务启动时把遗留的 queued/running 任务标记为失败（上次进程退出时正在跑的任务
  不会有人接着跑），前端不再无限转圈，重试入口也解禁。
- `llm_debug.jsonl`：每次 LLM 调用前后往任务目录写一行事件（请求参数 / finish_reason
  / 用量 / 错误），排查「卡在哪一次调用」不用再猜。
- 产出规范单一事实源：`templates/spec.md` + `contracts/spec.py`。交接单（handoff）
  与管线提示词从同一份 spec 渲染，必填 schema、去重口径、body/sections 分工不再
  两边各维护一份。

### 变更

- 网页端改三栏工作台：左栏粘贴操作区 + 执行中队列，中间报告阅读区，右栏历史任务。
  左右栏可收起；页面收窄自动收起；≤680px 走底部 tab 栏（当前报告 / 添加任务 / 历史）。
- 报告正文（body）不再设 800 字上限，深度由内容决定，与交接单口径一致。
- `chat_json` 解析失败自动重试（最多 3 次，输出预算逐次翻倍至 16000）：
  带思考的模型在预算紧了时 content 会被截断甚至为空，翻倍预算比换提示词管用。
- 报告不再产出时间线模块：与 sections 的分段时间码重复，禁令在 spec 单点维护。
- 历史任务的标题与「打开」由按钮改为链接：左键仍是页内展开，右键可「在新标签页
  打开 / 复制链接」。已完成的任务指向 `/api/jobs/{id}/html`。
- 网页端资源读取带进程内缓存，改 `index.html` / `style.css` 后要重启服务才生效
  （`app.js` 走静态挂载不受影响）。

### 修复

- 只收起左栏时布局塌陷：三栏 grid 全靠 auto-placement，中栏被排到第 1 列（0 宽）。
  三块显式声明 `grid-column` 后不再受 DOM 顺序影响。
- 打开报告时页面抖一下：无条件 `smooth scrollIntoView` 叠加滚动条出现引起的横向
  挤压。改为条件触发即时滚动 + `scroll-margin-top` + `scrollbar-gutter: stable`。
- 执行中的任务卡片溢出右栏：`min-width: auto` 连锁把内容最小宽顶破列宽，逐层归零。
- 「重新理解」成功后漏写 `cost_usd`，成本账本只在整链重试时更新。
- Spotify creators 链接此前不认平台正则，整条任务直接 403 失败（见新增）。

## [Unreleased]

### 计划中

- 任务队列持久化：进程重启后能续跑未完成的任务
- 报告正文的分节跳转目录
- 更多正文抽取站点的适配

## [0.3.0] - 2026-11-17

### 新增

- `ASR_MODEL_DIR`：本地模型根目录。`ASR_MODEL` 只写模型名，靠它定位到具体目录，
  报告与配置快照仍记仓库名，不把随机器变的绝对路径写进产物。查找顺序：
  `ASR_MODEL` 本身是目录 → `ASR_MODEL_DIR/<仓库名末段>` → `ASR_MODEL_DIR/<ASR_MODEL>`
  → HF 缓存。只查不下载。
- 网页端历史任务改成列表：状态筛选（全部 / 进行中 / 完成 / 失败，带计数）、
  全选当前筛选、多选后批量删除，删除要二次确认。
- `DELETE /api/jobs/{id}` 与 `POST /api/jobs/prune`：按精确 id 删，目录进废纸篓。

### 变更

- `GET /api/jobs` 返回 `{jobs, counts, status, limit, offset}`（原先为裸数组），
  支持 `status` 筛选与 `offset` 分页，`limit` 上限 500。
- 还在排队/运行的任务拒删（409）：目录被搬走后管道会继续往旧位置写，只留残骸。
- 删任务后重建 `INDEX.md`，CLI `--prune` 与网页删除同一口径。
- Apple Silicon 上 `ASR_ENGINE=auto` 默认走 mlx-whisper（Metal）。判定链是
  「架构 → 本地模型文件 → 真加载一次」，任一不过回落 faster-whisper，原因写进报告的
  质量说明。显式指定引擎时不降级，失败直接报错并指路 `auto`。程序内不下载模型。
- `avg_logprob_to_conf` 上移到 `speech/base.py` 由两个引擎共用：换引擎后平均置信度、
  以及「平均置信度偏低」告警的阈值保持可比。

### 修复

- 首页样式改由服务端内联注入：聊天内置预览这类容器会丢弃 `<link rel="stylesheet">`
  却不拦脚本，页面呈现裸 HTML。`style.css` 仍是唯一样式源。
- 标题栏版本号写死成 `v0.1`（落后两个发布），改由 `/api/health` 的 version 填充。
- 历史任务的时间显示的是 UTC（`slice(11,16)` 直接截 ISO 串），按本地时区格式化。
- 状态页 ASR 徽章把**装了的引擎**并排显示成「faster-whisper / mlx-whisper」，
  读起来像两个引擎同时在跑。改为报这一进程实际会用的那个（`preview_engine`：架构 +
  本地模型文件，不加载权重），装了哪些降为悬停提示；启动横幅同一口径。
- mlx 适配器漏读 `avg_logprob`，导致 `confidence` 恒为 None——报告置信度显示 n/a，
  低置信度告警永不触发。现在与 faster-whisper 同一套换算。
- mlx 适配器直接收 16k float32 PCM，不再让上游对同一段音频重复起一次 ffmpeg。
- mlx 的 `language_probability` 改记 0.0（未知），不再谎报 1.00；交接单在概率未知时只报语言。
- mlx 的 `duration` 用末段结束时间补齐，入参传 0 时不再返回 0。

## [0.2.0] - 2026-11-17

报告体例收紧：一份报告只留一条叙事线，重复的模块删掉，读的位置与查的位置分开。

### 变更（报告版式，对外可见）

- **分段从「解读」降为「索引」**：`report.md` / `report.html` 里 `## 分段解读` 改为 `## 分段索引`，
  一行一条（时间码 + 标题），不再展开成每段三到五条要点。正文是唯一的叙事线。
  起因：结构化强的内容（编号清单、按周时间线）正文按顺序讲完，分段再逐条列一遍，
  同一批内容读者要读两遍。
- **HTML 去掉独立的目录块**：原来「分段」目录与「分段解读」是同批标题的两次呈现，
  现在合并成一处索引，时间码仍可跳回原片。
- **html 补上「正文」小标题**：此前正文块没有 `h2`，夹在「速览」与「分段索引」之间看不出来。
- **废弃 `key_points`**：它和 `tldr` 都是短句摘要，两者并存必然重复。模型字段保留（`exclude=True`，
  读得进老数据、不再输出），答案不再读取，渲染不再出现。
- `sections[].points` 回到细节层：页面上不渲染，只做下游数据；页面索引只取 `title` 与时间码。
- **`data_points` 不再渲染**：指标与数值正文里都讲过，单独成表只是重复；脱离语境的一列数字
  也不好读。字段仍写进 `report.json` 供下游取用，`report.md` / `report.html` 里不再出现「数据点」。
- **分段索引前移到正文之前**：顺序改为 速览 → 分段索引 → 正文。先给骨架再给内容，
  索引才起到目录的作用；标题要能单独看懂。
- 契约新增 3.6 节「`body` 与 `sections` 的分工」：正文给人读，分段给人查。

### 变更（内部重构，对外行为不变）

- 代码按层拆分：`conventions / config / net / capabilities / contracts / clean / ingest /
  understand / render / handoff / jobs / app`，依赖只向下。
- 外部依赖收进 `capabilities/`：一个能力一个端口（`base.py`）+ 每实现一个文件，
  ASR 引擎与模型都可换而不动业务代码。
- 非代码资源移出 Python：提示词 `resources/prompts/`、版式 `resources/templates/`、
  报错话术 `resources/messages/`、幻觉黑名单 `resources/asr/`、网页 `resources/web/`。
- 去掉 import 副作用：代理写入环境变量改由 `bootstrap()` 显式执行。
- 重复实现收敛：`ts()` / `_chapters_block` / `_to_report` 各只剩一份。

### 变更（配置，对外接口有新增）

- **`Settings` 换成 pydantic 模型**：字段带类型、范围与非空校验，`frozen=True` 不可变。
  数值越界直接报错（`ASR_MAX_SECONDS` 下限 60、`LLM_TEMPERATURE` 0–2、`LLM_CHUNK_CHARS` 下限 500），
  不再带着半截配置往下跑。
- **去掉模块级 `settings` 单例**：`load()` 在入口解析一次，`cfg` 逐层显式传参；
  `Store` 由 `cfg` 构造并持有它，目录解析不再依赖全局。同一进程可以跑不同参数的多个任务。
- **新增单次运行覆盖**：CLI `--set KEY=VALUE`、HTTP `POST /api/jobs` 的 `config` 字段。
  白名单制，`data_dir` / `proxy` / 外部程序路径不可覆盖。`--no-llm` 等价于 `--set llm.api_key=`。
- **新增配置快照**：每次运行写 `config.json`（密钥打码）并抄进 `manifest.json`；
  `cli <job> --config` 与 `GET /api/jobs/<id>/config` 可查，历史任务因此可复现。
- `llm_api_key` 改用 `SecretStr` 承载，序列化自动打码。
- ASR 引擎显式指定但未安装时直接报错，不再悄悄回退到别的引擎。
- 配置报错换成中文原文：`asr_max_seconds：不能小于 60`，不再把 pydantic 的英文报错直接推给用户。

### 修复

- **已安装形态下配置读不到**：路径锚点原本按 `parents[3]` 硬算，`pip install` 后指向
  `lib/python3.x`，`.env` 与 `data/` 都落到 Python 安装目录里。改为向上找 `pyproject.toml`
  判定源码树，找不到就用操作系统标准目录；`.env` 按「当前目录 → 仓库根 → 用户配置目录」
  顺序查找，并支持 `LINK_DIGEST_ENV` 指定。
- **相对路径随 cwd 漂移**：`LINK_DIGEST_DATA=./data` 现在锚在 `.env` 所在目录，
  从任何目录启动都指向同一份数据。
- **错误配置被静默改写**：`ASR_MAX_SECONDS=4h` 这类坏值改为启动时提示一行并回落默认值。
- **配置有两个来源**：`COOKIES_FILE` 原本绕过 `Settings` 直接读环境，现收进配置对象；
  `pipeline_version` 改为取 `__version__`，不再两处各写一份。
- **默认值不一致**：`ASR_MODEL` 的代码默认从 `large-v3` 对齐到 `.env.example` 的 `small`，
  避免首次安装静默下载 1.5GB 模型。
- **交接单的复跑命令写死 `.venv`**：改为当前解释器，并去掉对 `cd 仓库根` 的依赖。

## [0.1.0] - 2026-11-17

首个可用版本：端到端跑通，产物可核对。

### 新增

- **采集层**：yt-dlp（YouTube / B站 / Vimeo / X / TikTok / 快手 / 微博）、
  RSS 与「单集页面 → 反查原始 RSS」的播客路线、trafilatura 正文抽取、本地文件直入。
- **转写层**：ffmpeg 抽音轨 + faster-whisper / mlx-whisper 转写，确定性规则清洗
  （重复、幻觉、时间戳倒挂）；`ASR_ENGINE=auto` 按环境挑引擎。
- **契约**：`transcript.json` 作为唯一下游输入，含 schema 校验与 `bundle_id` 去重。
- **理解层**：OpenAI 兼容端点的 map-reduce 长文总结；无密钥时导出 `handoff/`
  交接包给任意 agent，回填 `answer.json` 后 `--apply` 渲染。
- **产物**：`report.md`（带 frontmatter）、`report.html`（单文件、样式内联、
  时间码可点回原片）、`report.json`。
- **网页**：FastAPI 单页界面，支持链接与文件上传、阶段进度、历史任务、交接包操作。
- **排错**：出口探测（直连 → 环境变量代理 → macOS 系统代理）与失败归因；
  `--check` 与 `/api/diagnostics` 逐条打印每条路径的结果。
- **登录态**：cookies.txt 合并写入脚本与联网校验（`LOGGED_IN` 真打一次站点），
  明确区分「没登录态」与「IP 被判成机器人」。
- **自检**：`scripts/selftest.py` 18 项断言，不联网、不调模型；`tests/fixtures/`
  存真实产物作为回归基准。

### 已知限制

- 单机单进程，无任务持久化。
- 抖音 / 小红书依赖 cookies，随时可能失效。
- 音乐类内容（DRM 加密）永久不支持。
