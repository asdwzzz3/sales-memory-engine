# Sales Memory Engine

销售记忆引擎 — 医疗器械销售智能记忆系统

## 简介

基于 SQLite + Faiss + ONNX 本地推理的销售知识管理系统，支持：
- 语义向量搜索（384维 all-MiniLM-L6-v2）
- 混合检索（语义 + 关键词）
- 拜访前30分钟战前简报自动生成
- 客户关系知识图谱
- CRM日报解析与结构化提取

## 技术栈

| 组件 | 说明 |
|------|------|
| SQLite | 主存储 + 向量表 |
| Faiss | 向量索引（内积） |
| ONNX Runtime | 本地嵌入模型推理 |
| Tokenizers | 文本编码 |

## 核心模块

| 文件 | 功能 |
|------|------|
| `src/memory_engine.py` | 核心存储与检索 API |
| `src/search.py` | 语义搜索 + 混合检索 |
| `src/database.py` | SQLite + 向量存储 |
| `src/extractor.py` | 实体提取 + 隐私过滤 |
| `src/vector_index.py` | Faiss 索引管理 |
| `src/visit_briefing.py` | 拜访简报生成 |
| `src/knowledge_graph.py` | 客户关系图谱 |
| `src/crm_daily_parser.py` | CRM 日报解析 |

## 验证状态

- ✅ ONNX 本地推理：384维向量，L2归一化
- ✅ Faiss ANN 搜索：余弦相似度计算
- ✅ 混合检索：语义+关键词加权
- ✅ 端到端：录入→搜索→命中完整链路

## 版本历史

- v3 (2026-05-13): ONNX 本地推理修复，绕过 fastembed 依赖
- v2 (2026-05-11): 初始向量搜索实现
- v1 (2026-05-10): MVP 完成

## 区域

江苏徐州 / 宿迁 / 淮安 — 流式细胞仪及试剂销售支持
