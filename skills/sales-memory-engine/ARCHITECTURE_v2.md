# 销售记忆引擎 - 第二期架构设计

## 目标
将销售记忆引擎从「本地 SQLite + 关键词检索」升级为「语义向量检索 + AgentMemory 桥接 + 子 Agent 居中协调」的三层架构。

## 第一期现状
- 数据库：SQLite，5张表（observations, entities, relations, projects, summaries）
- 检索：关键词 LIKE 匹配 + 日期/标签过滤
- 向量：sentence-transformers 占位（安装 OOM，未启用）
- 集成：exec 调用 Python 脚本（extractor.py / memory_engine.py）

## 第二期核心能力

### 1. 语义向量检索（Semantic Search）
**问题**：当前关键词检索只能匹配字面，无法找"瑞斯凯尔竞争"和"竞品压力"的语义关联。

**方案**：
- 模型选型：sentence-transformers 太重（>2GB），换 **GTE-Small**（~60MB，性能足够）或 **m3e-small**
- 向量化时机：每条 observation 入库时实时 embed
- 检索方式：用户查询 → embed → cosine similarity top-k → 返回相关记忆
- 混合检索：语义 top-10 + 关键词补充，取并集

**技术路径**：
```python
# 轻量模型（约60MB）
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('thenlper/gte-small')  # 384维，商业友好许可证

# 纯 CPU，内存 <200MB
# 768维向量，SQLite 直接存 BLOB，无需 FAISS/Pinecone
```

### 2. AgentMemory 记忆架构桥接
**问题**：OpenClaw 主 Agent（我）有自己的 MEMORY.md + memory/ 日记，销售记忆引擎是独立 SQLite，两边数据孤岛。

**方案**：双向同步桥接

**A. 从销售记忆引擎 → AgentMemory**
- 每晚 cron（20:30）运行 sync 脚本：
  1. 读取当天新增的 observations（重要性 ≥3.0）
  2. 生成摘要，append 到 `memory/YYYY-MM-DD.md`
  3. 关键项目变更 → 更新 MEMORY.md「关键客户与项目」区块
- 格式：自然语言日记，不是结构化数据

**B. 从 AgentMemory → 销售记忆引擎**
- 我（主 Agent）在日常对话中提取的客户/竞品/项目信息
- 通过 `exec` 调用 `memory_engine.py --ingest` 写入 SQLite
- 实现：我在回复用户时，如果涉及新信息，后台自动调用 ingest

**C. 统一查询接口**
- 用户问"盱眙县中医院最近怎么样"
- 我优先查销售记忆引擎（结构化、带时间戳、带竞品信息）
- 再查 AgentMemory（我的日记、用户说过的话）
- 合并输出：结构化事实 + 上下文解读

### 3. 子 Agent 居中协调（Coordinator Pattern）
**问题**：当前 cron 任务如果子 Agent 失败（Message failed / timeout），没有 fallback，连续 error 累积。

**方案**：三层协调架构

```
┌─────────────────────────────────────────┐
│  Coordinator Agent（居中协调器）           │
│  - 接收定时任务触发                        │
│  - 评估任务复杂度                          │
│  - 分发到 Worker Agent                    │
└─────────────────────────────────────────┘
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
   ┌────────┐ ┌────────┐ ┌────────┐
   │Worker 1│ │Worker 2│ │Worker 3│
   │采集    │ │简报    │ │推送    │
   └────────┘ └────────┘ └────────┘
        │           │           │
        └───────────┴───────────┘
                    │
                    ▼
         ┌─────────────────┐
         │ Fallback Logic   │
         │ - Worker 失败 →  │
         │   降级/重试/告警 │
         └─────────────────┘
```

**具体实现**：
- 每个复杂 cron 任务（morning-task-brief、evening-visit-reminder）拆成 2-3 个子 Agent
- Worker 1（采集）：只读取文件，生成结构化 JSON 输出到 /tmp/
- Worker 2（简报）：读取 JSON，生成人话简报
- Worker 3（推送）：只负责调用 message 工具发微信
- Coordinator 监控每个 Worker 的 result，失败时：
  - 采集失败 → 用「昨日数据」降级推送
  - 简报失败 → 直接推送原始 JSON（降级）
  - 推送失败 → 标记「未送达」，我（主 Agent）下次对话时提醒用户

### 4. 晨报可展开设计
**问题**：用户要求晨报简化，但对感兴趣的信息需要详细内容和分析。

**方案**：晨报 = 压缩摘要 + 可展开标记

**消息格式**：
```
📋 晨报 | 5/11 08:10
━━━━━━━━━━━━━━
1️⃣ 盱眙县中医院 [细胞因子] 计划审批等待中
   → 距上次跟进 14 天 ⚠️
2️⃣ 淮安市一院 瑞斯凯尔已装机 [obs-2]
3️⃣ 徐州市儿童医院 需求单待提交 [obs-5]

💡 提示：回复 obs-2 查看详情和分析
━━━━━━━━━━━━━━
```

**展开机制**：
- 用户在主对话中回复 "obs-2" 或 "2"
- 我调用销售记忆引擎查询 obs_id=2
- 输出完整内容：时间线、竞品信息、建议动作、相关联系记录

## 实施优先级

| 优先级 | 模块 | 预计工时 | 阻塞项 |
|--------|------|---------|--------|
| P0 | 语义向量（gte-small） | 2h | 模型下载/安装 |
| P0 | Cron timeout 延长 | 10min | ✅ 已完成 |
| P1 | AgentMemory 桥接（A→B） | 3h | 无 |
| P1 | 晨报可展开设计 | 2h | 向量检索依赖 |
| P2 | 子 Agent 协调器 | 4h | 需要多 session 测试 |
| P2 | AgentMemory 桥接（B→A） | 2h | 无 |
| P3 | 混合检索优化 | 2h | 向量检索就绪后 |

## 立即执行动作
1. ✅ Cron timeout 已拉至 1800s（30分钟）
2. 🔧 安装 gte-small 模型（换国内镜像源）
3. 🔧 写 AgentMemory 桥接脚本（sync_to_diary.py）
4. 🔧 修改晨报 prompt，加入 obs_id 标记

## 待确认
- 向量模型偏好：gte-small（384维，60MB）vs m3e-small（512维，120MB）？
- 桥接频率：每天一次批量同步，还是每次对话实时同步？
- 协调器复杂度：先做单 Worker + 降级逻辑，还是直接上多 Worker？
