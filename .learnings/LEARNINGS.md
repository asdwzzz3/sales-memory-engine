# 学习记录

本文件用于记录从用户纠正、知识差距和最佳实践中获得的学习。

格式基于 self-improving-agent 技能规范。

---

## [LRN-20260513-001] correction — 产品品牌定位

**Logged**: 2026-05-13T22:30:00+08:00
**Priority**: high
**Status**: resolved
**Area**: docs

### Summary
用户纠正三个关键事实错误

### Details
1. 层浪合作型号是 **LongCyte**，不是"Le"（之前简写错误）
2. 赛基主打高端线，德普主打基础线，对外销售时分别使用各自品牌名
3. 注册证需去官网点"详情页"下载，不是我之前瞎编的路径

### Suggested Action
- 所有文档中出现"层浪Le"的地方一律改为"层浪LongCyte"
- 品牌矩阵写入 MEMORY.md 和 USER.md 长期记忆
- 不确定的文件路径先问用户或查官网，不编造

### Metadata
- Source: user_feedback
- Related Files: MEMORY.md, USER.md, TOOLS.md
- Tags: brand, product, correction

---

## [LRN-20260513-002] best_practice — 外部API状态判断

**Logged**: 2026-05-13T10:15:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
"模型列表能拉"不等于"服务可用"——OpenRouter 列表API通但chat completions 500

### Details
验证API key时仅测试了 `/models` 端点，未测试实际调用端点（`/chat/completions`）。向用户汇报"key有效"后下一秒全模型500，非常尴尬。

### Suggested Action
- 以后验证API key必须做端到端测试（列表+实际调用）
- 第三方服务状态以 status page / 实际调用为准，不以单一端点推断

### Metadata
- Source: error
- Related Files: TOOLS.md
- Tags: api, validation, openrouter

---

## [LRN-20260513-003] best_practice — 标讯匹配排除规则

**Logged**: 2026-05-13T16:00:00+08:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
"莘塔幼儿园采购雪糕棒"被判定为4级紧急信号——排除逻辑有漏洞

### Details
医院匹配返回 None，但产品词（"抗体""蛋白质 Marker"）把 urgency 硬拉上去了。根因：排除逻辑只看了医院维度，没在产品维度做二次过滤。另外"人民"作为别名太短，飘进了"头桥社区卫生服务中心"→误匹配"江苏省人民医院"。

### Suggested Action
- urgency 评分必须双维度同时满足：医院匹配命中 AND 产品词命中
- 医院别名长度<3字的词条从匹配库移除，避免短词漂移
- 增加幼儿园/学校/雪糕棒等明显非医疗词的黑名单

### Metadata
- Source: error
- Related Files: tender_intel/tender_crm_pipeline.py, tender_intel/tender_crm_bridge.py
- Tags: tender, matching, false_positive

---

## [LRN-20260513-004] knowledge_gap — 微信登录方式记忆

**Logged**: 2026-05-13T21:00:00+08:00
**Priority**: low
**Status**: resolved
**Area**: config

### Summary
被问到"cron微信推送报错的问题之前搞过，你还有印象吗"时大脑空白

### Details
实际之前确实是扫码登录的（kimi提供的二维码），但用户问的时候我没提前翻文件，只能含糊说"就……正常认识的啊"。细节全在日志里，但我没养成会话前检索的习惯。

### Suggested Action
- 会话启动时除了读 SOUL.md / USER.md / memory，还要主动扫一遍 .learnings/ 和近期 memory/
- 被问到历史操作时，先查文件再回答，不凭模糊记忆

### Metadata
- Source: user_feedback
- Related Files: AGENTS.md
- Tags: memory, session_startup, weixin

---