#!/usr/bin/env python3
"""
销售记忆引擎 - 向量语义搜索测试脚本
验证: save → embed → semantic_search 全链路 + fallback
"""

import os
import sys
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# 确保模块路径
sys.path.insert(0, os.path.expanduser("~/.openclaw/skills/sales-memory-engine/src"))

from database import (
    get_conn, init_db,
    save_observation_vector, get_all_vectors, get_vector_by_obs_id,
    cosine_similarity_py,
)
from search import (
    get_kimi_embedding, keyword_search, hybrid_search_v2,
)
from memory_engine import save_observation, semantic_search


# ========== Mock 向量工具 ==========

def _make_mock_embedding(text: str, dim: int = 384) -> list:
    """
    基于文本哈希生成确定性 mock 向量，用于测试
    不依赖外部 API，但模拟真实向量的行为
    """
    import hashlib
    seed = hashlib.md5(text.encode("utf-8")).hexdigest()
    # 用 seed 生成一个单位向量
    vec = [0.0] * dim
    for i in range(dim):
        # 伪随机但确定性的值
        byte_val = int(seed[(i * 2) % 32 : (i * 2) % 32 + 2], 16)
        vec[i] = (byte_val - 127.5) / 127.5
    # 归一化
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


# ========== 测试类 ==========

class TestSemanticSearch(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 使用临时数据库，避免污染生产数据
        cls.tmp_dir = tempfile.mkdtemp(prefix="sales_memory_test_")
        cls.db_path = os.path.join(cls.tmp_dir, "test.db")
        # 注入临时 DB 路径
        import database as db_mod
        db_mod.DB_PATH = cls.db_path
        # 重新初始化
        init_db()

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_01_database_schema(self):
        """测试 observations_vectors 表已创建"""
        conn = get_conn()
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='observations_vectors'"
        )
        self.assertIsNotNone(cur.fetchone())
        conn.close()
        print("✅ test_01_database_schema passed")

    def test_02_save_and_retrieve_vector(self):
        """测试向量保存和读取"""
        conn = get_conn()
        try:
            # 先插入一条 dummy observation 满足外键约束
            conn.execute(
                "INSERT INTO observations (session_id, source, raw_content, extracted_json, importance_score, tags) VALUES (?, ?, ?, ?, ?, ?)",
                ("test", "user", "dummy", "{}", 1.0, "test")
            )
            dummy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            test_vec = [0.1, 0.2, 0.3, 0.4]
            save_observation_vector(conn, obs_id=dummy_id, vector=test_vec, model_version="test-v1")
            conn.commit()

            retrieved = get_vector_by_obs_id(conn, dummy_id)
            self.assertEqual(retrieved, test_vec)

            all_vecs = get_all_vectors(conn)
            self.assertTrue(any(v["obs_id"] == dummy_id for v in all_vecs))
        finally:
            conn.close()
        print("✅ test_02_save_and_retrieve_vector passed")

    def test_03_cosine_similarity_py(self):
        """测试纯Python余弦相似度"""
        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0, 0.0]
        c = [0.0, 1.0, 0.0]
        self.assertAlmostEqual(cosine_similarity_py(a, b), 1.0, places=5)
        self.assertAlmostEqual(cosine_similarity_py(a, c), 0.0, places=5)
        print("✅ test_03_cosine_similarity_py passed")

    @patch("memory_engine.get_kimi_embedding")
    def test_04_semantic_search_mock_api(self, mock_embed):
        """
        Mock KIMI API，测试语义搜索全链路
        使用确定性 mock 向量：query 与文档 A/B 相似度高，与 C 相似度低
        """
        dim = 8
        # 构造可控向量
        vec_a = [0.95, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # 盱眙细胞因子
        vec_b = [0.90, 0.10, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # 儿童医院细胞因子
        vec_c = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]    # 泗阳领导层
        query_vec = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # 细胞因子项目

        text_to_vec = {
            "盱眙县中医院张主任说细胞因子想上六项，瑞斯凯尔也在接触。": vec_a,
            "徐州市儿童医院TH1/TH2调节T项目需求单等待提交，细胞因子由六项调整为十二项。": vec_b,
            "泗阳人民医院领导层更替，暂时无法挂网，需等待新领导就位。": vec_c,
        }
        def side_effect(text):
            return text_to_vec.get(text, query_vec)
        mock_embed.side_effect = side_effect

        # 保存 3 条 observation
        texts = list(text_to_vec.keys())
        obs_ids = []
        for t in texts:
            obs_id = save_observation(t, session_id="test-sem", source="user")
            obs_ids.append(obs_id)

        # 验证向量已存入
        conn = get_conn()
        try:
            all_vecs = get_all_vectors(conn)
            self.assertGreaterEqual(len(all_vecs), 3, f"向量库中应有至少3条，实际 {len(all_vecs)}")
        finally:
            conn.close()

        # 语义搜索：查询"细胞因子项目"
        results = semantic_search("细胞因子项目", limit=5)
        # 至少命中前两条（都包含"细胞因子"语义）
        hit_ids = {r["id"] for r in results}
        self.assertTrue(
            obs_ids[0] in hit_ids or obs_ids[1] in hit_ids,
            f"语义搜索应命中细胞因子相关记录，但命中: {hit_ids}"
        )

        print(f"✅ test_04_semantic_search_mock_api passed (命中 {len(hit_ids)} 条)")

    @patch("memory_engine.get_kimi_embedding")
    def test_05_semantic_search_ranking(self, mock_embed):
        """
        测试语义搜索的排序合理性：越相关的应该排在越前面
        使用与 test_04 正交的维度，避免旧数据干扰
        """
        # 向量集中在第2、3维，与 test_04 的第0、1维正交
        vec_a = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]   # 对应 "贝克曼流式细胞仪"
        vec_b = [0.0, 0.0, 0.9, 0.1, 0.0, 0.0, 0.0, 0.0]   # 对应 "流式检测项目"
        vec_c = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]   # 对应 "医院行政管理"
        query_vec = [0.0, 0.0, 0.95, 0.05, 0.0, 0.0, 0.0, 0.0]  # 对应 "贝克曼流式"

        text_to_vec = {
            "贝克曼流式细胞仪": vec_a,
            "流式检测项目": vec_b,
            "医院行政管理": vec_c,
        }
        def side_effect(text):
            return text_to_vec.get(text, query_vec)
        mock_embed.side_effect = side_effect

        obs_ids = []
        for t in text_to_vec.keys():
            obs_id = save_observation(t, session_id="test-rank", source="user")
            obs_ids.append(obs_id)

        # 查询与 vec_a 最接近的文本
        results = semantic_search("贝克曼流式", limit=3)
        self.assertGreater(len(results), 0, "语义搜索应返回至少1条结果")
        top_result = results[0]
        # 最相关应该是 "贝克曼流式细胞仪"
        self.assertEqual(top_result["content"], "贝克曼流式细胞仪")
        self.assertGreater(top_result["score"], 0.9, f"top1 相似度应 > 0.9，实际 {top_result['score']}")

        print(f"✅ test_05_semantic_search_ranking passed (top1={top_result['content']}, score={top_result['score']:.3f})")

    @patch("search.get_fastembed_embedding")
    def test_06_fallback_to_keyword(self, mock_embed):
        """
        当 fastembed 不可用时（返回 None），应降级为关键词搜索
        """
        mock_embed.return_value = None  # 模拟 fastembed 不可用

        # 先保存一条记录（这里也会用 mock，返回 None，所以不会存向量）
        save_observation("盱眙县中医院细胞因子项目跟进", session_id="test-fb", source="user")

        # 查询时 fastembed 返回 None，应触发 fallback
        results = semantic_search("盱眙县中医院", limit=5)
        self.assertGreater(len(results), 0, "fallback 关键词搜索应返回至少1条结果")
        # 至少有一条是关键词命中的
        sources = {r.get("source", "") for r in results}
        self.assertTrue(
            "keyword" in sources or "fts5" in sources,
            f"fallback 时应返回 keyword/fts5 结果，实际 source={sources}"
        )

        print(f"✅ test_06_fallback_to_keyword passed (sources={sources})")

    @patch("search.get_fastembed_embedding")
    def test_07_hybrid_search_v2(self, mock_embed):
        """测试混合检索"""
        dim = 8
        def side_effect(text):
            return _make_mock_embedding(text, dim=dim)
        mock_embed.side_effect = side_effect

        save_observation("徐州市儿童医院淋巴细胞亚群项目已立项", session_id="test-hyb", source="user")
        save_observation("宿迁某医院竞品瑞斯凯尔中标", session_id="test-hyb", source="user")

        results = hybrid_search_v2("淋巴细胞亚群", top_k=5)
        self.assertGreater(len(results), 0)
        # 混合检索应该包含 semantic 或 keyword 的结果
        print(f"✅ test_07_hybrid_search_v2 passed (返回 {len(results)} 条)")

    def test_08_dimension_mismatch_handling(self):
        """
        测试向量维度不匹配时的处理（新旧模型切换场景）
        """
        conn = get_conn()
        try:
            # 先插入两条 dummy observations 满足外键约束
            conn.execute(
                "INSERT INTO observations (session_id, source, raw_content, extracted_json, importance_score, tags) VALUES (?, ?, ?, ?, ?, ?)",
                ("test", "user", "dummy1", "{}", 1.0, "test")
            )
            id1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO observations (session_id, source, raw_content, extracted_json, importance_score, tags) VALUES (?, ?, ?, ?, ?, ?)",
                ("test", "user", "dummy2", "{}", 1.0, "test")
            )
            id2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # 保存一个 4 维向量
            save_observation_vector(conn, obs_id=id1, vector=[1.0, 0.0, 0.0, 0.0], model_version="old-model")
            # 保存一个 3 维向量
            save_observation_vector(conn, obs_id=id2, vector=[0.0, 1.0, 0.0], model_version="new-model")
            conn.commit()

            # 构造一个 3 维 query 向量
            query_vec = [0.0, 1.0, 0.0]

            # 读取所有向量并计算相似度
            all_vecs = get_all_vectors(conn)
            scored = []
            for rec in all_vecs:
                if rec["obs_id"] in (id1, id2):
                    try:
                        sim = cosine_similarity_py(query_vec, rec["vector"])
                        scored.append((rec["obs_id"], sim))
                    except ValueError:
                        # 维度不匹配应被捕获
                        scored.append((rec["obs_id"], None))

            # id1 应该因为维度不匹配而被跳过（score=None）
            mismatched = [s for s in scored if s[0] == id1]
            matched = [s for s in scored if s[0] == id2]
            self.assertTrue(any(s[1] is None for s in mismatched), "维度不匹配应返回 None")
            self.assertTrue(any(s[1] is not None and s[1] > 0.9 for s in matched), "维度匹配应返回高相似度")
        finally:
            conn.close()

        print("✅ test_08_dimension_mismatch_handling passed")


# ========== 运行入口 ==========
if __name__ == "__main__":
    print("=" * 60)
    print("销售记忆引擎 - 向量语义搜索测试")
    print("=" * 60)
    unittest.main(verbosity=2)
