#!/usr/bin/env python3
"""
销售记忆引擎 - Faiss 向量索引模块
提供高效的近似最近邻搜索（ANN），替代暴力扫描

设计:
- IndexFlatIP: 内积索引，因向量已归一化，IP == 余弦相似度
- 持久化: 索引文件保存在 db_dir/faiss.index + ids 映射文件
- 增量更新: 支持 add/remove/rebuild
- 线程安全: 写操作加锁（文件锁或 threading.Lock）
"""

import os
import json
import struct
import sqlite3
import threading
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# 懒加载 faiss（避免未安装时整个模块崩溃）
_faiss = None

def _ensure_faiss():
    global _faiss
    if _faiss is None:
        import faiss
        _faiss = faiss
    return _faiss

# ─── 配置 ───
DB_DIR = Path(os.path.expanduser("~/.openclaw/workspace/memory_engine/db"))
INDEX_PATH = DB_DIR / "faiss.index"
IDS_PATH = DB_DIR / "faiss_ids.json"
DIM = 384  # all-MiniLM-L6-v2 维度

# 线程锁
_index_lock = threading.Lock()

# 内存中缓存的 index + id 映射
_index_cache = None
_id_map_cache = None  # faiss internal id -> obs_id


def _get_id_map() -> Dict[int, int]:
    """读取 id 映射文件: {faiss_internal_id: obs_id}"""
    if IDS_PATH.exists():
        with open(IDS_PATH, "r", encoding="utf-8") as f:
            # 存的是 list: [obs_id0, obs_id1, ...]，index=faiss_internal_id
            obs_ids = json.load(f)
            return {i: obs_id for i, obs_id in enumerate(obs_ids)}
    return {}


def _save_id_map(id_map: Dict[int, int]):
    """保存 id 映射"""
    if not id_map:
        if IDS_PATH.exists():
            IDS_PATH.unlink()
        return
    # 按 faiss internal id 排序
    max_id = max(id_map.keys())
    obs_ids = [id_map.get(i, -1) for i in range(max_id + 1)]
    with open(IDS_PATH, "w", encoding="utf-8") as f:
        json.dump(obs_ids, f)


def build_index(vectors: List[Tuple[int, List[float]]], dim: int = DIM) -> Tuple[Optional[object], Dict[int, int]]:
    """
    从 (obs_id, vector) 列表构建 faiss 索引
    
    Returns:
        (index, id_map) 或 (None, {}) 如果 vectors 为空
    """
    faiss = _ensure_faiss()
    if not vectors:
        return None, {}
    
    # 分离 obs_id 和 vector
    obs_ids = []
    vecs = []
    for obs_id, vec in vectors:
        obs_ids.append(obs_id)
        vecs.append(vec)
    
    # 转为 float32 numpy array
    xb = np.array(vecs, dtype=np.float32)
    n, d = xb.shape
    
    # 归一化（确保余弦相似度 = 内积）
    faiss.normalize_L2(xb)
    
    # 创建 FlatIP 索引（精确搜索，适合 <10万条）
    index = faiss.IndexFlatIP(d)
    index.add(xb)
    
    # id_map: faiss_internal_id -> obs_id
    id_map = {i: obs_ids[i] for i in range(n)}
    
    return index, id_map


def save_index(index, id_map: Dict[int, int]):
    """保存索引到磁盘"""
    faiss = _ensure_faiss()
    DB_DIR.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(INDEX_PATH))
    _save_id_map(id_map)


def load_index() -> Tuple[Optional[object], Dict[int, int]]:
    """从磁盘加载索引"""
    faiss = _ensure_faiss()
    if not INDEX_PATH.exists():
        return None, {}
    
    index = faiss.read_index(str(INDEX_PATH))
    id_map = _get_id_map()
    return index, id_map


def rebuild_index_from_db(db_path: Optional[str] = None) -> Tuple[Optional[object], Dict[int, int]]:
    """
    从 SQLite 数据库重建索引
    
    Args:
        db_path: 数据库路径，默认使用 database.DB_PATH
    """
    # 避免循环导入
    from database import DB_PATH as DEFAULT_DB_PATH, get_conn
    
    db = db_path or str(DEFAULT_DB_PATH)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    
    rows = conn.execute(
        "SELECT obs_id, vector FROM observations_vectors"
    ).fetchall()
    conn.close()
    
    vectors = []
    for row in rows:
        vec = json.loads(row["vector"])
        if len(vec) == DIM:
            vectors.append((row["obs_id"], vec))
        else:
            # 维度不匹配，跳过
            pass
    
    index, id_map = build_index(vectors)
    if index:
        save_index(index, id_map)
    
    return index, id_map


def search_index(query_vec: List[float], top_k: int = 5, min_score: float = 0.3) -> List[Tuple[int, float]]:
    """
    在索引中搜索最近邻
    
    Args:
        query_vec: 查询向量
        top_k: 返回 top_k 个结果
        min_score: 最低相似度阈值
    
    Returns:
        [(obs_id, score), ...] 按 score 降序
    """
    global _index_cache, _id_map_cache
    
    # 懒加载缓存
    if _index_cache is None:
        _index_cache, _id_map_cache = load_index()
    
    if _index_cache is None or _index_cache.ntotal == 0:
        return []
    
    faiss = _ensure_faiss()
    
    # 查询向量归一化
    xq = np.array([query_vec], dtype=np.float32)
    faiss.normalize_L2(xq)
    
    # 搜索
    scores, indices = _index_cache.search(xq, top_k)
    
    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        if score < min_score:
            continue
        obs_id = _id_map_cache.get(int(idx))
        if obs_id is not None:
            results.append((obs_id, float(score)))
    
    return results


def add_vector(obs_id: int, vector: List[float]):
    """向索引中添加单个向量（增量更新）"""
    global _index_cache, _id_map_cache
    
    with _index_lock:
        # 重新加载最新索引
        index, id_map = load_index()
        
        if index is None:
            # 首次添加，创建新索引
            index, id_map = build_index([(obs_id, vector)])
        else:
            faiss = _ensure_faiss()
            x = np.array([vector], dtype=np.float32)
            faiss.normalize_L2(x)
            index.add(x)
            new_id = index.ntotal - 1
            id_map[new_id] = obs_id
        
        save_index(index, id_map)
        _index_cache = index
        _id_map_cache = id_map


def remove_vector(obs_id: int):
    """
    从索引中移除指定 obs_id 的向量
    
    注意: faiss 的 IndexFlat 不支持直接删除，这里采用标记删除+重建策略
    如果删除比例 > 10%，触发重建
    """
    global _index_cache, _id_map_cache
    
    with _index_lock:
        index, id_map = load_index()
        if index is None:
            return
        
        # 找出要删除的 internal id
        to_remove = [k for k, v in id_map.items() if v == obs_id]
        if not to_remove:
            return
        
        # 从 id_map 中移除
        for k in to_remove:
            del id_map[k]
        
        # 如果删除比例 > 10%，直接重建
        total = index.ntotal
        removed = len(to_remove)
        if total > 0 and removed / total > 0.1:
            rebuild_index_from_db()
        else:
            # 否则保存更新后的 id_map（搜索时会跳过已删除的 internal id）
            _save_id_map(id_map)
            _id_map_cache = id_map


# ─── 初始化检查 ───
def ensure_index_exists():
    """确保索引存在，如果不存在则从数据库重建"""
    if not INDEX_PATH.exists():
        rebuild_index_from_db()


if __name__ == "__main__":
    # 测试
    print("[FaissIndex] 测试模式")
    ensure_index_exists()
    idx, id_map = load_index()
    if idx:
        print(f"[FaissIndex] 索引加载成功: {idx.ntotal} 条向量")
    else:
        print("[FaissIndex] 索引为空，将从数据库重建...")
        rebuild_index_from_db()
        idx, id_map = load_index()
        if idx:
            print(f"[FaissIndex] 重建成功: {idx.ntotal} 条向量")
