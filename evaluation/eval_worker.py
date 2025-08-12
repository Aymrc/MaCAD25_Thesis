# eval_worker.py - Python 3 evaluation worker (refined KPI)
# Reads: JOB_DIR\graph.json and JOB_DIR\boundary.json
# Writes: JOB_DIR\evaluation.json and JOB_DIR\EVAL_DONE.txt / EVAL_FAILED.txt

import os
import sys
import json
import math
import time
import traceback
from datetime import datetime

import networkx as nx

try:
    from shapely.geometry import Point, Polygon
except Exception:
    print("Shapely is required. pip install shapely")
    raise

# =============================
# KPI parameters (same tables)
# =============================
COMPATIBILITY = {
    "Cultural":    {"Cultural": 0.50, "Leisure": 0.92, "Office": 0.75, "Residential": 1.00, "Green": 0.83},
    "Leisure":     {"Cultural": 0.92, "Leisure": 0.66, "Office": 0.83, "Residential": 0.92, "Green": 1.00},
    "Office":      {"Cultural": 0.75, "Leisure": 0.83, "Office": 0.41, "Residential": 0.83, "Green": 0.66},
    "Residential": {"Cultural": 1.00, "Leisure": 0.91, "Office": 0.83, "Residential": 0.50, "Green": 1.00},
    "Green":       {"Cultural": 0.83, "Leisure": 1.00, "Office": 0.66, "Residential": 1.00, "Green": 0.58},
}
NODE_WEIGHTS = {"Cultural": 1.2, "Leisure": 1.1, "Office": 1.0, "Residential": 0.9, "Green": 1.3}

# Distance cutoff (meters) to focus on human-scale interactions and speed up
CUTOFF_M = 3000.0

# Verdict bands (x1000 scale), aligned with your empirical runs
BANDS = {
    "low": 0.8,    # below -> LOW
    "ok":  1.0,    # 1.0–2.0 -> ACCEPTABLE
    "good":2.0,    # 2.0–3.0 -> GOOD
    "great": 3.0   # >3.0    -> EXCEPTIONAL
}

# Stability guards: if typed sample is too small or sparse, fall back to "typed" method
MIN_TYPED = 50
MIN_TYPED_PER_KM2 = 25


# -----------------------------
# IO helpers
# -----------------------------
def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# -----------------------------
# Graph + categorization
# -----------------------------
def _build_graph_from_json(graph_json):
    G = nx.Graph()
    for n in graph_json.get("nodes", []):
        nid = n["id"]
        G.add_node(nid, **{k: v for k, v in n.items() if k != "id"})
    for e in graph_json.get("edges", []):
        u, v = e["u"], e["v"]
        attrs = {k: v for k, v in e.items() if k not in ("u", "v")}
        if "distance" not in attrs:
            try:
                x1, y1 = G.nodes[u]["x"], G.nodes[u]["y"]
                x2, y2 = G.nodes[v]["x"], G.nodes[v]["y"]
                attrs["distance"] = math.hypot(x2 - x1, y2 - y1)
            except Exception:
                attrs["distance"] = 1.0
        G.add_edge(u, v, **attrs)
    return G

def _categorize_node(data):
    tags = {str(k).lower(): str(v).lower() for k, v in data.items()}
    building = tags.get("building", "").strip()
    amenity  = tags.get("amenity", "").strip()
    leisure  = tags.get("leisure", "").strip()
    landuse  = tags.get("landuse", "").strip()
    typ      = tags.get("type", "").strip()

    residential = {
        "apartments","house","residential","semidetached_house","terrace",
        "bungalow","detached","dormitory","yes"
    }
    office = {"office","commercial","industrial","retail","manufacture","warehouse","service"}
    cultural = {"college","school","kindergarten","government","civic","church","fire_station","prison"}
    leisure_set = {"hotel","boathouse","houseboat","bridge"}
    green_set = {"greenhouse","allotment_house"}

    if building in residential: return "Residential"
    if building in office:      return "Office"
    if building in cultural:    return "Cultural"
    if building in leisure_set: return "Leisure"
    if building in green_set:   return "Green"

    if amenity in ("university", "place_of_worship"): return "Cultural"
    if building in ("chapel", "synagogue", "university"): return "Cultural"

    if any(k in leisure for k in ("park", "recreation", "garden")): return "Leisure"
    if any(k in amenity for k in ("museum", "theatre", "gallery")): return "Cultural"
    if landuse in ("grass", "meadow") or "green" in typ: return "Green"
    return None


# -----------------------------
# KPI helpers
# -----------------------------
def _typed_nodes_inside(G, poly: Polygon):
    """Return dict {node_id: category} for typed nodes strictly inside the polygon."""
    typed = {}
    for nid, data in G.nodes(data=True):
        x, y = data.get("x"), data.get("y")
        if x is None or y is None:
            continue
        if not poly.contains(Point(x, y)):
            continue
        cat = _categorize_node(data)
        if cat in NODE_WEIGHTS:
            typed[nid] = cat
    return typed

def _counts_for(node_ids, typed_map):
    counts = {k: 0 for k in NODE_WEIGHTS}
    for n in node_ids:
        c = typed_map.get(n)
        if c in counts:
            counts[c] += 1
    return counts

def _classify(score_x1000: float):
    if score_x1000 < BANDS["low"]:
        return "LOW"
    if BANDS["ok"] <= score_x1000 < BANDS["good"]:
        return "ACCEPTABLE"
    if score_x1000 < BANDS["great"]:
        return "GOOD"
    return "EXCEPTIONAL"


# -----------------------------
# KPI (typed) – stable but slower
# Works within the inside-typed set only.
# -----------------------------
def _compute_kpi_typed(G: nx.Graph, typed_map: dict, cutoff_m: float):
    T = list(typed_map.keys())
    if len(T) < 2:
        return 0.0, 0, 0

    score_sum = 0.0
    pair_count = 0
    paths_found = 0

    # Restrict to components to reduce Dijkstra domains
    for comp in nx.connected_components(G):
        comp_typed = [n for n in comp if n in typed_map]
        if len(comp_typed) < 2:
            continue
        H = G.subgraph(comp)
        for i, u in enumerate(comp_typed):
            # Single-source shortest path lengths with cutoff
            lengths = nx.single_source_dijkstra_path_length(H, u, weight="distance", cutoff=cutoff_m)
            for v in comp_typed[i+1:]:
                d = lengths.get(v)
                if d is None or d <= 0:
                    continue
                cu, cv = typed_map[u], typed_map[v]
                score_sum += (NODE_WEIGHTS[cu] * NODE_WEIGHTS[cv] * COMPATIBILITY[cu][cv]) / float(d)
                pair_count += 1
                paths_found += 1

    avg = (score_sum / max(1, pair_count)) if pair_count > 0 else 0.0
    return avg, pair_count, paths_found


# -----------------------------
# KPI (street_anchor) – default fast path
# Uses anchor -> street distance + access legs, restricted to inside-typed nodes.
# -----------------------------
def _compute_kpi_street_anchor(G: nx.Graph, typed_map: dict, cutoff_m: float):
    T = list(typed_map.keys())
    if len(T) < 2:
        return 0.0, 0, 0

    # Build street-only subgraph
    street_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "street"]
    S = nx.Graph()
    for n in street_nodes:
        d = G.nodes[n]
        S.add_node(n, x=d.get("x"), y=d.get("y"))
    for u, v, d in G.edges(data=True):
        if G.nodes[u].get("type") == "street" and G.nodes[v].get("type") == "street":
            S.add_edge(u, v, distance=d.get("distance", 1.0))

    # Anchor selection (typed node -> nearest connected street via its access edge)
    anchor = {}
    access_len = {}
    for n in T:
        best_sid = None
        best_d = float("inf")
        for nbr in G.neighbors(n):
            if G.nodes[nbr].get("type") == "street":
                d = G.edges[n, nbr].get("distance", 0.0)
                if d < best_d:
                    best_d = d
                    best_sid = nbr
        if best_sid is not None:
            anchor[n] = best_sid
            access_len[n] = best_d

    T = [n for n in T if n in anchor]
    if len(T) < 2:
        return 0.0, 0, 0

    # Precompute distances between anchors with cutoff on street graph
    unique_anchors = sorted(set(anchor[n] for n in T))
    anchor_dists = {
        a: nx.single_source_dijkstra_path_length(S, a, weight="distance", cutoff=max(0.0, cutoff_m))
        for a in unique_anchors
    }

    score_sum = 0.0
    pair_count = 0
    paths_found = 0

    for i, u in enumerate(T):
        au = anchor[u]
        acc_u = access_len[u]
        for v in T[i+1:]:
            av = anchor[v]
            acc_v = access_len[v]
            ds = anchor_dists.get(au, {}).get(av)
            if ds is None:
                continue
            d = acc_u + ds + acc_v
            if d <= 0:
                continue
            cu, cv = typed_map[u], typed_map[v]
            score_sum += (NODE_WEIGHTS[cu] * NODE_WEIGHTS[cv] * COMPATIBILITY[cu][cv]) / float(d)
            pair_count += 1
            paths_found += 1

    avg = (score_sum / max(1, pair_count)) if pair_count > 0 else 0.0
    return avg, pair_count, paths_found


# -----------------------------
# Main
# -----------------------------
def main():
    job_dir = os.environ.get("JOB_DIR")
    if not job_dir or not os.path.isdir(job_dir):
        raise RuntimeError(f"JOB_DIR not set or invalid: {job_dir}")

    graph_path = os.path.join(job_dir, "graph.json")
    boundary_path = os.path.join(job_dir, "boundary.json")

    if not os.path.exists(graph_path):
        raise RuntimeError(f"graph.json not found at {graph_path}")
    if not os.path.exists(boundary_path):
        raise RuntimeError(f"boundary.json not found at {boundary_path}")

    t_all0 = time.time()
    graph_json = _load_json(graph_path)
    boundary_xy = _load_json(boundary_path)

    # Build graph and polygon (coordinates are already in projected meters)
    G = _build_graph_from_json(graph_json)
    poly = Polygon(boundary_xy)
    area_km2 = (poly.area / 1e6) if poly and poly.area else 0.0

    # Typed nodes strictly inside boundary
    typed_inside = _typed_nodes_inside(G, poly)
    inside_typed_ids = list(typed_inside.keys())
    typed_N = len(inside_typed_ids)
    cat_counts = _counts_for(inside_typed_ids, typed_inside)
    typed_per_km2 = (typed_N / area_km2) if area_km2 > 0 else 0.0

    # Compute KPI (fast method first)
    method_used = "street_anchor"
    t0 = time.time()
    avg, pairs, paths = _compute_kpi_street_anchor(G, typed_inside, CUTOFF_M)
    elapsed = time.time() - t0

    # Fallback for stability if the sample is small/sparse
    fallback_used = False
    if (typed_N < MIN_TYPED) or (typed_per_km2 < MIN_TYPED_PER_KM2):
        t1 = time.time()
        avg2, pairs2, paths2 = _compute_kpi_typed(G, typed_inside, CUTOFF_M)
        elapsed = time.time() - t1
        avg, pairs, paths = avg2, pairs2, paths2
        method_used = "typed"
        fallback_used = True

    score_x1000 = avg * 1000.0
    verdict = _classify(score_x1000)

    out = {
        "job_dir": job_dir,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "method": method_used,
        "fallback_used": fallback_used,
        "cutoff_m": CUTOFF_M,
        "bands_x1000": BANDS,
        "stats": {
            "total_nodes": G.number_of_nodes(),
            "total_edges": G.number_of_edges(),
            "area_km2": area_km2,
            "typed_nodes_inside": typed_N,
            "typed_density_per_km2": typed_per_km2,
            "category_counts": cat_counts,
            "pairs_evaluated": pairs,
            "paths_found": paths,
        },
        "score": {
            "avg_per_pair": avg,
            "x1000": score_x1000,
            "verdict": verdict,
        },
        "elapsed_s": elapsed,
        "elapsed_total_s": time.time() - t_all0,
    }

    _save_json(os.path.join(job_dir, "evaluation.json"), out)
    with open(os.path.join(job_dir, "EVAL_DONE.txt"), "w", encoding="utf-8") as f:
        f.write("ok\n")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        tb = traceback.format_exc()
        sys.stderr.write(tb + "\n")
        try:
            with open(os.path.join(os.environ.get("JOB_DIR", "."), "EVAL_FAILED.txt"), "w", encoding="utf-8") as f:
                f.write(str(e) + "\n" + tb)
        finally:
            pass
        sys.exit(1)
