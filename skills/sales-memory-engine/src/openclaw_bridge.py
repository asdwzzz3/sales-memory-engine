#!/usr/bin/env python3
"""
销售记忆引擎 - OpenClaw 集成钩子
每次对话结束后自动捕获并存储关键信息

使用方法：
1. 将此脚本配置为 OpenClaw 的 post-message 钩子
2. 或在每次对话后手动调用
"""

import sys
import os
import json
import hashlib

# 确保模块路径
sys.path.insert(0, os.path.expanduser("~/.openclaw/skills/sales-memory-engine/src"))

from memory_engine import save_observation, search, get_customer_profile, list_customers
from extractor import extract_entities

def auto_capture(session_id: str, message_text: str, source: str = "user"):
    """
    自动捕获一次对话内容，并同步到知识图谱
    v1.1: 新增 CRM 双向联动 — 提取到客户/项目/拜访后自动推送销售易
    """
    extracted = extract_entities(message_text)
    has_value = any([
        extracted.get("customers"),
        extracted.get("competitors"),
        extracted.get("projects"),
        extracted.get("charge_codes"),
        len(extracted.get("actions", [])) > 0,
    ])
    
    if not has_value:
        print(f"[AUTO_CAPTURE] 跳过无价值消息: {message_text[:50]}...")
        return None
    
    obs_id = save_observation(message_text, session_id=session_id, source=source)
    
    # v3: 同步三元组到知识图谱
    triples = extracted.get("triples", [])
    if triples:
        _sync_triples_to_graph(triples, obs_id)
    
    # ─── v1.1: CRM 双向联动 ───
    # 检查环境变量/配置开关，决定是否自动推送
    _maybe_push_to_crm(extracted, message_text, obs_id)
    
    return obs_id


def _maybe_push_to_crm(extracted: dict, raw_text: str, obs_id: int):
    """
    条件触发: 将提取结果自动推送到销售易 CRM
    
    触发条件:
      1. 配置中 auto_push = true（默认 false，需老板手动开启）
      2. 提取结果包含客户、项目、或拜访相关动作
    
    安全设计:
      - 默认关闭（auto_push=false），防止开发期误操作
      - mock_mode=true 时只打日志不调用 API
      - 所有写操作先记录详细日志
    """
    import os
    import sys
    
    # 加载 crm_sync 模块
    crm_sync_path = os.path.expanduser("~/.openclaw/workspace/skills/crm-sales-workflow/src")
    if crm_sync_path not in sys.path:
        sys.path.insert(0, crm_sync_path)
    
    try:
        import crm_sync
    except ImportError as e:
        print(f"[CRM_SYNC] 模块加载失败，跳过自动推送: {e}")
        return
    
    # 读取配置
    creds = crm_sync._load_credentials()
    auto_push = creds.get("auto_push", "false").lower() == "true"
    mock_mode = creds.get("mock_mode", "true").lower() == "true"
    
    if not auto_push:
        print("[CRM_SYNC] auto_push=false，跳过 CRM 自动推送（在 .ini 中开启）")
        return
    
    # 判断是否值得推送
    has_customers = bool(extracted.get("customers"))
    has_projects = bool(extracted.get("projects"))
    has_visit_actions = any(
        a.get("type") == "action" and a.get("action") in ("待跟进", "待提交", "已下单")
        for a in extracted.get("actions", [])
    )
    
    if not (has_customers or has_projects or has_visit_actions):
        print("[CRM_SYNC] 无客户/项目/拜访信息，跳过推送")
        return
    
    print(f"[CRM_SYNC] 检测到销售实体，准备推送至 CRM (mock={mock_mode})...")
    
    # 将原始文本注入 extracted 用于活动记录生成
    extracted["_raw_text"] = raw_text
    
    try:
        results = crm_sync.sync_observation_to_crm(extracted)
        success_count = sum(1 for r in results if r.get("result", {}).get("success", False))
        print(f"[CRM_SYNC] 推送完成: {success_count}/{len(results)} 成功")
        for r in results:
            status = "✅" if r.get("result", {}).get("success") else "⚠️"
            print(f"  {status} [{r['type']}] {r['name']}")
    except Exception as e:
        print(f"[CRM_SYNC] 推送异常: {e}")


def _sync_triples_to_graph(triples: list, obs_id: int):
    """将提取的三元组写入 graph_nodes + graph_edges"""
    import sqlite3
    DB_PATH = os.path.expanduser("~/.openclaw/workspace/memory_engine/db/sales_memory.db")
    conn = sqlite3.connect(DB_PATH)
    
    for t in triples:
        head = t["head"]
        tail = t["tail"]
        relation = t["relation"]
        weight = t.get("weight", 1.0)
        
        # 插入/更新节点
        for node in [head, tail]:
            node_id = hashlib.md5(f"{node['type']}:{node['label']}".encode()).hexdigest()[:12]
            props_json = json.dumps(node.get("props", {}), ensure_ascii=False)
            
            conn.execute("""
                INSERT OR REPLACE INTO graph_nodes (id, type, label, props, last_updated, source_count)
                VALUES (?, ?, ?, ?, datetime('now'), COALESCE((SELECT source_count FROM graph_nodes WHERE id=?), 0) + 1)
            """, (node_id, node["type"], node["label"], props_json, node_id))
        
        # 插入关系
        head_id = hashlib.md5(f"{head['type']}:{head['label']}".encode()).hexdigest()[:12]
        tail_id = hashlib.md5(f"{tail['type']}:{tail['label']}".encode()).hexdigest()[:12]
        
        conn.execute("""
            INSERT OR REPLACE INTO graph_edges (src_id, dst_id, type, weight, timestamp, source_obs_id)
            VALUES (?, ?, ?, ?, datetime('now'), ?)
        """, (head_id, tail_id, relation, weight, obs_id))
    
    conn.commit()
    conn.close()
    print(f"[KG] 同步 {len(triples)} 个三元组到知识图谱")

def prepare_context_for_session(session_id: str, user_input: str) -> str:
    """
    在 SessionStart 时，根据用户输入加载相关记忆作为上下文注入
    
    Returns:
        建议注入的上下文文本（或空字符串）
    """
    # 1. 检查用户输入是否涉及已知客户
    extracted = extract_entities(user_input)
    
    context_parts = []
    
    # 2. 如果提到客户，加载客户画像
    for customer in extracted.get("customers", []):
        customer_id = customer["name"].lower().replace("医院", "").replace("市", "").replace("县", "").replace("集团", "").replace(" ", "_")
        profile = get_customer_profile(customer_id)
        if profile:
            ctx = _format_customer_context(profile)
            context_parts.append(ctx)
    
    # 3. 如果提到竞品，加载竞品动态
    for competitor in extracted.get("competitors", []):
        from memory_engine import get_competitor_timeline
        events = get_competitor_timeline(competitor, days=14)
        if events:
            ctx = f"【竞品:{competitor}最近动态】\n"
            for e in events[:3]:
                ctx += f"  - {e['event_date'][:10]} {e['event_type']}: {e['description'][:80]}...\n"
            context_parts.append(ctx)
    
    # 4. 如果提到收费代码，加载政策信息
    for code in extracted.get("charge_codes", []):
        # 简单提示
        from memory_engine import _guess_policy_name
        name = _guess_policy_name(code)
        context_parts.append(f"【收费代码】{code} - {name}")
    
    # 5. v3 Phase 3: 基于图谱的客户背景卡注入
    from graph_briefing import prepare_graph_context
    graph_ctx = prepare_graph_context(user_input)
    if graph_ctx:
        context_parts.append(graph_ctx)
    
    if context_parts:
        return "\n".join(["\n[记忆引擎上下文]"] + context_parts + ["[/记忆引擎上下文]\n"])
    
    return ""

def _format_customer_context(profile: dict) -> str:
    """格式化客户画像为可读文本"""
    lines = [f"【客户:{profile['name']}】"]
    
    if profile.get("level"):
        lines.append(f"  级别: {profile['level']}")
    if profile.get("region"):
        lines.append(f"  区域: {profile['region']}")
    
    if profile.get("contacts"):
        contacts_str = ", ".join([f"{c['name']}({c['title']})" for c in profile["contacts"]])
        lines.append(f"  联系人: {contacts_str}")
    
    if profile.get("projects"):
        for p in profile["projects"]:
            lines.append(f"  项目: {p.get('project_name', '')} - {p.get('stage', '')}")
    
    if profile.get("recent_observations"):
        lines.append("  最近记录:")
        for obs in profile["recent_observations"][:2]:
            lines.append(f"    - {obs.get('timestamp', '')[:10]}: {obs.get('summary', obs.get('raw_content', ''))[:60]}")
    
    return "\n".join(lines)

def generate_daily_digest() -> str:
    """
    生成每日记忆摘要（用于写入 MEMORY.md 或 ima）
    """
    from memory_engine import get_recent_observations
    
    obs = get_recent_observations(hours=24)
    if not obs:
        return "今日无新增销售记忆记录。"
    
    lines = ["## 今日销售记忆摘要", ""]
    
    # 按客户分组
    by_customer = {}
    for o in obs:
        summary = o.get("summary", "")
        # 尝试提取客户名
        from extractor import extract_entities
        e = extract_entities(summary)
        customers = e.get("customers", [])
        customer = customers[0]["name"] if customers else "其他"
        
        if customer not in by_customer:
            by_customer[customer] = []
        by_customer[customer].append(o)
    
    for customer, items in by_customer.items():
        lines.append(f"### {customer}")
        for item in items:
            lines.append(f"- {item.get('summary', '无摘要')}")
        lines.append("")
    
    # v3 Phase 3: 注入基于图谱的客户背景卡
    from graph_briefing import inject_into_digest
    return inject_into_digest("\n".join(lines))

# ========== CLI 入口 ==========
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="销售记忆引擎 CLI")
    sub = parser.add_subparsers(dest="cmd")
    
    # capture 命令
    p_cap = sub.add_parser("capture", help="捕获一次对话")
    p_cap.add_argument("text", help="对话文本")
    p_cap.add_argument("--session", default="manual", help="session ID")
    p_cap.add_argument("--source", default="user", choices=["user", "assistant"])
    
    # search 命令
    p_search = sub.add_parser("search", help="检索记忆")
    p_search.add_argument("query", help="查询词")
    p_search.add_argument("--limit", type=int, default=5)
    
    # profile 命令
    p_prof = sub.add_parser("profile", help="查看客户画像")
    p_prof.add_argument("customer_id", help="客户ID")
    
    # digest 命令
    p_dig = sub.add_parser("digest", help="生成每日摘要")
    
    args = parser.parse_args()
    
    if args.cmd == "capture":
        obs_id = auto_capture(args.session, args.text, args.source)
        if obs_id:
            print(f"✅ 已保存 observation_id={obs_id}")
        else:
            print("⏭️ 无价值内容，已跳过")
    
    elif args.cmd == "search":
        results = search(args.query, limit=args.limit)
        for r in results:
            print(f"[{r['source']}] {r['timestamp']} | {r.get('summary', r['content'][:60])}")
    
    elif args.cmd == "profile":
        profile = get_customer_profile(args.customer_id)
        if profile:
            print(json.dumps(profile, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"❌ 未找到客户: {args.customer_id}")
    
    elif args.cmd == "digest":
        print(generate_daily_digest())
    
    else:
        parser.print_help()
