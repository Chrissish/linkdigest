内容元信息：
标题：{title}
平台：{platform}
作者：{author}
时长/篇幅：{duration}
原语言：{lang}
字数：{chars}

分段摘要（按时间顺序）：
{digests}

机械章节切分（可调整合并，但时间范围要落在相邻章节内）：
{chapters}

输出 JSON：
{{
  "intent": "可选。读者是谁、要回答什么问题，一句话",
  "one_liner": "一句话说清这是什么、讲了什么",
  "tags": ["3-5 个内容标签词，名词或短语，供知识库归类"],
  "tldr": ["核心结论，条数与每条的详略都由内容决定"],
  "body": "可选。3-6 段正文，平直整理源内容：讲了什么、按什么顺序、给了哪些具体做法和判断。不做评价、不夹批注。不超过 800 字",
  "sections": [{{"title": "小标题", "start": 0, "end": 300, "points": ["要点1", "要点2"]}}],
  "key_points": ["最重要的信息，条数随内容"],
  "data_points": [{{"label": "指标", "value": "数值", "start": 0}}],
  "quotes": [{{"text": "原文", "start": 0, "note": "为什么值得记"}}],
  "controversies": ["有争议/未证实的说法"],
  "open_questions": ["材料没回答但重要的问题"],
  "terms": {{"术语": "解释"}},
  "appendix": "可选。通用字段装不下的结构（时间线/表格/代码），Markdown，自带 ### 标题"
}}
