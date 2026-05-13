#!/usr/bin/env python3
"""
销售记忆引擎 v3 — 知识图谱核心模块
NetworkX 内存图 + 查询接口 + 与向量层融合
"""

import sqlite3
import json
import os
import hashlib
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

try:
    import networkx as nx
except ImportError:
    nx = None
    print("[WARN] NetworkX 未安装，图算法将不可用。pip install networkx")

DB_PATH = os.path.expanduser("~/.openclaw/workspace/memory_engine/db/sales_memory.db")


# ========== 内存图管理 ==========

class KnowledgeGraph:
    """基于 NetworkX 的内存知识图谱，启动时从 SQLite 加载"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.graph = nx.MultiDiGraph() if nx else None
        self.node_id_map = {}  # label -> id 快速查找
        self._loaded = False
    
    def load_from_db(self) -> bool:
        """从 SQLite 加载节点和关系到 NetworkX"""
        if not nx or self._loaded:
            return False
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # 加载节点
        nodes = conn.execute("SELECT * FROM graph_nodes").fetchall()
        for row in nodes:
            node_id = row["id"]
            props = json.loads(row["props"] or "{}")
            self.graph.add_node(
                node_id,
                type=row["type"],
                label=row["label"],
                props=props,
                first_seen=row["first_seen"],
                last_updated=row["last_updated"],
                source_count=row["source_count"]
            )
            self.node_id_map[row["label"]] = node_id
        
        # 加载边
        edges = conn.execute("SELECT * FROM graph_edges").fetchall()
        for row in edges:
            self.graph.add_edge(
                row["src_id"], row["dst_id"],
                key=row["type"],
                type=row["type"],
                weight=row["weight"],
                props=json.loads(row["props"] or "{}"),
                timestamp=row["timestamp"],
                source_obs_id=row["source_obs_id"]
            )
        
        conn.close()
        self._loaded = True
        print(f"[KG] 加载完成: {self.graph.number_of_nodes()} 节点, {self.graph.number_of_edges()} 边")
        return True
    
    def get_node_id(self, label: str) -> Optional[str]:
        """通过 label 查找 node_id"""
        return self.node_id_map.get(label)
    
    def get_node(self, node_id: str) -> Optional[Dict]:
        """获取节点详情"""
        if not self.graph or node_id not in self.graph:
            return None
        data = self.graph.nodes[node_id]
        return {
            "id": node_id,
            "type": data.get("type"),
            "label": data.get("label"),
            "props": data.get("props", {}),
            **data.get("props", {})
        }


# ========== 核心图查询 ==========

def get_customer_network(kg: KnowledgeGraph, hospital_label: str, depth: int = 2) -> Dict:
    """
    以某医院为中心展开关系网络
    BFS 遍历 depth 层
    
    返回: {
        center: {id, label, type},
        nodes: [{id, label, type, distance}],
        edges: [{src, dst, type, weight}],
        stats: {competitor_count, project_count, person_count}
    }
    """
    if not kg.graph:
        return {"error": "NetworkX not available"}
    
    center_id = kg.get_node_id(hospital_label)
    if not center_id:
        return {"error": f"Hospital '{hospital_label}' not found"}
    
    center = kg.get_node(center_id)
    nodes = {center_id: {"distance": 0, **center}}
    edges = []
    
    # BFS
    visited = {center_id}
    frontier = [(center_id, 0)]
    
    while frontier:
        current_id, dist = frontier.pop(0)
        if dist >= depth:
            continue
        
        for neighbor_id in kg.graph.successors(current_id):
            if neighbor_id not in visited:
                visited.add(neighbor_id)
                neighbor = kg.get_node(neighbor_id)
                nodes[neighbor_id] = {"distance": dist + 1, **neighbor}
                frontier.append((neighbor_id, dist + 1))
            
            # 收集边
            for key, data in kg.graph[current_id][neighbor_id].items():
                edges.append({
                    "src": kg.graph.nodes[current_id].get("label", current_id),
                    "dst": kg.graph.nodes[neighbor_id].get("label", neighbor_id),
                    "type": data.get("type", key),
                    "weight": data.get("weight", 1.0)
                })
    
    # 统计
    stats = {
        "competitor_count": sum(1 for n in nodes.values() if n.get("type") == "Competitor"),
        "project_count": sum(1 for n in nodes.values() if n.get("type") == "Project"),
        "person_count": sum(1 for n in nodes.values() if n.get("type") == "Person"),
        "total_nodes": len(nodes),
        "total_edges": len(edges)
    }
    
    return {
        "center": {"id": center_id, "label": center["label"], "type": center["type"]},
        "nodes": list(nodes.values()),
        "edges": edges,
        "stats": stats
    }


def competitor_penetration_path(kg: KnowledgeGraph, competitor_label: str, days: int = 90) -> Dict:
    """
    竞品渗透路径分析
    返回: {
        competitor: {id, label},
        timeline: [{date, hospital, project, event_type, weight}],
        hospital_list: [{name, region, first_seen, last_event}],
        stage_distribution: {stage: count}
    }
    """
    if not kg.graph:
        return {"error": "NetworkX not available"}
    
    comp_id = kg.get_node_id(competitor_label)
    if not comp_id:
        return {"error": f"Competitor '{competitor_label}' not found"}
    
    # 找到所有指向该竞品的关系
    timeline = []
    hospital_set = set()
    stage_dist = {}
    
    for src_id, dst_id, key, data in kg.graph.in_edges(comp_id, keys=True, data=True):
        if data.get("type") == "COMPETES_WITH":
            src_node = kg.get_node(src_id)
            hospital_name = src_node.get("label") if src_node else src_id
            hospital_set.add(hospital_name)
            
            timestamp = data.get("timestamp", "")
            weight = data.get("weight", 1.0)
            props = data.get("props", {})
            
            timeline.append({
                "date": timestamp[:10] if timestamp else "",
                "hospital": hospital_name,
                "project": props.get("project", ""),
                "event_type": props.get("event_type", "mention"),
                "weight": weight
            })
            
            stage = props.get("stage", "unknown")
            stage_dist[stage] = stage_dist.get(stage, 0) + 1
    
    return {
        "competitor": {"id": comp_id, "label": competitor_label},
        "timeline": sorted(timeline, key=lambda x: x["date"], reverse=True),
        "hospital_count": len(hospital_set),
        "hospital_list": list(hospital_set),
        "stage_distribution": stage_dist,
        "total_events": len(timeline)
    }


def recommend_next_visit(kg: KnowledgeGraph, hospital_label: str, top_k: int = 5) -> List[Dict]:
    """
    基于共同邻居推荐下一个拜访医院
    算法: 与当前医院有共同竞品/共同项目/共同政策的其他医院
    """
    if not kg.graph:
        return []
    
    center_id = kg.get_node_id(hospital_label)
    if not center_id:
        return []
    
    # 获取当前医院的邻居（项目、竞品、人等）
    center_neighbors = set(kg.graph.successors(center_id))
    center_neighbors.update(kg.graph.predecessors(center_id))
    
    # 找到所有其他医院
    candidates = []
    for node_id, data in kg.graph.nodes(data=True):
        if data.get("type") == "Hospital" and node_id != center_id:
            # 计算共同邻居数
            node_neighbors = set(kg.graph.successors(node_id))
            node_neighbors.update(kg.graph.predecessors(node_id))
            common = center_neighbors & node_neighbors
            
            if len(common) > 0:
                hospital = kg.get_node(node_id)
                candidates.append({
                    "id": node_id,
                    "label": hospital.get("label", node_id),
                    "region": hospital.get("props", {}).get("region", ""),
                    "common_neighbors": len(common),
                    "shared_entities": [
                        kg.get_node(n).get("label", n) for n in common
                    ],
                    "score": len(common) * 1.0  # 可扩展为加权分数
                })
    
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_k]


def get_influence_chain(kg: KnowledgeGraph, person_label: str, max_depth: int = 3) -> Dict:
    """
    影响力传播分析
    沿 INFLUENCED_BY + HAS_CONTACT + 共同医院边传播
    """
    if not kg.graph:
        return {"error": "NetworkX not available"}
    
    person_id = kg.get_node_id(person_label)
    if not person_id:
        return {"error": f"Person '{person_label}' not found"}
    
    # 找到这个人所在的医院
    hospital_id = None
    for pred in kg.graph.predecessors(person_id):
        if kg.graph.nodes[pred].get("type") == "Hospital":
            hospital_id = pred
            break
    
    # BFS 找可达的人
    visited = {person_id}
    frontier = [(person_id, 0)]
    reachable = []
    
    while frontier:
        current_id, dist = frontier.pop(0)
        if dist >= max_depth:
            continue
        
        # 通过医院找到其他联系人
        for pred in kg.graph.predecessors(current_id):
            if kg.graph.nodes[pred].get("type") == "Hospital":
                for other_person in kg.graph.successors(pred):
                    if other_person not in visited and kg.graph.nodes[other_person].get("type") == "Person":
                        visited.add(other_person)
                        person = kg.get_node(other_person)
                        reachable.append({
                            "id": other_person,
                            "label": person.get("label", other_person),
                            "hospital": kg.get_node(pred).get("label", pred),
                            "distance": dist + 1,
                            "title": person.get("props", {}).get("title", "")
                        })
                        frontier.append((other_person, dist + 1))
    
    return {
        "center": {"id": person_id, "label": person_label, "hospital": kg.get_node(hospital_id).get("label") if hospital_id else ""},
        "reachable_people": reachable,
        "reachable_count": len(reachable),
        "hospitals_reached": len(set(p.get("hospital") for p in reachable))
    }


# ========== 与向量层的融合 ==========

def hybrid_search_v3(
    kg: KnowledgeGraph,
    query: str,
    vector_results: List[Dict],
    top_k: int = 10
) -> List[Dict]:
    """
    混合检索 v3: 向量召回 observations → 图查询展开相关实体
    
    输入: 语义向量检索结果 (observations)
    输出: 合并了图谱信息的增强结果
    """
    if not kg.graph:
        return vector_results
    
    enhanced = []
    seen_entities = set()
    
    for obs in vector_results:
        obs_id = obs.get("id", obs.get("observation_id", ""))
        
        # 找到与该 observation 关联的实体
        related = []
        for node_id, data in kg.graph.nodes(data=True):
            # 简化匹配: 检查 observation 内容是否包含实体名
            content = obs.get("raw_content", obs.get("content", ""))
            if data.get("label", "") in content:
                related.append({
                    "id": node_id,
                    "label": data.get("label"),
                    "type": data.get("type"),
                    "props": data.get("props", {})
                })
                seen_entities.add(node_id)
        
        # 找到这些实体之间的关系
        relationships = []
        for i, e1 in enumerate(related):
            for e2 in related[i+1:]:
                if kg.graph.has_edge(e1["id"], e2["id"]):
                    for key, data in kg.graph[e1["id"]][e2["id"]].items():
                        relationships.append({
                            "from": e1["label"],
                            "to": e2["label"],
                            "type": data.get("type", key)
                        })
        
        enhanced.append({
            **obs,
            "related_entities": related,
            "relationships": relationships,
            "graph_enriched": len(related) > 0
        })
    
    return enhanced


# ========== 快捷接口 ==========

def quick_graph_query(query_type: str, **kwargs) -> Dict:
    """
    快捷查询接口，不需要手动管理 KnowledgeGraph 对象
    
    query_type:
      - "customer_network": hospital_label, depth=2
      - "competitor_path": competitor_label, days=90
      - "recommend_visit": hospital_label, top_k=5
      - "influence": person_label, max_depth=3
    """
    kg = KnowledgeGraph()
    kg.load_from_db()
    
    if query_type == "customer_network":
        return get_customer_network(kg, kwargs.get("hospital_label"), kwargs.get("depth", 2))
    elif query_type == "competitor_path":
        return competitor_penetration_path(kg, kwargs.get("competitor_label"), kwargs.get("days", 90))
    elif query_type == "recommend_visit":
        return {"recommendations": recommend_next_visit(kg, kwargs.get("hospital_label"), kwargs.get("top_k", 5))}
    elif query_type == "influence":
        return get_influence_chain(kg, kwargs.get("person_label"), kwargs.get("max_depth", 3))
    else:
        return {"error": f"Unknown query type: {query_type}"}


# ========== 测试 ==========
if __name__ == "__main__":
    print("=== 知识图谱测试 ===\n")
    
    # 1. 客户关系网
    print("1. 盱眙县中医院 关系网络:")
    result = quick_graph_query("customer_network", hospital_label="盱眙县中医院", depth=2)
    if "error" in result:
        print(f"   {result['error']}")
    else:
        print(f"   中心: {result['center']['label']}")
        print(f"   节点: {result['stats']['total_nodes']} (竞品{result['stats']['competitor_count']}, 项目{result['stats']['project_count']}, 联系人{result['stats']['person_count']})")
        print(f"   关系: {result['stats']['total_edges']}")
        for e in result['edges'][:5]:
            print(f"      {e['src']} --[{e['type']}]--> {e['dst']}")
    
    print("\n2. 瑞斯凯尔 渗透路径:")
    result = quick_graph_query("competitor_path", competitor_label="瑞斯凯尔")
    if "error" in result:
        print(f"   {result['error']}")
    else:
        print(f"   涉及医院: {result['hospital_count']} 家")
        print(f"   事件数: {result['total_events']}")
        for t in result['timeline'][:3]:
            print(f"      {t['date']} | {t['hospital']} | {t['event_type']}")
    
    print("\n3. 推荐拜访 (基于盱眙县中医院):")
    result = quick_graph_query("recommend_visit", hospital_label="盱眙县中医院", top_k=5)
    for r in result.get("recommendations", []):
        print(f"   {r['label']} ({r['region']}) | 共同邻居: {r['common_neighbors']} | 共享: {', '.join(r['shared_entities'][:3])}")
    
    print("\n✅ 知识图谱查询测试完成!")
