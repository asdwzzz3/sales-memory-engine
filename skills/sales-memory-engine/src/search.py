#!/usr/bin/env python3
"""
销售记忆引擎 - 搜索模块 (v2)
语义向量搜索 + 关键词搜索 + 混合检索
基于 KIMI Embedding API，无本地模型依赖
"""

import os
import sys
import json
import sqlite3
import urllib.request
import urllib.error
import numpy as np
from typing import List, Dict, Any, Optional

# 确保能 import database
sys.path.insert(0, os.path.dirname(__file__))
from database import (
    get_conn, init_db,
    save_observation_vector, get_all_vectors, get_vector_by_obs_id,
    cosine_similarity_py,
)

# ========== fastembed 本地向量嵌入 ==========
_fastembed_model = None

def get_fastembed_embedding(text: str) -> Optional[List[float]]:
    """
    使用本地 ONNX 模型 (all-MiniLM-L6-v2) 获取文本向量
    绕过 fastembed 库（缓存结构不匹配问题），直接使用 onnxruntime + tokenizers
    模型路径: ~/.cache/fastembed/models/all-MiniLM-L6-v2/
    维度: 384, 已 L2 归一化
    失败返回 None（调用方应 fallback 到关键词搜索）
    """
    global _fastembed_model
    try:
        if _fastembed_model is None:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            
            cache_dir = os.path.expanduser("~/.cache/fastembed/models/all-MiniLM-L6-v2")
            model_path = os.path.join(cache_dir, "onnx/model.onnx")
            tokenizer_path = os.path.join(cache_dir, "tokenizer.json")
            
            if not os.path.exists(model_path):
                print(f"[EMBED] ONNX 模型未找到: {model_path}")
                return None
            if not os.path.exists(tokenizer_path):
                print(f"[EMBED] Tokenizer 未找到: {tokenizer_path}")
                return None
            
            session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
            tokenizer = Tokenizer.from_file(tokenizer_path)
            tokenizer.enable_truncation(max_length=128)
            tokenizer.enable_padding(length=128)
            
            _fastembed_model = (session, tokenizer)
            print(f"[EMBED] 本地 ONNX 模型已加载: all-MiniLM-L6-v2 (384维)")
        
        session, tokenizer = _fastembed_model
        encoded = tokenizer.encode(text)
        
        input_ids = np.array([encoded.ids], dtype=np.int64)
        attention_mask = np.array([encoded.attention_mask], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids)
        
        outputs = session.run(None, {
            'input_ids': input_ids,
            'attention_mask': attention_mask,
            'token_type_ids': token_type_ids,
        })
        
        # Mean pooling + L2 归一化
        last_hidden = outputs[0]  # [batch, seq_len, hidden_dim]
        mask = attention_mask.astype(np.float32)
        mask_expanded = np.expand_dims(mask, axis=-1)
        sum_embeddings = np.sum(last_hidden * mask_expanded, axis=1)
        sum_mask = np.clip(np.sum(mask, axis=1, keepdims=True), a_min=1e-9, a_max=None)
        vec = (sum_embeddings / sum_mask)[0]
        vec = vec / np.linalg.norm(vec)
        
        return vec.tolist()
    except Exception as e:
        print(f"[EMBED] 本地 ONNX 推理失败: {e}")
        return None


# ========== KIMI Embedding API（保留向后兼容）==========
KIMI_EMBED_URL = "https://api.moonshot.cn/v1/embeddings"
KIMI_EMBED_MODEL = "moonshot-v1-embedding"
EMBED_TIMEOUT = 15  # 秒


def _get_api_key() -> Optional[str]:
    """获取 KIMI API Key（按优先级）"""
    for env_var in ("KIMI_PLUGIN_API_KEY", "KIMI_API_KEY"):
        key = os.environ.get(env_var)
        if key:
            return key
    key_file = os.path.expanduser("~/.config/kimi/api_key")
    if os.path.exists(key_file):
        with open(key_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None


def get_kimi_embedding(text: str) -> Optional[List[float]]:
    """
    调用 KIMI Embedding API 获取文本向量
    失败返回 None（调用方应 fallback 到关键词搜索）
    """
    api_key = _get_api_key()
    if not api_key:
        print("[EMBED] 未找到 KIMI API Key")
        return None

    payload = json.dumps({
        "model": KIMI_EMBED_MODEL,
        "input": text,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        KIMI_EMBED_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=EMBED_TIMEOUT)
        data = json.loads(resp.read().decode("utf-8"))
        # OpenAI-compatible 格式: data[0].embedding
        embeddings = data.get("data", [])
        if not embeddings:
            print(f"[EMBED] API 返回空 embeddings: {data}")
            return None
        return embeddings[0].get("embedding")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")[:200]
        print(f"[EMBED] HTTP {e.code}: {err_body}")
        return None
    except Exception as e:
        print(f"[EMBED] 请求异常: {e}")
        return None


# ========== 纯关键词搜索 ==========

def keyword_search(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    基于 LIKE + FTS5 的关键词搜索
    """
    conn = get_conn()
    results = []
    like_query = f"%{query}%"

    try:
        # 1. 先用 LIKE 做宽泛匹配（按重要性排序）
        like_rows = conn.execute(
            """SELECT id, raw_content, extracted_json, timestamp, importance_score, tags
               FROM observations
               WHERE raw_content LIKE ? OR tags LIKE ?
               ORDER BY importance_score DESC, timestamp DESC
               LIMIT ?""",
            (like_query, like_query, limit)
        ).fetchall()
        for row in like_rows:
            results.append({
                "id": row["id"],
                "content": row["raw_content"],
                "extracted": json.loads(row["extracted_json"]) if row["extracted_json"] else {},
                "timestamp": row["timestamp"],
                "importance": row["importance_score"],
                "tags": row["tags"],
                "score": 0.5,
                "source": "keyword",
            })
    except Exception as e:
        print(f"[SEARCH] 关键词 LIKE 检索失败: {e}")

    # 2. 再用 FTS5 做精确匹配（补充结果）
    try:
        fts_rows = conn.execute(
            """SELECT rowid as id, raw_content, tags FROM observations_fts
               WHERE observations_fts MATCH ?""",
            (query,)
        ).fetchall()
        existing_ids = {r["id"] for r in results}
        for row in fts_rows:
            if row["id"] not in existing_ids:
                # 补全 observation 其他字段
                obs = conn.execute(
                    """SELECT id, raw_content, extracted_json, timestamp, importance_score, tags
                       FROM observations WHERE id = ?""",
                    (row["id"],)
                ).fetchone()
                if obs:
                    results.append({
                        "id": obs["id"],
                        "content": obs["raw_content"],
                        "extracted": json.loads(obs["extracted_json"]) if obs["extracted_json"] else {},
                        "timestamp": obs["timestamp"],
                        "importance": obs["importance_score"],
                        "tags": obs["tags"],
                        "score": 0.7,  # FTS5 匹配质量通常比 LIKE 高
                        "source": "fts5",
                    })
    except Exception as e:
        print(f"[SEARCH] FTS5 检索失败: {e}")

    conn.close()
    # 按分数降序，截断
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


# ========== 语义向量搜索 ==========

def semantic_search(query: str, top_k: int = 5, min_score: float = 0.5) -> List[Dict[str, Any]]:
    """
    语义向量搜索：
    1. query → fastembed 本地模型 → 向量
    2. 优先使用 faiss 索引做 ANN 搜索（O(log n)）
    3. faiss 不可用时 fallback 到 SQLite 暴力扫描
    4. 返回 top_k 结果

    如果 Embedding 不可用，自动降级为 keyword_search
    """
    # Step 1: 获取 query 向量
    query_vec = get_fastembed_embedding(query)
    if query_vec is None:
        print("[SEMANTIC] fastembed 不可用，降级为关键词搜索")
        return keyword_search(query, limit=top_k)

    # Step 2: 尝试 faiss 索引搜索（高效）
    try:
        from vector_index import search_index, ensure_index_exists
        ensure_index_exists()
        faiss_results = search_index(query_vec, top_k=top_k, min_score=min_score)
        if faiss_results:
            conn = get_conn()
            results = []
            for obs_id, score in faiss_results:
                row = conn.execute(
                    """SELECT id, raw_content, extracted_json, timestamp, importance_score, tags
                       FROM observations WHERE id = ?""",
                    (obs_id,)
                ).fetchone()
                if row:
                    results.append({
                        "id": row["id"],
                        "content": row["raw_content"],
                        "extracted": json.loads(row["extracted_json"]) if row["extracted_json"] else {},
                        "timestamp": row["timestamp"],
                        "importance": row["importance_score"],
                        "tags": row["tags"],
                        "score": score,
                        "source": "semantic",
                        "model_version": "fastembed-all-MiniLM-L6-v2",
                    })
            conn.close()
            if results:
                print(f"[SEMANTIC] faiss 命中 {len(results)} 条")
                return results
    except Exception as e:
        print(f"[SEMANTIC] faiss 搜索失败，fallback 到暴力扫描: {e}")

    # Step 3: fallback 到 SQLite 暴力扫描
    conn = get_conn()
    all_vec_records = get_all_vectors(conn)
    if not all_vec_records:
        conn.close()
        print("[SEMANTIC] 向量库为空，降级为关键词搜索")
        return keyword_search(query, limit=top_k)

    scored = []
    for rec in all_vec_records:
        try:
            sim = cosine_similarity_py(query_vec, rec["vector"])
        except ValueError as e:
            print(f"[SEMANTIC] 维度跳过 obs_id={rec['obs_id']}: {e}")
            continue
        if sim >= min_score:
            scored.append((rec["obs_id"], sim, rec["model_version"]))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_ids = [x[0] for x in scored[:top_k]]

    if not top_ids:
        conn.close()
        print("[SEMANTIC] 无相似度达标结果，降级为关键词搜索")
        return keyword_search(query, limit=top_k)

    results = []
    for obs_id, sim, model_ver in scored[:top_k]:
        row = conn.execute(
            """SELECT id, raw_content, extracted_json, timestamp, importance_score, tags
               FROM observations WHERE id = ?""",
            (obs_id,)
        ).fetchone()
        if row:
            results.append({
                "id": row["id"],
                "content": row["raw_content"],
                "extracted": json.loads(row["extracted_json"]) if row["extracted_json"] else {},
                "timestamp": row["timestamp"],
                "importance": row["importance_score"],
                "tags": row["tags"],
                "score": sim,
                "source": "semantic",
                "model_version": model_ver,
            })

    conn.close()
    return results


# ========== 混合检索 ==========

def hybrid_search_v2(
    query: str,
    top_k: int = 5,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3,
    min_score: float = 0.3,
    customer: Optional[str] = None,
    project: Optional[str] = None,
    competitor: Optional[str] = None,
    date_after: Optional[str] = None,
    min_importance: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    混合检索：语义向量 + 关键词，加权合并

    新增结构化过滤参数:
        customer: 客户/医院名称（模糊匹配 extracted_json.customers.name）
        project: 项目名称（模糊匹配 extracted_json.projects.name）
        competitor: 竞品名称（模糊匹配 extracted_json.competitors）
        date_after: 时间过滤（格式 YYYY-MM-DD，匹配 timestamp >= date_after）
        min_importance: 最低重要性分数
    """
    # 语义结果
    semantic_results = semantic_search(query, top_k=top_k * 3, min_score=min_score)
    # 关键词结果
    kw_results = keyword_search(query, limit=top_k * 3)

    # 合并打分
    merged: Dict[int, Dict[str, Any]] = {}

    for item in semantic_results:
        oid = item["id"]
        merged[oid] = {
            **item,
            "hybrid_score": item.get("score", 0) * vector_weight,
        }

    for item in kw_results:
        oid = item["id"]
        if oid in merged:
            merged[oid]["hybrid_score"] += item.get("score", 0) * keyword_weight
            merged[oid]["source"] = "hybrid"
        else:
            merged[oid] = {
                **item,
                "hybrid_score": item.get("score", 0) * keyword_weight,
            }

    # ─── 结构化过滤 ───
    results = list(merged.values())
    
    if customer:
        customer_lower = customer.lower()
        filtered = []
        for r in results:
            extracted = r.get("extracted", {})
            customers = extracted.get("customers", [])
            if any(customer_lower in c.get("name", "").lower() for c in customers):
                filtered.append(r)
            elif customer_lower in r.get("content", "").lower():
                # fallback: 内容中直接出现医院名
                filtered.append(r)
        results = filtered
    
    if project:
        project_lower = project.lower()
        filtered = []
        for r in results:
            extracted = r.get("extracted", {})
            projects = extracted.get("projects", [])
            if any(project_lower in p.get("name", "").lower() for p in projects):
                filtered.append(r)
            elif project_lower in r.get("content", "").lower():
                filtered.append(r)
        results = filtered
    
    if competitor:
        comp_lower = competitor.lower()
        filtered = []
        for r in results:
            extracted = r.get("extracted", {})
            competitors = extracted.get("competitors", [])
            # competitors 可能是字符串列表或字典列表
            comp_names = []
            for c in competitors:
                if isinstance(c, dict):
                    comp_names.append(c.get("name", "").lower())
                else:
                    comp_names.append(str(c).lower())
            if any(comp_lower in cn for cn in comp_names):
                filtered.append(r)
            elif comp_lower in r.get("content", "").lower():
                filtered.append(r)
        results = filtered
    
    if date_after:
        filtered = []
        for r in results:
            ts = r.get("timestamp", "")
            if ts and ts >= date_after:
                filtered.append(r)
        results = filtered
    
    if min_importance is not None:
        filtered = []
        for r in results:
            imp = r.get("importance", 0)
            if imp is None:
                imp = 0
            if imp >= min_importance:
                filtered.append(r)
        results = filtered

    # 按 hybrid_score 降序
    results.sort(key=lambda x: x.get("hybrid_score", x.get("score", 0)), reverse=True)
    return results[:top_k]


# ========== CLI 测试 ==========
if __name__ == "__main__":
    init_db()
    # 简单测试：先检查 fastembed 可用性
    test_vec = get_fastembed_embedding("测试文本")
    if test_vec:
        print(f"✅ fastembed 可用，维度: {len(test_vec)}")
    else:
        print("⚠️ fastembed 不可用（请检查 ONNX Runtime）")
    # 同时显示 KIMI API 状态
    kimi_vec = get_kimi_embedding("测试文本")
    if kimi_vec:
        print(f"✅ KIMI Embedding API 可用，维度: {len(kimi_vec)}")
    else:
        print("⚠️ KIMI Embedding API 不可用（请检查 API Key）")
