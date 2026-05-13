#!/usr/bin/env python3
"""
销售记忆引擎 - 实体提取模块
从销售对话中提取结构化信息
"""

import re
import json
from typing import Dict, List, Any, Optional
from datetime import datetime

# ========== 医院/客户词典 ==========
HOSPITAL_PATTERNS = [
    # 徐州
    ("徐州市儿童医院", "徐州", "三甲", "检验科"),
    ("徐州医科大学附属医院", "徐州", "三甲", "检验科/血液科"),
    ("矿务集团总医院", "徐州", "三甲", "检验科"),
    ("徐州市中心医院", "徐州", "三甲", "检验科"),
    ("邳州市人民医院", "徐州", "二甲", "检验科"),
    # 宿迁
    ("泗阳人民医院", "宿迁", "二甲", "检验科"),
    ("泗洪县人民医院", "宿迁", "二甲", "检验科"),
    ("沭阳县人民医院", "宿迁", "二甲", "检验科"),
    ("宿迁市第一人民医院", "宿迁", "三甲", "检验科"),
    ("南京鼓楼医院集团宿迁医院", "宿迁", "三甲", "检验科"),
    # 淮安
    ("淮安市第一人民医院", "淮安", "三甲", "检验科/血液科"),
    ("盱眙县中医院", "淮安", "二甲", "检验科"),
    ("盱眙县人民医院", "淮安", "二甲", "检验科"),
    ("涟水县人民医院", "淮安", "二甲", "检验科"),
    ("洪泽区人民医院", "淮安", "二甲", "检验科"),
]

# 构建正则
HOSPITAL_NAMES = [h[0] for h in HOSPITAL_PATTERNS]
HOSPITAL_RE = re.compile(
    "(" + "|".join(re.escape(h) for h in HOSPITAL_NAMES) + ")"
)

# ========== 竞品词典 ==========
COMPETITOR_PATTERNS = {
    "瑞斯凯尔": ["瑞斯凯尔", "RaiSecare", "raisecare"],
    "贝克曼": ["贝克曼", "Beckman", "DxFLEX", "CytoFLEX"],
    "BD": ["BD", "Becton Dickinson", "FACSCanto", "FACSAria"],
    "安捷伦": ["安捷伦", "Agilent", "NovoCyte"],
    "层浪": ["层浪", "Longlight"],
    "迈瑞": ["迈瑞", "Mindray", "BriCyte"],
    "优利特": ["优利特", "URIT"],
    "博奥赛斯": ["博奥赛斯", "Bioscience"],
}

# 扁平化为正则
_COMPETITOR_FLAT = []
for brand, aliases in COMPETITOR_PATTERNS.items():
    for alias in aliases:
        _COMPETITOR_FLAT.append((brand, alias))
COMPETITOR_RE = re.compile(
    "(" + "|".join(re.escape(alias) for _, alias in _COMPETITOR_FLAT) + ")",
    re.IGNORECASE
)

# ========== 项目/检测词典 ==========
PROJECT_PATTERNS = {
    "细胞因子": ["细胞因子", "白介素", "IL-", "干扰素", "IFN", "TNF", "肿瘤坏死因子"],
    "淋巴细胞亚群": ["淋巴细胞亚群", "TBNK", "T细胞", "B细胞", "NK细胞", "CD3", "CD4", "CD8"],
    "HLA-B27": ["HLA-B27", "B27", "强直性脊柱炎"],
    "阿尔茨海默症": ["阿尔茨海默", "AD三项", "Aβ40", "Aβ42", "p-tau181", "pT181", "淀粉样蛋白"],
    "可溶性炎症因子": ["可溶性炎症因子", "sCD25", "sCD40L"],
    "绝对计数": ["绝对计数", "绝对计术", "微球"],
    "PD-1": ["PD-1", "PD1", "PD-L1", "免疫检查点"],
    "IgG亚类": ["IgG亚类", "免疫球蛋白G亚类"],
}

# ========== 收费代码词典 ==========
CHARGE_CODE_RE = re.compile(r"250\d{5,6}[\-a-zA-Z]*")

# ========== 决策阶段词典 ==========
STAGE_PATTERNS = {
    "prospect": ["有意向", "想了解", "咨询", "关注"],
    "demo": ["演示", "试用", "样机", "装机", "体验"],
    "trial": ["临床验证", "比对", "评估", "测试"],
    "quote": ["报价", "预算", "招标", "投标", "挂网", "计划审批"],
    "negotiation": ["谈判", "议价", "合同", "签约"],
    "won": ["中标", "签单", "下单", "成交", "达成合作"],
    "lost": ["流标", "选别家", "被竞品拿下", "暂停", "搁置"],
}

# ========== 关键动作/待办 ==========
ACTION_PATTERNS = [
    ("待提交", r"(?:需要|待|等|准备).{0,5}提交|需求单|申请"),
    ("待审批", r"(?:等|待).{0,5}审批|批复|签字|上会"),
    ("待挂网", r"(?:等|待).{0,5}挂网|公示|采购"),
    ("待跟进", r"(?:下周|下次|过几天|15天|一周后).{0,5}跟进|联系|拜访"),
    ("已下单", r"已下单|已签单|已采购|已中标"),
    ("领导更替", r"领导.{0,5}更替|换人|调整|调动|退休"),
]

# ========== 隐私过滤 ==========
PRIVACY_PATTERNS = [
    (r"\b1[3-9]\d{9}\b", lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:]),  # 手机号
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "***@***.***"),  # 邮箱
    (r"sk-[a-zA-Z0-9]{20,}", "***API-KEY***"),  # API Key
    (r"\b\d{17}[\dXx]\b", "***身份证号***"),  # 身份证
]

def privacy_filter(text: str) -> str:
    """过滤敏感信息"""
    for pattern, replacement in PRIVACY_PATTERNS:
        if callable(replacement):
            text = re.sub(pattern, replacement, text)
        else:
            text = re.sub(pattern, replacement, text)
    return text

# ========== 主提取函数 ==========

def extract_entities(text: str, session_id: str = "") -> Dict[str, Any]:
    """
    从销售对话中提取所有结构化实体
    
    Returns:
        {
            "customers": [...],
            "contacts": [...],
            "competitors": [...],
            "projects": [...],
            "charge_codes": [...],
            "actions": [...],
            "summary": "..."
        }
    """
    result = {
        "session_id": session_id,
        "timestamp": datetime.now().isoformat(),
        "customers": [],
        "contacts": [],
        "competitors": [],
        "projects": [],
        "charge_codes": [],
        "actions": [],
        "summary": "",
    }
    
    # 1. 提取医院/客户
    for match in HOSPITAL_RE.finditer(text):
        name = match.group(0)
        for h in HOSPITAL_PATTERNS:
            if h[0] == name:
                result["customers"].append({
                    "name": name,
                    "region": h[1],
                    "level": h[2],
                    "department": h[3],
                })
                break
    
    # 2. 提取联系人（修正版：准确匹配"张主任"等，兼容中文语境）
    contact_re = re.compile(r"(?:^|[，。；,\s])([\u4e00-\u9fa5]{1,3})(主任|院长|科长|技师|医生)")
    contact_re2 = re.compile(r"(?:医院|科室|集团)([\u4e00-\u9fa5]{1,3})(主任|院长|科长|技师|医生)")
    for match in contact_re.finditer(text):
        result["contacts"].append({
            "name": match.group(1) + match.group(2),
            "title": match.group(2),
            "role": "unknown",
        })
    for match in contact_re2.finditer(text):
        result["contacts"].append({
            "name": match.group(1) + match.group(2),
            "title": match.group(2),
            "role": "unknown",
        })
        result["contacts"].append({
            "name": match.group(1) + match.group(2),
            "title": match.group(2),
            "role": "unknown",  # 后续根据上下文推断
        })
    
    # 3. 提取竞品
    seen_competitors = set()
    for match in COMPETITOR_RE.finditer(text):
        matched_text = match.group(0)
        for brand, aliases in COMPETITOR_PATTERNS.items():
            if any(alias.lower() == matched_text.lower() for alias in aliases):
                if brand not in seen_competitors:
                    seen_competitors.add(brand)
                    result["competitors"].append(brand)
                break
    
    # 4. 提取项目/检测
    for project, keywords in PROJECT_PATTERNS.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                result["projects"].append({
                    "name": project,
                    "keyword_matched": kw,
                })
                break
    
    # 5. 提取收费代码
    for match in CHARGE_CODE_RE.finditer(text):
        result["charge_codes"].append(match.group(0))
    
    # 去重联系人（按名字）
    seen_contacts = set()
    unique_contacts = []
    for c in result["contacts"]:
        if c["name"] not in seen_contacts:
            seen_contacts.add(c["name"])
            unique_contacts.append(c)
    result["contacts"] = unique_contacts
    
    # 6. 提取阶段/动作
    for stage_name, keywords in STAGE_PATTERNS.items():
        for kw in keywords:
            if kw in text:
                result["actions"].append({
                    "type": "stage",
                    "stage": stage_name,
                    "keyword": kw,
                })
                break
    
    # 7. 提取待办/关键动作
    for action_name, pattern in ACTION_PATTERNS:
        m = re.search(pattern, text)
        if m:
            result["actions"].append({
                "type": "action",
                "action": action_name,
                "context": text[max(0, m.start()-20):m.end()+20],
            })
    
    # 8. 生成一句话摘要
    result["summary"] = _generate_summary(result, text)
    
    # 9. 标签
    tags = set()
    if result["customers"]:
        tags.add("customer")
    if result["competitors"]:
        tags.add("competitor")
    if result["projects"]:
        tags.add("project")
    if result["charge_codes"]:
        tags.add("policy")
    if result["actions"]:
        tags.add("action")
    result["tags"] = ",".join(tags) if tags else "general"
    
    # 10. 生成知识图谱三元组 (v3)
    result["triples"] = _extract_triples(result, text)
    
    return result

def _generate_summary(result: Dict, text: str) -> str:
    """基于提取结果生成一句话摘要"""
    parts = []
    
    if result["customers"]:
        parts.append("/".join(c["name"] for c in result["customers"]))
    
    if result["contacts"]:
        parts.append("-" + "/".join(c["name"] for c in result["contacts"]))
    
    if result["projects"]:
        parts.append("[" + "/".join(p["name"] for p in result["projects"]) + "]")
    
    if result["actions"]:
        action_descs = []
        for a in result["actions"]:
            if a["type"] == "stage":
                action_descs.append(a["keyword"])
            elif a["type"] == "action":
                action_descs.append(a["action"])
        if action_descs:
            parts.append("(" + "/".join(action_descs) + ")")
    
    if result["competitors"]:
        parts.append("竞品:" + "/".join(result["competitors"]))
    
    return " ".join(parts) if parts else "一般对话"

# ========== 知识图谱三元组提取 (v3) ==========

def _extract_triples(result: Dict, text: str) -> List[Dict]:
    """
    从提取结果生成知识图谱三元组 (head, relation, tail)
    用于直接写入 graph_nodes + graph_edges
    """
    triples = []
    
    # 1. 医院 -> 联系人 (HAS_CONTACT)
    for customer in result.get("customers", []):
        hospital_name = customer["name"]
        for contact in result.get("contacts", []):
            contact_name = contact["name"]
            hospital_idx = text.find(hospital_name)
            contact_base = contact_name.replace("主任", "").replace("院长", "").replace("科长", "")
            contact_idx = text.find(contact_base) if contact_base else -1
            
            if hospital_idx >= 0 and contact_idx >= 0 and abs(hospital_idx - contact_idx) < 100:
                triples.append({
                    "head": {"type": "Hospital", "label": hospital_name, 
                            "props": {k: v for k, v in customer.items() if k != "name"}},
                    "relation": "HAS_CONTACT",
                    "tail": {"type": "Person", "label": contact_name,
                            "props": {k: v for k, v in contact.items() if k != "name"}},
                    "weight": 1.0,
                })
        
        # 2. 医院 -> 项目 (HAS_PROJECT)
        for project in result.get("projects", []):
            project_name = project["name"]
            stage = "prospect"
            for action in result.get("actions", []):
                if action.get("type") == "stage":
                    stage = action.get("stage", "prospect")
                    break
            
            triples.append({
                "head": {"type": "Hospital", "label": hospital_name,
                        "props": {k: v for k, v in customer.items() if k != "name"}},
                "relation": "HAS_PROJECT",
                "tail": {"type": "Project", "label": project_name,
                        "props": {"keyword_matched": project.get("keyword_matched", ""),
                                  "stage": stage}},
                "weight": 1.0,
            })
        
        # 3. 医院 -> 竞品 (COMPETES_WITH)
        for competitor in result.get("competitors", []):
            comp_name = competitor if isinstance(competitor, str) else competitor.get("name", "")
            if comp_name:
                triples.append({
                    "head": {"type": "Hospital", "label": hospital_name,
                            "props": {k: v for k, v in customer.items() if k != "name"}},
                    "relation": "COMPETES_WITH",
                    "tail": {"type": "Competitor", "label": comp_name,
                            "props": {"type": "unknown", "threat_level": 3}},
                    "weight": 1.0,
                })
    
    # 4. 项目 -> 竞品 (COMPETES_WITH)
    for project in result.get("projects", []):
        project_name = project["name"]
        for competitor in result.get("competitors", []):
            comp_name = competitor if isinstance(competitor, str) else competitor.get("name", "")
            if comp_name:
                triples.append({
                    "head": {"type": "Project", "label": project_name,
                            "props": {"keyword_matched": project.get("keyword_matched", "")}},
                    "relation": "COMPETES_WITH",
                    "tail": {"type": "Competitor", "label": comp_name,
                            "props": {"type": "unknown", "threat_level": 3}},
                    "weight": 1.0,
                })
    
    # 5. 收费代码 -> 医院 (POLICY_COVERED)
    for code in result.get("charge_codes", []):
        for customer in result.get("customers", []):
            triples.append({
                "head": {"type": "Hospital", "label": customer["name"],
                        "props": {k: v for k, v in customer.items() if k != "name"}},
                "relation": "POLICY_COVERED",
                "tail": {"type": "Policy", "label": code,
                        "props": {"code": code}},
                "weight": 1.0,
            })
    
    # 去重
    seen = set()
    unique_triples = []
    for t in triples:
        key = (t["head"]["label"], t["relation"], t["tail"]["label"])
        if key not in seen:
            seen.add(key)
            unique_triples.append(t)
    
    return unique_triples


# ========== 测试 (v3 包含三元组) ==========
if __name__ == "__main__":
    test_text = """
    盱眙县中医院张主任那边，细胞因子项目计划审批还在等回复，
    上次说想上六项。瑞斯凯尔也在接触他们，报价比较低。
    收费代码是250401014-a，白介素30元/项。
    下周需要去跟进一下，手机号是17712345678。
    """
    
    test_text = privacy_filter(test_text)
    result = extract_entities(test_text, session_id="test-001")
    print("=== 提取结果 ===")
    print(json.dumps({
        "customers": result["customers"],
        "contacts": result["contacts"],
        "competitors": result["competitors"],
        "projects": result["projects"],
        "actions": result["actions"],
        "summary": result["summary"],
    }, ensure_ascii=False, indent=2))
    
    print("\n=== 知识图谱三元组 ===")
    for t in result["triples"]:
        print(f"  [{t['head']['type']}] {t['head']['label']} --[{t['relation']}]--> [{t['tail']['type']}] {t['tail']['label']}")
    
    print(f"\n三元组总数: {len(result['triples'])}")
