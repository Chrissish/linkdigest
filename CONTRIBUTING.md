# 参与贡献

欢迎 issue 和 PR。最有价值的贡献是**实测反馈**：哪个平台、什么链接、报了什么错、
`--check` 输出是什么。带这些信息的 issue 比「跑不起来」有用一百倍。

## 开发环境

```bash
./setup.sh                       # venv + 依赖
cp .env.example .env             # 可选：不填 LLM_API_KEY 就走降级模式
.venv/bin/python scripts/selftest.py
```

## 改动前先跑自检

`scripts/selftest.py` 是确定性层的回归网：不联网、不调模型、不下模型权重，
18 项断言覆盖清洗规则、时间码、契约校验、降级报告与失败归因。改清洗规则、改渲染、
改报错文案后必跑。

```bash
.venv/bin/python scripts/selftest.py        # 逻辑自检
.venv/bin/ruff check src scripts tests      # 风格与明显错误
```

需要对照「真实跑过的产物」时，用 `tests/fixtures/`：那里存着文本 / 音频 / 降级 / 失败
四类任务的 transcript、report、stages，只留产物不留音视频。说明见
[tests/fixtures/README.md](tests/fixtures/README.md)。

## 代码约定

- **错误信息面向用户，不面向栈。** 报错要说清「为什么失败」和「下一步做什么」，
  不要出现 `FileNotFoundError: [Errno 2]` 这种把底层异常直接甩出去的话。统一走
  `sources.explain_error()`，新平台新错法就往里加一条分叉。
- **重依赖延迟导入。** `yt_dlp`、`trafilatura`、`feedparser`、`faster_whisper` 都在函数内部导入，
  这样自检和网页能在没装这些包的环境里跑起来。
- **确定性归确定性。** 能用规则解决的（重复、幻觉、时间戳倒挂）不要交给模型；
  模型只负责归纳和判断。
- **产物只追加不改写。** 历史任务不动，接口按需补渲染（`report.html` 就是按需生成的）。
- 中文文案统一用简体，标点用全角；代码注释写「为什么」，不写「是什么」。

## 提交与 PR

- 提交信息用祈使句，一句话说清改了什么：`修 YouTube 登录态误判`、`补 cookies 合并写入`。
  正文留给「为什么」，需要时再写。
- 一个 PR 一件事。顺手重构请单独开 PR，别和功能混在一起。
- PR 描述里写清：改了什么、怎么验证的（贴 `selftest` 输出或截图）、有没有改变已有产物格式。
- 新增依赖请在 `pyproject.toml` 里说明理由；能进 `optional-dependencies` 的别放主依赖。
- **不要提交** `.env`、`data/`、`cookies.txt` 或任何登录凭证。`.gitignore` 已覆盖，
  但 PR 里贴日志时也留意别带上 cookie 值。

## 新增平台支持

1. 在 `sources.py` 的 `normalize_input()` 里加链接归一（去掉 `?si=` 这类追踪串，
   否则同一内容会算出不同 bundle_id 重复入库）。
2. 采集走已有路径就别新写：能 yt-dlp 的用 yt-dlp，能给 RSS 的走 RSS，最后才考虑正文抽取。
3. 给失败情况加一条 `explain_error()` 分叉，并在 `scripts/selftest.py` 里加断言。
4. 在 README 的支持范围表里标注稳定性——诚实点，「脆，随时坏」比虚假的「稳」有价值。
