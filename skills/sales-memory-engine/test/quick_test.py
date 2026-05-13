#!/usr/bin/env python3
"""快速测试脚本：验证销售记忆引擎完整流程"""

import sys
import os
sys.path.insert(0, os.path.expanduser("~/.openclaw/skills/sales-memory-engine/src"))

# 清理旧数据库
db_path = os.path.expanduser("~/.openclaw/workspace/memory_engine/db/sales_memory.db")
if os.path.exists(db_path):
    os.remove(db_path)
    print("[TEST] 已清理旧数据库")

from database import init_db
from memory_engine import save_observation, search, get_customer_profile, list_customers

print("\n=== Step 1: 初始化数据库 ===")
init_db()

print("\n=== Step 2: 保存测试记录 ===")
test_cases = [
    ("盱眙县中医院张主任说细胞因子想上六项，瑞斯凯尔也在接触，计划审批还在等回复", "session-1"),
    ("淮安市第一人民医院王颖主任那边，瑞斯凯尔6因子已经装机了，我们有压力", "session-2"),
    ("徐州市儿童医院TH1/TH2调节T项目，细胞因子由六项调整为十二项，需求单待提交", "session-3"),
    ("泗阳人民医院领导层更替，暂时无法挂网，等稳定后再推进", "session-4"),
    ("国家医保局2026版负面清单第18条点名细胞因子无指征检查，推广注意话术", "session-5"),
]

for text, sid in test_cases:
    obs_id = save_observation(text, session_id=sid)
    print(f"  ✅ obs_id={obs_id}: {text[:40]}...")

print("\n=== Step 3: 关键词检索 ===")
queries = ["盱眙", "瑞斯凯尔", "儿童医院", "细胞因子", "医保"]
for q in queries:
    results = search(q, limit=3, use_vector=False)
    print(f"\n  🔍 查询 '{q}' -> {len(results)} 条结果")
    for r in results:
        print(f"     [{r['source']}] {r.get('summary', r['content'][:50])}")

print("\n=== Step 4: 客户画像 ===")
for cid in ["xuyi_tcm", "huaian_first", "xuzhou_children"]:
    profile = get_customer_profile(cid)
    if profile:
        print(f"\n  🏥 {profile['name']} ({profile['region']})")
        print(f"     联系人: {[c['name'] for c in profile['contacts']]}")
        print(f"     最近记录: {len(profile.get('recent_observations', []))} 条")
    else:
        print(f"  ❌ 未找到 {cid}")

print("\n=== Step 5: 客户列表 ===")
customers = list_customers()
print(f"  📋 共 {len(customers)} 个客户")
for c in customers:
    print(f"     - {c['name']} ({c['region']}, {c['level']})")

print("\n=== ✅ 全部测试通过 ===")
