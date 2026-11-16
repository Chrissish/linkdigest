---
name: Bug 报告
about: 某个链接跑不动、报告渲染不对、网页出错
labels: bug
---

## 现象

<!-- 一句话说清发生了什么 -->

## 复现

- 链接或文件类型（**不要贴带 token / cookie 的私有链接**）：
- 使用的入口：命令行 / 网页 / 上传文件
- 平台：YouTube / B站 / 播客 / 公众号 / 本地文件 …

## 报错原文

<!-- 贴完整的那一段中文报错，别只贴「失败了」 -->

```
```

如果是采集阶段的问题，请附上自检输出：

```
python -m linkdigest.cli --check "<链接>"
```

## 环境

- 系统：macOS / Linux，什么版本
- Python 版本：
- `ASR_MODEL`：
- 是否配了 `LLM_API_KEY`（不用贴 key 本身）：
- 是否用了代理（`PROXY_URL`）：

## 已排除

<!-- 例如：确认网页是通过 http://127.0.0.1:8787 打开的、cookies 已按 docs/COOKIES.md 验证过 LOGGED_IN -->
