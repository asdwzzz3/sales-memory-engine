#!/usr/bin/env python3
"""
销售记忆引擎 - 存储与检索模块 (v3)
KIMI Embedding API + 语义搜索 + 混合检索 + AgentMemory 桥接
"""

import sqlite3
import json
import os
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from database import (
    get_conn, init_db,
    save_embedding, save_observation_vector,
    cosine_similarity,
)
from extractor import extract_entities, privacy_filter

# ========== 向量嵌入 (通过 fastembed 本地模型，替代 KIMI API) ==========
from search import get_fastembed_embedding as get_kimi_embedding, semantic_search as _semantic_search_from_module, keyword_search

# 保留旧版 hybrid_search 用于兼容（如果有 vector_memory 中的旧向量）
from database import hybrid_search


# ========== 存储 API ==========

def save_observation(raw_text: str, session_id: str = "", source: str = "user") -> int:
    """
    保存一次观察记录，自动提取实体、嵌入向量、索引
    Returns: observation_id
    """
    safe_text = privacy_filter(raw_text)
    extracted = extract_entities(safe_text, session_id)
    importance = _calc_importance(extracted)

    # 向量嵌入（通过 KIMI Embedding API → JSON 向量表）
    # 注意：先在外部获取 embedding，避免在数据库事务中做网络 I/O
    embedding = get_kimi_embedding(safe_text)
    text_hash = hashlib.sha256(safe_text.encode()).hexdigest()[:16]

    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO observations (session_id, source, raw_content, extracted_json, importance_score, tags)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, source, safe_text, json.dumps(extracted, ensure_ascii=False), importance, extracted["tags"])
    )
    obs_id = cur.lastrowid

    if embedding is not None:
        # 新版 JSON 向量表
        save_observation_vector(conn, obs_id, embedding, model_version="fastembed-all-MiniLM-L6-v2")
        print(f"[EMBED] obs_id={obs_id} 向量已保存 (dim={len(embedding)})")

    _update_customer_profiles(conn, extracted)
    _update_competitor_events(conn, extracted, safe_text)
    _update_policies(conn, extracted)

    conn.commit()
    conn.close()
    print(f"[SAVE] obs_id={obs_id}, importance={importance}, tags={extracted['tags']}")
    return obs_id


def _calc_importance(extracted: Dict) -> float:
    """计算重要性分数 1.0-5.0"""
    score = 1.0
    if extracted.get("customers"):
        score += 1.0
    if extracted.get("competitors"):
        score += 0.5
    if extracted.get("projects"):
        score += 1.0
    if extracted.get("action_items"):
        score += 0.5
    if extracted.get("urgency") in ["urgent", "blocker"]:
        score += 1.0
    return min(score, 5.0)


def _update_customer_profiles(conn: sqlite3.Connection, extracted: Dict):
    """更新客户画像表"""
    for customer in extracted.get("customers", []):
        customer_id = _slugify(customer["name"])
        existing = conn.execute(
            "SELECT id FROM customer_profiles WHERE customer_id = ?",
            (customer_id,)
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE customer_profiles
                   SET last_updated = datetime('now'), visit_count = visit_count + 1
                   WHERE customer_id = ?""",
                (customer_id,)
            )
        else:
            conn.execute(
                """INSERT INTO customer_profiles (customer_id, name, region, level, department, profile_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (customer_id, customer["name"], customer.get("region", ""),
                 customer.get("level", ""), customer.get("department", ""),
                 json.dumps(customer, ensure_ascii=False))
            )

        # 联系人
        for contact in extracted.get("contacts", []):
            if contact.get("hospital") == customer["name"]:
                conn.execute(
                    """INSERT OR REPLACE INTO contacts (customer_id, name, title, role, last_interaction)
                       VALUES (?, ?, ?, ?, datetime('now'))""",
                    (customer_id, contact.get("name", ""), contact.get("title", ""), contact.get("role", ""))
                )


def _update_competitor_events(conn: sqlite3.Connection, extracted: Dict, raw_text: str):
    """更新竞品事件表"""
    for competitor in extracted.get("competitors", []):
        # 兼容字符串和字典格式
        if isinstance(competitor, dict):
            competitor_name = competitor.get("name", "")
        else:
            competitor_name = str(competitor)
        event_type = "mention"
        hospital = ""
        region = ""

        if extracted.get("customers"):
            hospital = extracted["customers"][0].get("name", "")
            region = extracted["customers"][0].get("region", "")

        conn.execute(
            """INSERT INTO competitor_events
               (competitor_name, event_type, hospital, region, description, event_date, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (competitor_name, event_type, hospital, region, raw_text[:200],
             datetime.now().isoformat(), "user_mention")
        )


def _update_policies(conn: sqlite3.Connection, extracted: Dict):
    """更新政策关联"""
    for code in extracted.get("charge_codes", []):
        existing = conn.execute(
            "SELECT id, related_hospitals FROM policies WHERE code = ?",
            (code,)
        ).fetchone()

        hospitals = [c["name"] for c in extracted.get("customers", [])]
        if existing:
            old_hospitals = json.loads(existing[1]) if existing[1] else []
            for h in hospitals:
                if h not in old_hospitals:
                    old_hospitals.append(h)
            conn.execute(
                "UPDATE policies SET related_hospitals = ? WHERE id = ?",
                (json.dumps(old_hospitals, ensure_ascii=False), existing[0])
            )
        else:
            name = {
                "250401014-a": "各种白介素测定",
                "250401013": "干扰素测定",
                "250404013-a": "肿瘤坏死因子测定",
                "250301023": "β淀粉样蛋白测定",
                "250301022": "磷酸化tau-181蛋白测定",
            }.get(code, f"收费项目({code})")
            conn.execute(
                """INSERT INTO policies (code, name, related_hospitals)
                   VALUES (?, ?, ?)""",
                (code, name, json.dumps(hospitals, ensure_ascii=False))
            )


def _slugify(name: str) -> str:
    """将医院名转为ID格式"""
    return name.lower().replace("医院", "").replace("市", "").replace("县", "").replace("区", "").replace("集团", "").replace("南京鼓楼", "").replace(" ", "_")


# ========== 检索 API ==========

def search(query: str, limit: int = 10, use_vector: bool = True) -> List[Dict]:
    """
    混合检索：FTS5 关键词 + KIMI Embedding 语义向量
    当向量API不可用时自动降级为纯关键词搜索
    """
    if use_vector:
        # 优先使用 search.py 中的 hybrid_search_v2（向量+关键词加权）
        try:
            from search import hybrid_search_v2
            return hybrid_search_v2(query, top_k=limit)
        except Exception as e:
            print(f"[SEARCH] hybrid_search_v2 失败，降级: {e}")
    # 降级：纯关键词
    return keyword_search(query, limit=limit)


def semantic_search(query: str, limit: int = 5) -> List[Dict]:
    """
    语义向量检索（纯向量，用于晨报 obs_id 标记和深度分析）
    如果 KIMI API 不可用，自动降级为关键词搜索
    """
    # 使用 memory_engine 中导入的 get_kimi_embedding（可被外部 patch 用于测试）
    query_vec = get_kimi_embedding(query)
    if query_vec is None:
        print("[SEMANTIC] Embedding API 不可用，降级为关键词搜索")
        return keyword_search(query, limit=limit)

    # 读取所有已存储向量（暴力扫描，适合 <1000 条数据）
    conn = get_conn()
    from database import get_all_vectors, cosine_similarity_py
    all_vec_records = get_all_vectors(conn)
    if not all_vec_records:
        conn.close()
        return keyword_search(query, limit=limit)

    scored = []
    for rec in all_vec_records:
        try:
            sim = cosine_similarity_py(query_vec, rec["vector"])
        except ValueError as e:
            print(f"[SEMANTIC] 维度跳过 obs_id={rec['obs_id']}: {e}")
            continue
        if sim >= 0.5:
            scored.append((rec["obs_id"], sim, rec["model_version"]))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_ids = [x[0] for x in scored[:limit]]

    if not top_ids:
        conn.close()
        return keyword_search(query, limit=limit)

    results = []
    for obs_id, sim, model_ver in scored[:limit]:
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


def get_customer_profile(customer_id: str) -> Optional[Dict]:
    """获取完整客户画像"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM customer_profiles WHERE customer_id = ?",
        (customer_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None

    profile = dict(row)
    profile["profile_json"] = json.loads(row["profile_json"]) if row["profile_json"] else {}
    profile["contacts"] = [dict(c) for c in conn.execute(
        "SELECT * FROM contacts WHERE customer_id = ?", (customer_id,)).fetchall()]
    profile["projects"] = [dict(p) for p in conn.execute(
        "SELECT * FROM projects WHERE customer_id = ?", (customer_id,)).fetchall()]
    profile["recent_observations"] = [dict(o) for o in conn.execute(
        """SELECT id, raw_content, timestamp, importance_score FROM observations
           WHERE raw_content LIKE ? ORDER BY timestamp DESC LIMIT 5""",
        (f"%{profile['name']}%",)).fetchall()]
    conn.close()
    return profile


def list_customers(region: Optional[str] = None, limit: int = 50) -> List[Dict]:
    """列出客户列表"""
    conn = get_conn()
    if region:
        rows = conn.execute(
            "SELECT * FROM customer_profiles WHERE region = ? ORDER BY last_updated DESC LIMIT ?",
            (region, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM customer_profiles ORDER BY last_updated DESC LIMIT ?",
            (limit,)
        ).fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return result


def get_competitor_timeline(competitor_name: str, days: int = 30) -> List[Dict]:
    """获取竞品最近事件"""
    conn = get_conn()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT * FROM competitor_events
           WHERE competitor_name = ? AND event_date > ?
           ORDER BY event_date DESC""",
        (competitor_name, since)
    ).fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return result


def get_recent_observations(hours: int = 24, tag: Optional[str] = None) -> List[Dict]:
    """获取最近观察记录"""
    conn = get_conn()
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    if tag:
        rows = conn.execute(
            """SELECT * FROM observations
               WHERE timestamp > ? AND tags LIKE ?
               ORDER BY importance_score DESC, timestamp DESC""",
            (since, f"%{tag}%")
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM observations
               WHERE timestamp > ?
               ORDER BY importance_score DESC, timestamp DESC""",
            (since,)
        ).fetchall()
    result = [{
        "id": r["id"], "timestamp": r["timestamp"], "source": r["source"],
        "summary": json.loads(r["extracted_json"]).get("summary", "") if r["extracted_json"] else "",
        "importance": r["importance_score"], "tags": r["tags"],
    } for r in rows]
    conn.close()
    return result


# ========== AgentMemory 桥接 ==========

def sync_to_diary(output_path: Optional[str] = None) -> str:
    """
    将当天重要观察同步到 memory/YYYY-MM-DD.md
    返回写入文件路径
    """
    from datetime import date
    today = date.today().isoformat()
    if output_path is None:
        output_path = os.path.expanduser(f"~/.openclaw/workspace/memory/{today}.md")

    obs = get_recent_observations(hours=24)
    if not obs:
        return ""

    lines = [f"\n## 销售记忆引擎自动同步 ({datetime.now().strftime('%H:%M')})\n"]
    for o in obs:
        if o["importance"] >= 2.0:
            lines.append(f"- **{o['summary']}** (重要性: {o['importance']:.1f})\n")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[SYNC] 已同步 {len([l for l in lines if l.startswith('-')])} 条到 {output_path}")
    return output_path


# ========== 测试 ==========
if __name__ == "__main__":
    init_db()
    test = "盱眙县中医院张主任说细胞因子想上六项，瑞斯凯尔也在接触。"
    obs_id = save_observation(test, session_id="test-v2")
    print(f"\n--- 关键词检索 '盱眙' ---")
    for r in search("盱眙", limit=3):
        print(f"  [{r['source']}] score={r.get('score', 0):.2f} | {r['content'][:40]}...")
    print(f"\n--- 语义检索 '瑞斯凯尔竞争' ---")
    for r in semantic_search("瑞斯凯尔竞争", limit=3):
        print(f"  score={r.get('hybrid_score', r.get('score', 0)):.2f} | {r.get('raw_content', r.get('content', ''))[:40]}...")
