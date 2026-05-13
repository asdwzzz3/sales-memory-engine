#!/usr/bin/env python3
"""
销售记忆引擎 - 数据库管理模块
SQLite + FTS5 + 向量表结构
"""

import sqlite3
import json
import os
import struct
import numpy as np
from datetime import datetime
from typing import Optional, List, Dict, Any

DB_PATH = os.path.expanduser("~/.openclaw/workspace/memory_engine/db/sales_memory.db")

EMBEDDING_DIM = 384  # gte-small 维度


def pack_embedding(emb: np.ndarray) -> bytes:
    """将 numpy 向量打包为 SQLite BLOB"""
    return struct.pack(f'{len(emb)}f', *emb.astype(np.float32))


def unpack_embedding(blob: bytes, dim: int = EMBEDDING_DIM) -> np.ndarray:
    """从 SQLite BLOB 解包为 numpy 向量"""
    return np.array(struct.unpack(f'{dim}f', blob), dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的余弦相似度"""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    """初始化所有表结构"""
    conn = get_conn()
    
    # 1. 原始观察记录表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp TEXT DEFAULT (datetime('now')),
            source TEXT,           -- 'user', 'assistant', 'tool', 'external'
            raw_content TEXT,      -- 原始对话文本（隐私过滤后）
            extracted_json TEXT,   -- 提取的实体JSON
            importance_score REAL DEFAULT 1.0,  -- 1-5，越高越重要
            tags TEXT              -- 逗号分隔标签
        )
    """)
    
    # 2. 客户画像表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT UNIQUE NOT NULL,  -- slug格式：xuyi_tcm
            name TEXT NOT NULL,                 -- 全称
            region TEXT,                        -- 徐州/宿迁/淮安
            level TEXT,                         -- 三甲/二甲/社区
            department TEXT,                    -- 检验科/血液科
            status TEXT DEFAULT 'active',       -- active/closed/paused
            profile_json TEXT,                  -- 完整画像JSON
            last_updated TEXT DEFAULT (datetime('now')),
            visit_count INTEGER DEFAULT 0,
            last_visit_date TEXT
        )
    """)
    
    # 3. 联系人表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            name TEXT NOT NULL,
            title TEXT,            -- 主任/副主任/技师
            role TEXT,             -- decision_maker / influencer / user
            interests TEXT,        -- JSON数组
            quotes TEXT,           -- 说过的话（JSON数组）
            last_interaction TEXT,
            FOREIGN KEY (customer_id) REFERENCES customer_profiles(customer_id)
        )
    """)
    
    # 4. 项目/商机表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            project_name TEXT NOT NULL,     -- 细胞因子/淋巴细胞亚群
            spec TEXT,                      -- 6项/12项/AD三项
            stage TEXT DEFAULT 'prospect',  -- prospect/demo/trial/quote/negotiation/won/lost
            budget TEXT,
            decision_chain TEXT,            -- JSON数组
            blocker TEXT,
            competitor TEXT,                -- 主要竞品
            start_date TEXT,
            expected_close TEXT,
            FOREIGN KEY (customer_id) REFERENCES customer_profiles(customer_id)
        )
    """)
    
    # 5. 竞品事件时间线
    conn.execute("""
        CREATE TABLE IF NOT EXISTS competitor_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_name TEXT NOT NULL,
            event_type TEXT,       -- win/launch/price_change/visit/rumor
            hospital TEXT,
            region TEXT,
            description TEXT,
            event_date TEXT,
            source TEXT,           -- 用户提及/招标网/乙方宝
            impact_score INTEGER DEFAULT 3  -- 1-5影响度
        )
    """)
    
    # 6. 政策/收费代码表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,             -- 250401014-a
            name TEXT,             -- 白介素测定
            price TEXT,
            category TEXT,         -- 医保甲类/乙类/丙类
            region TEXT,           -- 江苏省
            effective_date TEXT,
            related_hospitals TEXT,  -- JSON数组
            related_competitors TEXT, -- JSON数组
            promotion_script TEXT     -- 推广话术
        )
    """)
    
    # 7. FTS5 全文检索虚拟表（独立存储，不依赖外部表）
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
            raw_content,
            tags,
            tokenize='unicode61 remove_diacritics 0'
        )
    """)
    
    # 8. 向量存储表（旧版BLOB格式，保留向后兼容）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vector_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER,
            embedding BLOB,
            text_hash TEXT,
            timestamp TEXT DEFAULT (datetime('now'))
        )
    """)
    
    # 9. 新版向量存储表（JSON格式，适配远程API）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations_vectors (
            obs_id INTEGER PRIMARY KEY,
            vector TEXT NOT NULL,          -- JSON数组
            model_version TEXT DEFAULT '',  -- e.g. moonshot-v1-embedding
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (obs_id) REFERENCES observations(id) ON DELETE CASCADE
        )
    """)
    
    # 触发器：observations 插入后同步到 FTS5
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN
            INSERT INTO observations_fts(rowid, raw_content, tags)
            VALUES (new.id, new.raw_content, new.tags);
        END
    """)
    
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN
            INSERT INTO observations_fts(observations_fts, rowid, raw_content, tags)
            VALUES ('delete', old.id, old.raw_content, old.tags);
        END
    """)
    
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS observations_fts_update AFTER UPDATE ON observations BEGIN
            INSERT INTO observations_fts(observations_fts, rowid, raw_content, tags)
            VALUES ('delete', old.id, old.raw_content, old.tags);
            INSERT INTO observations_fts(rowid, raw_content, tags)
            VALUES (new.id, new.raw_content, new.tags);
        END
    """)
    
    conn.commit()
    conn.close()
    print("[DB] 数据库初始化完成:", DB_PATH)


# ==================== 新版向量存储与检索（JSON格式，适配远程API）====================

def save_observation_vector(conn: sqlite3.Connection, obs_id: int, vector: List[float], model_version: str = ""):
    """保存向量到 observations_vectors 表（JSON格式），并自动更新 faiss 索引"""
    conn.execute(
        """INSERT OR REPLACE INTO observations_vectors (obs_id, vector, model_version)
           VALUES (?, ?, ?)""",
        (obs_id, json.dumps(vector), model_version)
    )
    conn.commit()
    # 同步更新 faiss 索引（增量添加，无需重建）
    try:
        from vector_index import add_vector
        add_vector(obs_id, vector)
    except Exception as e:
        print(f"[DB] faiss 索引更新失败（非阻塞）: {e}")


def get_all_vectors(conn: sqlite3.Connection, model_version: Optional[str] = None) -> List[Dict[str, Any]]:
    """获取所有向量记录，可选按模型版本过滤"""
    if model_version:
        rows = conn.execute(
            "SELECT obs_id, vector, model_version FROM observations_vectors WHERE model_version = ?",
            (model_version,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT obs_id, vector, model_version FROM observations_vectors"
        ).fetchall()
    return [
        {
            "obs_id": row["obs_id"],
            "vector": json.loads(row["vector"]),
            "model_version": row["model_version"],
        }
        for row in rows
    ]


def get_vector_by_obs_id(conn: sqlite3.Connection, obs_id: int) -> Optional[List[float]]:
    """获取指定obs_id的向量"""
    row = conn.execute(
        "SELECT vector FROM observations_vectors WHERE obs_id = ?",
        (obs_id,)
    ).fetchone()
    if row:
        return json.loads(row["vector"])
    return None


def delete_observation_vector(conn: sqlite3.Connection, obs_id: int):
    """删除指定obs_id的向量"""
    conn.execute("DELETE FROM observations_vectors WHERE obs_id = ?", (obs_id,))


def cosine_similarity_py(a: List[float], b: List[float]) -> float:
    """纯Python余弦相似度计算（不依赖numpy）"""
    if len(a) != len(b):
        raise ValueError(f"向量维度不匹配: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b + 1e-10)


# ==================== 向量存储与检索（旧版BLOB，保留向后兼容）====================

def save_embedding(conn: sqlite3.Connection, observation_id: int, embedding: np.ndarray, text_hash: str = ""):
    """保存向量到 vector_memory 表（复用已有连接）"""
    conn.execute(
        "INSERT INTO vector_memory (observation_id, embedding, text_hash) VALUES (?, ?, ?)",
        (observation_id, pack_embedding(embedding), text_hash)
    )


def search_by_vector(query_emb: np.ndarray, top_k: int = 5, min_score: float = 0.5) -> List[Dict[str, Any]]:
    """向量相似度检索（全表扫描，适合小数据集 <10万条）"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT vm.id, vm.observation_id, vm.embedding, o.raw_content, o.extracted_json, o.tags "
        "FROM vector_memory vm JOIN observations o ON vm.observation_id = o.id"
    ).fetchall()
    conn.close()
    
    results = []
    for row in rows:
        if row["embedding"] is None:
            continue
        emb = unpack_embedding(row["embedding"])
        score = cosine_similarity(query_emb, emb)
        if score >= min_score:
            results.append({
                "score": score,
                "observation_id": row["observation_id"],
                "raw_content": row["raw_content"],
                "extracted_json": row["extracted_json"],
                "tags": row["tags"],
            })
    
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def hybrid_search(
    query_emb: np.ndarray,
    keyword: str = "",
    top_k: int = 5,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3
) -> List[Dict[str, Any]]:
    """混合检索：向量 + 关键词 FTS5"""
    conn = get_conn()
    
    # 关键词检索（FTS5）
    keyword_results = []
    if keyword:
        fts_rows = conn.execute(
            "SELECT rowid as observation_id, raw_content, tags FROM observations_fts "
            "WHERE observations_fts MATCH ?",
            (keyword,)
        ).fetchall()
        keyword_results = {
            row["observation_id"]: {
                "observation_id": row["observation_id"],
                "raw_content": row["raw_content"],
                "tags": row["tags"],
                "kw_score": 1.0,
            }
            for row in fts_rows
        }
    
    # 向量检索
    vector_results = search_by_vector(query_emb, top_k=top_k * 2, min_score=0.3)
    
    # 合并打分
    merged = {}
    for item in vector_results:
        oid = item["observation_id"]
        merged[oid] = {
            **item,
            "hybrid_score": item["score"] * vector_weight + (keyword_results.get(oid, {}).get("kw_score", 0) * keyword_weight)
        }
    
    for oid, item in keyword_results.items():
        if oid not in merged:
            # 只命中关键词的，给基础分
            merged[oid] = {
                **item,
                "score": 0.0,
                "hybrid_score": item["kw_score"] * keyword_weight
            }
    
    conn.close()
    
    results = sorted(merged.values(), key=lambda x: x["hybrid_score"], reverse=True)
    return results[:top_k]


if __name__ == "__main__":
    init_db()
