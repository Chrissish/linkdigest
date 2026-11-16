## 改了什么

<!-- 一句话。正文留给「为什么这么改」 -->

## 为什么

## 怎么验证的

- [ ] `.venv/bin/python scripts/selftest.py` 全绿
- [ ] `.venv/bin/ruff check src scripts tests` 无告警
- [ ] 手动跑过一条真实链接（平台：______）

## 影响面

- [ ] 改变了产物格式（`transcript.json` / `report.*` 的字段或结构）→ 说明是否需要 `scripts/rerender.py`
- [ ] 新增依赖 → 已在 `pyproject.toml` 说明理由
- [ ] 涉及 cookies / 密钥处理（确认没有把凭证写进代码或日志）
