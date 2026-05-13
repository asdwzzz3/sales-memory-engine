#!/usr/bin/env python3
"""
v3 Phase 3 — 基于知识图谱的客户背景卡生成器
从图存储 + 向量层生成 Markdown 客户背景卡，用于晨报注入和会话上下文
"""

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

sys.path.insert(0, os.path.expanduser("~/.openclaw/skills/sales-memory-engine/src"))

from knowledge_graph import KnowledgeGraph, get_customer_network, recommend_next_visit
from memory_engine import get_recent_observations, get_customer_profile, hybrid_search


def generate_customer_card(hospital_label: str, include_recommendations: bool = True) -> str:
    """
    生成基于知识图谱的客户背景卡 (Markdown 格式)
    
    返回: Markdown 字符串，可直接注入晨报或会话上下文
    """
    lines = []
    
    # ─── 1. 图查询：客户网络 ───
    kg = KnowledgeGraph()
    kg.load_from_db()
    
    network = get_customer_network(kg, hospital_label, depth=2)
    
    if "error" in network:
        # 图谱中无数据，降级为向量检索
        return _generate_fallback_card(hospital_label)
    
    center = network.get("center", {})
    nodes = network.get("nodes", [])
    edges = network.get("edges", [])
    stats = network.get("stats", {})
    
    # ─── 2. 向量层：最近观察 ───
    recent_obs = _get_recent_obs_for_hospital(hospital_label)
    
    # ─── 3. 客户基本信息 ───
    lines.append(f"📊 客户背景卡：{hospital_label}")
    lines.append("━" * 40)
    lines.append("")
    
    # 从图谱节点中提取医院属性
    hospital_node = next((n for n in nodes if n.get("label") == hospital_label and n.get("distance", 0) == 0), {})
    props = hospital_node.get("props", {})
    
    basic_info = []
    if props.get("region"):
        basic_info.append(f"📍 区域: {props['region']}")
    if props.get("level"):
        basic_info.append(f"🏥 等级: {props['level']}")
    if props.get("department"):
        basic_info.append(f"🔬 科室: {props['department']}")
    
    if basic_info:
        lines.append(" | ".join(basic_info))
        lines.append("")
    
    # ─── 4. 关键联系人 ───
    contacts = [n for n in nodes if n.get("type") == "Person"]
    if contacts:
        lines.append(f"👥 关键联系人 ({len(contacts)}人)")
        for c in contacts:
            title = c.get("props", {}).get("title", "")
            role = c.get("props", {}).get("role", "")
            name = c.get("label", "")
            lines.append(f"  • {name}" + (f" — {title}" if title else ""))
        lines.append("")
    
    # ─── 5. 在途项目 ───
    projects = [n for n in nodes if n.get("type") == "Project"]
    if projects:
        lines.append(f"📌 在途项目 ({len(projects)}个)")
        for p in projects:
            p_name = p.get("label", "")
            p_props = p.get("props", {})
            stage = p_props.get("stage", "unknown")
            stage_emoji = _stage_emoji(stage)
            lines.append(f"  {stage_emoji} {p_name}" + (f" [{stage}]" if stage else ""))
        lines.append("")
    
    # ─── 6. 竞品渗透 ───
    competitors = [n for n in nodes if n.get("type") == "Competitor"]
    if competitors:
        lines.append(f"⚠️ 竞品动态 ({len(competitors)}家)")
        for comp in competitors:
            comp_name = comp.get("label", "")
            # 找出与该竞品的关系
            comp_edges = [e for e in edges if e.get("dst") == comp_name and e.get("type") == "COMPETES_WITH"]
            related = ", ".join(e["src"] for e in comp_edges[:3]) if comp_edges else ""
            lines.append(f"  🔰 {comp_name}" + (f" — 涉及: {related}" if related else ""))
        lines.append("")
    
    # ─── 7. 最近观察（向量层） ───
    if recent_obs:
        lines.append("📝 最近动态")
        for obs in recent_obs[:3]:
            ts = obs.get("timestamp", "")[:10]
            summary = obs.get("summary", obs.get("raw_content", "")[:60])
            lines.append(f"  • {ts}: {summary}")
        lines.append("")
    
    # ─── 8. 推荐动作 ───
    if include_recommendations:
        recs = recommend_next_visit(kg, hospital_label, top_k=3)
        if recs:
            lines.append("💡 关联客户推荐")
            for r in recs:
                lines.append(f"  • {r['label']} ({r['region']}) — 共享{', '.join(r['shared_entities'][:2])}")
            lines.append("")
    
    # ─── 9. 建议下一步 ───
    next_actions = _generate_next_actions(projects, competitors, recent_obs)
    if next_actions:
        lines.append("🎯 建议下一步")
        for act in next_actions:
            lines.append(f"  ➤ {act}")
        lines.append("")
    
    lines.append("━" * 40)
    
    return "\n".join(lines)


def _stage_emoji(stage: str) -> str:
    """阶段表情符号"""
    mapping = {
        "prospect": "🔍",
        "demo": "🧪",
        "trial": "⚗️",
        "quote": "💰",
        "negotiation": "🤝",
        "won": "✅",
        "lost": "❌",
    }
    return mapping.get(stage, "📋")


def _get_recent_obs_for_hospital(hospital_label: str, hours: int = 168) -> List[Dict]:
    """获取某医院最近168小时(7天)的观察记录"""
    obs = get_recent_observations(hours=hours)
    # 筛选包含该医院名称的观察
    filtered = []
    for o in obs:
        content = o.get("raw_content", "") + " " + o.get("summary", "")
        if hospital_label in content:
            filtered.append(o)
    return filtered


def _generate_fallback_card(hospital_label: str) -> str:
    """图谱中无数据时的降级卡片"""
    lines = [
        f"📊 客户背景卡：{hospital_label}",
        "━" * 40,
        "",
        "⚠️ 知识图谱中暂无该客户数据。",
        "",
        "💡 建议：",
        f"  ➤ 在对话中提及「{hospital_label}」的拜访记录、项目进展或联系人信息，",
        "     系统将自动构建该客户的关系网络。",
        "",
        "━" * 40,
    ]
    return "\n".join(lines)


def _generate_next_actions(projects: List[Dict], competitors: List[Dict], recent_obs: List[Dict]) -> List[str]:
    """基于项目状态和竞品动态生成建议动作"""
    actions = []
    
    # 分析项目阶段
    for p in projects:
        stage = p.get("props", {}).get("stage", "")
        p_name = p.get("label", "")
        if stage == "prospect":
            actions.append(f"[{p_name}] 处于意向阶段，建议安排科室访谈确认需求")
        elif stage == "quote":
            actions.append(f"[{p_name}] 已进入报价/招标阶段，密切关注招标文件技术参数")
        elif stage == "demo":
            actions.append(f"[{p_name}] 方案演示中，跟进试用反馈")
    
    # 竞品告警
    if competitors:
        comp_names = [c.get("label") for c in competitors]
        actions.append(f"⚠️ {', '.join(comp_names)} 已渗透该院，需评估竞争优势并制定应对策略")
    
    # 无近期动态提醒
    if not recent_obs and projects:
        actions.append("该院超过7天无更新记录，建议安排回访确认项目状态")
    
    return actions


# ========== 快捷函数：注入晨报 ==========

def inject_into_digest(digest_text: str) -> str:
    """
    在晨报文本中检测医院名称，自动注入对应的客户背景卡
    
    规则：如果 digest 中提到了某个医院名称，且该医院在图谱中有数据，
         则在 digest 末尾追加该医院的背景卡
    """
    sys.path.insert(0, os.path.expanduser("~/.openclaw/skills/sales-memory-engine/src"))
    from extractor import HOSPITAL_NAMES
    
    # 检测 digest 中出现的医院
    mentioned_hospitals = []
    for h_name in HOSPITAL_NAMES:
        if h_name in digest_text:
            mentioned_hospitals.append(h_name)
    
    if not mentioned_hospitals:
        return digest_text
    
    # 去重并生成卡片
    cards = []
    for h in set(mentioned_hospitals):
        card = generate_customer_card(h, include_recommendations=False)
        if "暂无该客户数据" not in card:  # 过滤空数据
            cards.append(card)
    
    if not cards:
        return digest_text
    
    # 注入到 digest 末尾
    separator = "\n\n" + "━" * 40 + "\n📊 今日提及客户背景卡\n" + "━" * 40 + "\n"
    return digest_text + separator + "\n\n".join(cards)


# ========== 快捷函数：会话启动上下文 ==========

def prepare_graph_context(user_input: str) -> str:
    """
    检测用户输入中的医院名称，生成背景卡作为会话上下文
    
    返回: 空字符串（无需注入）或背景卡文本
    """
    sys.path.insert(0, os.path.expanduser("~/.openclaw/skills/sales-memory-engine/src"))
    from extractor import extract_entities
    
    extracted = extract_entities(user_input)
    customers = extracted.get("customers", [])
    
    if not customers:
        return ""
    
    cards = []
    for c in customers:
        h_name = c.get("name", "")
        if h_name:
            card = generate_customer_card(h_name, include_recommendations=True)
            if "暂无该客户数据" not in card:
                cards.append(card)
    
    if not cards:
        return ""
    
    header = "\n[图谱背景卡]\n"
    footer = "[/图谱背景卡]\n"
    return header + "\n".join(cards) + footer


# ========== CLI 测试 ==========
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="客户背景卡生成器")
    parser.add_argument("hospital", help="医院名称")
    parser.add_argument("--no-recs", action="store_true", help="不输出推荐客户")
    
    args = parser.parse_args()
    
    print(generate_customer_card(args.hospital, include_recommendations=not args.no_recs))
