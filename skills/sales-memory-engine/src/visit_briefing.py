#!/usr/bin/env python3
"""
销售记忆引擎 — 拜访建议生成系统 (T4.14)

核心功能:
1. 从CRM日报提取次日拜访计划（医院名、联系人、目的）
2. 从记忆引擎拉取该客户历史记录
3. 关联竞品动态、政策变化、项目进展
4. 生成结构化拜访建议：
   - 上次聊到哪了（上次拜访要点回顾）
   - 客户当前痛点（从记录中提取的关键诉求）
   - 竞品威胁（竞品最近动态、中标情况）
   - 建议带什么材料（根据项目阶段匹配资料）
   - 推进策略建议（下一步动作、话术建议）

输入: 医院名称（或CRM日报JSON）
输出: Markdown格式的拜访建议简报
"""

import os
import sys
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from pathlib import Path

# 添加技能路径
sys.path.insert(0, os.path.expanduser("~/.openclaw/skills/sales-memory-engine/src"))

from database import get_conn, init_db
from search import hybrid_search_v2, keyword_search
from memory_engine import get_customer_profile, get_competitor_timeline


# ─── 拜访建议模板 ───

VISIT_BRIEF_TEMPLATE = """# 🏥 {hospital_name} — 拜访前简报
> 生成时间: {generated_at} | 数据源: 销售记忆引擎

---

## 📌 上次聊到哪了

{last_visit_summary}

---

## 🎯 客户当前痛点

{pain_points}

---

## ⚠️ 竞品威胁

{competitor_threats}

---

## 📚 建议携带材料

{recommended_materials}

---

## 🚀 推进策略建议

{strategy_suggestions}

---

## 📊 客户健康度仪表盘

{health_dashboard}

---

*简报由销售记忆引擎自动生成，数据截止至 {data_cutoff}*
"""


# ─── 核心函数 ───

def generate_visit_brief(hospital_name: str, contact_name: Optional[str] = None,
                         visit_purpose: Optional[str] = None) -> str:
    """
    生成拜访前简报
    
    Args:
        hospital_name: 医院名称（如"盱眙县中医院"）
        contact_name: 联系人姓名（可选）
        visit_purpose: 拜访目的（可选）
    
    Returns:
        Markdown格式的拜访建议简报
    """
    init_db()
    
    # 1. 获取客户画像
    profile = get_customer_profile_by_name(hospital_name)
    
    # 2. 检索历史记录（最近30天）
    recent_records = search_hospital_records(hospital_name, days=60)
    
    # 3. 竞品动态（最近30天）
    competitor_signals = get_recent_competitor_signals(hospital_name, days=30)
    
    # 4. 生成各部分摘要
    last_visit = _summarize_last_visit(recent_records)
    pain_points = _extract_pain_points(recent_records)
    threats = _summarize_competitor_threats(competitor_signals, recent_records)
    materials = _recommend_materials(recent_records, visit_purpose)
    strategy = _generate_strategy(recent_records, profile, visit_purpose)
    health = _generate_health_dashboard(profile, recent_records)
    
    # 5. 组装简报
    brief = VISIT_BRIEF_TEMPLATE.format(
        hospital_name=hospital_name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        last_visit_summary=last_visit,
        pain_points=pain_points,
        competitor_threats=threats,
        recommended_materials=materials,
        strategy_suggestions=strategy,
        health_dashboard=health,
        data_cutoff=datetime.now().strftime("%Y-%m-%d"),
    )
    
    # 6. 保存到文件
    output_path = f"/tmp/visit_brief_{hospital_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(brief)
    
    print(f"[VISIT_BRIEF] 简报已生成: {output_path}")
    return brief


def get_customer_profile_by_name(hospital_name: str) -> Optional[Dict]:
    """通过医院名称获取客户画像"""
    conn = get_conn()
    
    # 尝试精确匹配
    row = conn.execute(
        "SELECT * FROM customer_profiles WHERE name = ?",
        (hospital_name,)
    ).fetchone()
    
    if not row:
        # 模糊匹配
        row = conn.execute(
            "SELECT * FROM customer_profiles WHERE name LIKE ?",
            (f"%{hospital_name}%",)
        ).fetchone()
    
    conn.close()
    
    if row:
        return dict(row)
    return None


def search_hospital_records(hospital_name: str, days: int = 30) -> List[Dict]:
    """检索某医院的历史记录"""
    # 使用结构化搜索：customer过滤
    results = hybrid_search_v2(
        query="拜访 项目 进展",
        customer=hospital_name,
        date_after=(datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d"),
        top_k=10,
    )
    
    # 如果没有结构化匹配结果，fallback到关键词
    if not results:
        results = keyword_search(hospital_name, limit=10)
    
    return results


def get_recent_competitor_signals(hospital_name: str, days: int = 30) -> List[Dict]:
    """获取该医院相关的竞品动态"""
    conn = get_conn()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    
    rows = conn.execute(
        """SELECT * FROM competitor_events 
           WHERE (hospital = ? OR description LIKE ?) 
           AND event_date > ?
           ORDER BY event_date DESC""",
        (hospital_name, f"%{hospital_name}%", since)
    ).fetchall()
    
    conn.close()
    return [dict(r) for r in rows]


# ─── 摘要生成函数 ───

def _summarize_last_visit(records: List[Dict]) -> str:
    """总结上次拜访要点"""
    if not records:
        return "⚠️ 未找到该客户的历史拜访记录。建议首次拜访时重点了解科室现状和项目需求。"
    
    # 按时间排序，取最新的一条
    records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    latest = records[0]
    
    lines = [f"**最近记录** ({latest.get('timestamp', '未知时间')})"]
    content = latest.get("content", "")[:300]
    lines.append(f"> {content}...")
    
    # 提取关键动作
    extracted = latest.get("extracted", {})
    action_items = extracted.get("action_items", [])
    if action_items:
        lines.append(f"\n**上次约定的动作**:")
        for action in action_items[:3]:
            lines.append(f"- {action}")
    
    # 显示还有多少条历史记录
    if len(records) > 1:
        lines.append(f"\n*该客户共有 {len(records)} 条历史记录*")
    
    return "\n".join(lines)


def _extract_pain_points(records: List[Dict]) -> str:
    """提取客户痛点"""
    if not records:
        return "⚠️ 暂无足够数据。建议本次拜访重点挖掘：预算、决策链、竞品使用情况、项目时间表。"
    
    pain_keywords = [
        "价格", "预算", "贵", "高", "钱", "费用", "成本", "报销",
        "慢", "周期", "等", "拖延", "卡", "审批", "困难",
        "问题", "bug", "故障", "不好用", "不满意", "投诉",
        "竞品", "对手", "别家", "已经买了", "已中标",
    ]
    
    pain_points = []
    for rec in records:
        content = rec.get("content", "")
        for kw in pain_keywords:
            if kw in content:
                # 提取包含关键词的句子
                sentences = content.split("。")
                for s in sentences:
                    if kw in s and len(s) > 10:
                        pain_points.append(s.strip() + "。")
                        break
                break  # 每条记录只取一个痛点
    
    if pain_points:
        # 去重并取前5个
        unique_pains = list(dict.fromkeys(pain_points))[:5]
        return "\n".join([f"{i+1}. {p}" for i, p in enumerate(unique_pains)])
    
    return "暂无明确痛点记录。建议本次拜访通过开放式问题挖掘：\n1. 目前检测项目的主要困扰是什么？\n2. 对现有供应商/设备有哪些不满意？\n3. 预算和审批流程是否顺畅？"


def _summarize_competitor_threats(signals: List[Dict], records: List[Dict]) -> str:
    """总结竞品威胁"""
    threats = []
    
    # 从竞品事件表提取
    for sig in signals[:3]:
        comp = sig.get("competitor_name", "未知竞品")
        event = sig.get("event_type", "动态")
        desc = sig.get("description", "")[:100]
        threats.append(f"- **{comp}** | {event}: {desc}...")
    
    # 从历史记录中提取竞品提及
    competitor_mentions = []
    for rec in records:
        extracted = rec.get("extracted", {})
        comps = extracted.get("competitors", [])
        for c in comps:
            if isinstance(c, dict):
                name = c.get("name", "")
            else:
                name = str(c)
            if name and name not in [t for t in competitor_mentions]:
                competitor_mentions.append(name)
    
    if competitor_mentions:
        threats.append(f"\n**客户提及过的竞品**: {', '.join(competitor_mentions[:5])}")
    
    if threats:
        return "\n".join(threats)
    
    return "✅ 近期未发现竞品威胁。建议保持警惕，定期关注该区域招标动态。"


def _recommend_materials(records: List[Dict], visit_purpose: Optional[str]) -> str:
    """推荐携带材料"""
    materials = []
    
    # 基础材料（每次必带）
    materials.append("- 📄 产品彩页（DxFLEX + 试剂盒组合）")
    materials.append("- 💰 最新报价单（含医保收费代码对照）")
    
    # 根据拜访目的匹配
    if visit_purpose:
        purpose_lower = visit_purpose.lower()
        if "医保" in purpose_lower or "收费" in purpose_lower:
            materials.append("- 📋 医保立项指南（检验类收费代码解读）")
            materials.append("- 📊 江苏省检验项目收费标准对照表")
        if "细胞因子" in purpose_lower or "淋巴" in purpose_lower:
            materials.append("- 🔬 细胞因子检测临床价值文献（3-5篇精选）")
            materials.append("- 📈 同类医院装机案例（徐州/宿迁区域优先）")
        if "阿尔茨海默" in purpose_lower or "AD" in purpose_lower:
            materials.append("- 🧠 AD三项检测（Aβ40/Aβ42/pT181）临床意义白皮书")
            materials.append("- 📑 国家医保局立项指南相关条款")
    
    # 根据历史记录中的项目匹配
    for rec in records[:3]:
        extracted = rec.get("extracted", {})
        projects = extracted.get("projects", [])
        for p in projects:
            pname = p.get("name", "") if isinstance(p, dict) else str(p)
            if "细胞因子" in pname and "🔬 细胞因子检测临床价值文献" not in " ".join(materials):
                materials.append("- 🔬 细胞因子检测临床价值文献（3-5篇精选）")
            if "Treg" in pname or "亚群" in pname and "🔬 淋巴细胞亚群检测方案" not in " ".join(materials):
                materials.append("- 🔬 淋巴细胞亚群检测方案彩页")
    
    # 如果有竞品威胁，加对比材料
    if any("竞品" in r.get("content", "") for r in records[:3]):
        materials.append("- ⚖️ 竞品对比表（贝克曼 vs 瑞斯凯尔 vs BD 关键参数）")
    
    materials.append("- 🎁 小礼品（根据科室偏好，如记事本/笔/咖啡券）")
    
    return "\n".join(materials)


def _generate_strategy(records: List[Dict], profile: Optional[Dict],
                       visit_purpose: Optional[str]) -> str:
    """生成推进策略建议"""
    strategies = []
    
    # 根据是否有历史记录
    if not records:
        strategies.append("**首次拜访策略**:")
        strategies.append("1. **建立关系**: 自我介绍+公司背景（赛基/德普代理贝克曼/层浪）")
        strategies.append("2. **需求挖掘**: 了解科室现有设备品牌、检测项目开展情况")
        strategies.append("3. **痛点确认**: 现有设备/试剂的使用体验、是否有新增项目计划")
        strategies.append("4. **下一步**: 争取参观实验室机会，收集现有设备型号信息")
        return "\n".join(strategies)
    
    # 有历史记录，分析项目阶段
    latest = records[0]
    extracted = latest.get("extracted", {})
    projects = extracted.get("projects", [])
    action_items = extracted.get("action_items", [])
    
    if projects:
        strategies.append(f"**项目推进策略** (当前阶段: {projects[0].get('stage', '待确认') if isinstance(projects[0], dict) else '待确认'})")
        strategies.append("1. **跟进上次约定**: 确认上次承诺的材料/样品是否已收到")
        strategies.append("2. **深化需求**: 了解项目预算审批进度、决策链是否清晰")
        strategies.append("3. **排除竞品**: 如竞品已介入，强调我方差异化优势（服务响应/技术支持/本地案例）")
        strategies.append("4. **推进动作**: 争取样品测试机会，或安排技术交流会")
    else:
        strategies.append("**常规拜访策略**:")
        strategies.append("1. **关系维护**: 回顾上次交流要点，展示持续关注的诚意")
        strategies.append("2. **信息更新**: 分享该区域同类医院的最新进展（匿名化处理）")
        strategies.append("3. **新需求挖掘**: 询问是否有新增检测项目计划（AD三项、Treg等）")
        strategies.append("4. **下一步**: 确认下次拜访时间，或约定电话/微信沟通节点")
    
    # 根据拜访目的细化
    if visit_purpose:
        strategies.append(f"\n**本次专项目的**: {visit_purpose}")
        if "送样" in visit_purpose:
            strategies.append("- 💡 提前确认样品类型、数量、接收人、实验室准备条件")
        if "报价" in visit_purpose or "价格" in visit_purpose:
            strategies.append("- 💡 准备多套报价方案（高端/基础），预留谈判空间")
        if "合同" in visit_purpose or "签约" in visit_purpose:
            strategies.append("- 💡 确认合同条款细节（付款方式、交货期、售后服务），带法务审核版")
    
    return "\n".join(strategies)


def _generate_health_dashboard(profile: Optional[Dict], records: List[Dict]) -> str:
    """生成客户健康度仪表盘"""
    lines = []
    
    # 基础信息
    if profile:
        lines.append(f"- 🏥 **医院等级**: {profile.get('level', '未知')} | **区域**: {profile.get('region', '未知')}")
        lines.append(f"- 📞 **拜访次数**: {profile.get('visit_count', 0)} | **最近更新**: {profile.get('last_updated', '未知')}")
    
    # 记录统计
    if records:
        latest_ts = records[0].get("timestamp", "")
        earliest_ts = records[-1].get("timestamp", "")
        lines.append(f"- 📊 **历史记录**: {len(records)} 条 | **最早**: {earliest_ts[:10] if earliest_ts else '未知'} | **最新**: {latest_ts[:10] if latest_ts else '未知'}")
        
        # 计算平均重要性
        avg_importance = sum(r.get("importance", 0) or 0 for r in records) / len(records)
        lines.append(f"- ⭐ **平均重要性**: {avg_importance:.1f}/5.0")
    else:
        lines.append("- 📊 **历史记录**: 暂无")
    
    # 健康度评分
    health_score = 50  # 基础分
    if profile and profile.get("visit_count", 0) >= 3:
        health_score += 15
    if records and len(records) >= 5:
        health_score += 15
    if any(r.get("importance", 0) >= 4 for r in records):
        health_score += 20
    
    if health_score >= 80:
        status = "🟢 健康"
    elif health_score >= 60:
        status = "🟡 关注"
    else:
        status = "🔴 需激活"
    
    lines.append(f"- 🏥 **客户健康度**: {health_score}/100 ({status})")
    
    return "\n".join(lines)


# ─── CLI 入口 ───

def main():
    import argparse
    parser = argparse.ArgumentParser(description="生成拜访前简报")
    parser.add_argument("hospital", help="医院名称，如'盱眙县中医院'")
    parser.add_argument("--contact", "-c", help="联系人姓名")
    parser.add_argument("--purpose", "-p", help="拜访目的")
    parser.add_argument("--output", "-o", help="输出文件路径")
    args = parser.parse_args()
    
    brief = generate_visit_brief(args.hospital, args.contact, args.purpose)
    
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(brief)
        print(f"简报已保存: {args.output}")
    else:
        print(brief)


if __name__ == "__main__":
    main()
