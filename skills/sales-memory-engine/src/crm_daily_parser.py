#!/usr/bin/env python3
"""
CRM日报解析器 — 从销售易日报文本中提取明日拜访计划

支持格式:
1. 销售易CRM标准日报格式
2. 用户自定义文本格式
3. 钉钉/微信消息格式

输出: [{"hospital": "", "contact": "", "purpose": "", "time": ""}]
"""

import re
from typing import List, Dict, Optional
from datetime import datetime, timedelta

# ─── 医院名称词典（用于匹配） ───
KNOWN_HOSPITALS = [
    "徐州市儿童医院", "徐州医科大学附属医院", "矿务集团总医院", "徐州市中心医院",
    "邳州市人民医院", "泗阳人民医院", "泗洪县人民医院", "沭阳县人民医院",
    "宿迁市第一人民医院", "南京鼓楼医院集团宿迁医院", "盱眙县中医院",
    "淮安市第一人民医院", "淮安市第二人民医院", "涟水县人民医院", "金湖县人民医院",
]

# ─── 解析函数 ───

def parse_daily_report(text: str) -> Dict[str, any]:
    """
    解析完整日报文本
    
    Returns:
        {
            "today_work": [...],
            "tomorrow_plan": [{"hospital": "", "contact": "", "purpose": "", "time": ""}],
            "crm_updates": [...],
            "raw": text
        }
    """
    result = {
        "today_work": [],
        "tomorrow_plan": [],
        "crm_updates": [],
        "raw": text,
    }
    
    # 提取"明日计划" / "明天计划" / "次日计划"部分
    tomorrow_section = _extract_section(text, [
        "明日计划", "明天计划", "次日计划", "明日工作", "明天工作",
        "明日拜访", "明天拜访", "下一步计划", "后续计划"
    ])
    
    if tomorrow_section:
        result["tomorrow_plan"] = _parse_visit_plans(tomorrow_section)
    
    # 提取"今日工作"部分
    today_section = _extract_section(text, [
        "今日工作", "今天工作", "今日拜访", "今天拜访", "本日工作", "当日工作"
    ])
    if today_section:
        result["today_work"] = _parse_today_work(today_section)
    
    # 提取CRM更新部分
    crm_section = _extract_section(text, [
        "CRM更新", "系统更新", "数据录入", "CRM同步"
    ])
    if crm_section:
        result["crm_updates"] = _parse_crm_updates(crm_section)
    
    return result


def _extract_section(text: str, headers: List[str]) -> Optional[str]:
    """从文本中提取以某个标题开头的段落"""
    # 尝试匹配 "## 明日计划" 或 "明日计划：" 或 "明日计划\n"
    for header in headers:
        # 模式1: Markdown标题
        pattern1 = rf"#{1,3}\s*{header}.*?(?=\n#{1,3}\s|\n[A-Z]|$)"
        match = re.search(pattern1, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(0).strip()
        
        # 模式2: 中文冒号
        pattern2 = rf"{header}[：:\s]*\n(.*?)(?=\n\s*(?:{'|'.join(headers)}|今日|本周|备注)|$)"
        match = re.search(pattern2, text, re.DOTALL | re.IGNORECASE)
        if match:
            return header + "\n" + match.group(1).strip()
    
    return None


def _parse_visit_plans(section_text: str) -> List[Dict]:
    """解析拜访计划列表"""
    plans = []
    
    # 按行分割，识别每条计划
    lines = section_text.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('*') and len(line) < 3:
            continue
        
        plan = _parse_single_plan(line)
        if plan and plan.get("hospital"):
            plans.append(plan)
    
    return plans


def _parse_single_plan(line: str) -> Optional[Dict]:
    """解析单条拜访计划"""
    # 去除列表标记
    line = re.sub(r"^[-*•・\d+\.\s]+", "", line).strip()
    if len(line) < 5:
        return None
    
    plan = {"hospital": "", "contact": "", "purpose": "", "time": "", "raw": line}
    
    # 1. 提取医院名称
    for hosp in KNOWN_HOSPITALS:
        if hosp in line or hosp.replace("医院", "").replace("县", "").replace("市", "") in line:
            plan["hospital"] = hosp
            break
    
    # 如果没匹配到已知医院，尝试正则提取
    if not plan["hospital"]:
        hosp_match = re.search(r"([\u4e00-\u9fa5]{2,8}(?:医院|卫生院|中心|诊所))", line)
        if hosp_match:
            plan["hospital"] = hosp_match.group(1)
    
    # 2. 提取联系人（X主任/院长/科长/经理）
    contact_match = re.search(r"([\u4e00-\u9fa5]{1,4}(?:主任|院长|科长|经理|老师|医生))", line)
    if contact_match:
        plan["contact"] = contact_match.group(1)
    
    # 3. 提取拜访目的（推进/跟进/拜访/维护/演示/送样/报价/签约）
    purpose_keywords = [
        "推进", "跟进", "拜访", "维护", "客情", "演示", "送样", "报价",
        "签约", "合同", "催款", "装机", "培训", "验收", "回款",
        "细胞因子", "淋巴细胞", "亚群", "Treg", "TH1", "TH2", "HLA",
        "阿尔茨海默", "AD", "Aβ", "流式", "设备", "试剂", "项目"
    ]
    
    purposes = []
    for kw in purpose_keywords:
        if kw in line:
            purposes.append(kw)
    plan["purpose"] = "、".join(purposes[:3]) if purposes else ""
    
    # 4. 提取时间（明天/上午/下午/上午9点/周二等）
    time_match = re.search(r"(明天|上午|下午|早上|晚上|(\d{1,2})[点:：](\d{0,2})?|周[一二三四五六日])", line)
    if time_match:
        plan["time"] = time_match.group(1)
    
    return plan


def _parse_today_work(section_text: str) -> List[Dict]:
    """解析今日工作记录"""
    works = []
    lines = section_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        line = re.sub(r"^[-*•・\d+\.\s]+", "", line).strip()
        if len(line) > 5:
            works.append(line)
    return works


def _parse_crm_updates(section_text: str) -> List[str]:
    """解析CRM更新项"""
    updates = []
    lines = section_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        line = re.sub(r"^[-*•・\d+\.\s\[\]x✓✅]+", "", line).strip()
        if len(line) > 3:
            updates.append(line)
    return updates


# ─── 自动生成明日拜访简报 ───

def generate_tomorrow_visit_briefs(daily_report_text: str) -> List[Dict]:
    """
    从日报文本自动生成所有明日拜访的简报
    
    Returns:
        [{"hospital": "", "brief": "markdown", "plan": {...}}]
    """
    import sys, os
    sys.path.insert(0, os.path.expanduser("~/.openclaw/skills/sales-memory-engine/src"))
    from visit_briefing import generate_visit_brief
    
    parsed = parse_daily_report(daily_report_text)
    plans = parsed.get("tomorrow_plan", [])
    
    results = []
    for plan in plans:
        hosp = plan.get("hospital")
        if not hosp:
            continue
        
        try:
            brief = generate_visit_brief(
                hospital_name=hosp,
                contact_name=plan.get("contact"),
                visit_purpose=plan.get("purpose")
            )
            results.append({
                "hospital": hosp,
                "brief": brief,
                "plan": plan,
            })
        except Exception as e:
            results.append({
                "hospital": hosp,
                "brief": f"生成失败: {e}",
                "plan": plan,
            })
    
    return results


# ─── CLI ───

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CRM日报解析")
    parser.add_argument("--file", "-f", help="日报文件路径")
    parser.add_argument("--text", "-t", help="日报文本内容")
    parser.add_argument("--output", "-o", help="输出简报文件路径")
    args = parser.parse_args()
    
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    elif args.text:
        text = args.text
    else:
        # 示例
        text = """
# 工作日报 — 2026-05-12 · 张智

## 今日工作
1. 【拜访】徐州市儿童医院 — 检验科方主任 — TH1/TH2需求单推进，细胞因子调整为12项
2. 【跟进】盱眙县中医院 — 张主任 — 计划审批中，等待回复
3. 【内部】参加公司月度会议，汇报Q1业绩

## 明日计划
1. 【拜访】淮安市第一人民医院 — 王颖主任 — 细胞因子项目推进，应对瑞斯凯尔竞争
2. 【跟进】矿务集团总医院 — 采购科 — 确认挂网进度

## CRM更新
- [x] 徐州儿童医院拜访记录
- [ ] 盱眙县中医院活动记录待补
        """
    
    print("=== 解析结果 ===")
    parsed = parse_daily_report(text)
    
    print(f"\n📋 今日工作 ({len(parsed['today_work'])}条):")
    for w in parsed['today_work']:
        print(f"  • {w}")
    
    print(f"\n📅 明日拜访计划 ({len(parsed['tomorrow_plan'])}条):")
    for p in parsed['tomorrow_plan']:
        print(f"  • {p['hospital']} | {p['contact']} | {p['purpose']} | {p['time']}")
    
    if parsed['tomorrow_plan']:
        print("\n=== 自动生成拜访简报 ===")
        briefs = generate_tomorrow_visit_briefs(text)
        for b in briefs:
            print(f"\n{'='*50}")
            print(f"🏥 {b['hospital']}")
            print(f"{'='*50}")
            print(b['brief'][:800] + "...\n(truncated)")
            
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                for b in briefs:
                    f.write(f"# {b['hospital']}\n\n")
                    f.write(b['brief'])
                    f.write("\n\n---\n\n")
            print(f"\n✅ 简报已保存: {args.output}")
