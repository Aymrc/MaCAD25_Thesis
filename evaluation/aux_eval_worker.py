# aux_eval_worker.py — Minimal evaluator for specific graphs (LLM-friendly)
# Configure the list GRAPHS_TO_EVALUATE below. No CLI args, no env vars.
#
# For each input graph JSON:
#   - Writes outputs into: <project_root>\knowledge\aux_evaluation\
#       * <basename>_evaluation.json
#       * EVAL_DONE_<basename>.txt   (or EVAL_FAILED_<basename>.txt)
#   - Prints a machine-friendly JSON summary to stdout.

import os
import sys
import json
import math
import time
import traceback
from datetime import datetime, timezone

import networkx as nx

try:
    # Kept for compatibility; boundary polygons are NOT used
    from shapely.geometry import Point, Polygon  # noqa: F401
except Exception:
    print("Shapely is required. pip install shapely")
    raise

# =============================
# CONFIG — EDIT THESE PATHS
# =============================
# Example:
# GRAPHS_TO_EVALUATE = [
#     r"C:\Users\CDH\Documents\GitHub\MaCAD25_Thesis\knowledge\iteration\it1.json",
#     r"C:\Users\CDH\Documents\GitHub\MaCAD25_Thesis\knowledge\iteration\it2.json",
# ]
GRAPHS_TO_EVALUATE = [
    # Add absolute or relative paths to graph JSON files here
]

# Centralized output directory: <project_root>\knowledge\aux_evaluation
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
AUX_OUT_DIR = os.path.join(PROJECT_ROOT, "knowledge", "aux_evaluation")

# =============================
# KPI parameters
# =============================
COMPATIBILITY = {
    "Cultural":    {"Cultural": 0.50, "Leisure": 0.92, "Office": 0.75, "Residential": 1.00, "Green": 0.83},
    "Leisure":     {"Cultural": 0.92, "Leisure": 0.66, "Office": 0.83, "Residential": 0.92, "Green": 1.00},
    "Office":      {"Cultural": 0.75, "Leisure": 0.83, "Office": 0.41, "Residential": 0.83, "Green": 0.66},
    "Residential": {"Cultural": 1.00, "Leisure": 0.91, "Office": 0.83, "Residential": 0.50, "Green": 1.00},
    "Green":       {"Cultural": 0.83, "Leisure": 1.00, "Office": 0.66, "Residential": 1.00, "Green": 0.58},
}
NODE_WEIGHTS = {"Cultural": 1.2, "Leisure": 1.1, "Office": 1.0, "Residential": 0.9, "Green": 1.3}

# Interaction cutoff (meters)
CUTOFF_M = 3000.0

# Verdict bands (x1000 scale)
BANDS = {"low": 0.8, "ok": 1.0, "good": 2.0, "great": 3.0}

# Stability guards
MIN_TYPED = 50
MIN_TYPED_PER_KM2 = 25

# Reference scores (x1000) for normalization
REFERENCE_SCORES = {
    "NewYork_US":     2.738,
    "Barcelona_ES":   2.468,
    "Tokyo_JP":       2.331,
    "Madrid_ES":      2.314,
    "Paris_FR":       2.081,
    "Copenhagen_DK":  2.013,
    "MexicoCity_MX":  1.941,
    "Amsterdam_NL":   1.920,
    "London_UK":      1.468,
    "Singapore_SG":   0.792,
}

# =============================
# Helpers
# =============================
def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

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

def _typed_nodes_all(G):
    typed = {}
    for nid, data in G.nodes(data=True):
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

def _compute_kpi_typed(G: nx.Graph, typed_map: dict, cutoff_m: float):
    T = list(typed_map.keys())
    if len(T) < 2:
        return 0.0, 0, 0
    score_sum = 0.0
    pair_count = 0
    paths_found = 0
    for comp in nx.connected_components(G):
        comp_typed = [n for n in comp if n in typed_map]
        if len(comp_typed) < 2:
            continue
        H = G.subgraph(comp)
        for i, u in enumerate(comp_typed):
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

def _compute_kpi_street_anchor(G: nx.Graph, typed_map: dict, cutoff_m: float):
    T = list(typed_map.keys())
    if len(T) < 2:
        return 0.0, 0, 0
    street_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "street"]
    S = nx.Graph()
    for n in street_nodes:
        d = G.nodes[n]
        S.add_node(n, x=d.get("x"), y=d.get("y"))
    for u, v, d in G.edges(data=True):
        if G.nodes[u].get("type") == "street" and G.nodes[v].get("type") == "street":
            S.add_edge(u, v, distance=d.get("distance", 1.0))
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

def _scale_1_100(value, vmin, vmax):
    if vmax <= vmin:
        return 50.0
    scaled = 1 + (value - vmin) * (99.0 / (vmax - vmin))
    return float(round(max(1.0, min(100.0, scaled)), 1))

# =============================
# Core evaluation
# =============================
def evaluate_graph(graph_path):
    """
    Evaluate a single graph JSON. Returns a dict with results and also writes files to AUX_OUT_DIR.
    """
    t_all0 = time.time()
    graph_json = _load_json(graph_path)
    G = _build_graph_from_json(graph_json)

    # Area estimate from node bounding box (m^2 -> km^2)
    xs = [d.get("x") for _, d in G.nodes(data=True) if d.get("x") is not None]
    ys = [d.get("y") for _, d in G.nodes(data=True) if d.get("y") is not None]
    if len(xs) >= 2 and len(ys) >= 2:
        area_km2 = max(0.0, (max(xs) - min(xs)) * (max(ys) - min(ys)) / 1e6)
    else:
        area_km2 = 0.0

    typed_inside = _typed_nodes_all(G)
    inside_typed_ids = list(typed_inside.keys())
    typed_N = len(inside_typed_ids)
    cat_counts = _counts_for(inside_typed_ids, typed_inside)
    typed_per_km2 = (typed_N / area_km2) if area_km2 > 0 else 0.0

    # Fast method then fallback
    method_used = "street_anchor"
    t0 = time.time()
    avg, pairs, paths = _compute_kpi_street_anchor(G, typed_inside, CUTOFF_M)
    elapsed = time.time() - t0

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

    ref_min = min(REFERENCE_SCORES.values())
    ref_max = max(REFERENCE_SCORES.values())
    score_scaled_1_100 = _scale_1_100(score_x1000, ref_min, ref_max)
    reference_scores_scaled = {city: _scale_1_100(val, ref_min, ref_max) for city, val in REFERENCE_SCORES.items()}
    rating_norm = "high" if score_scaled_1_100 >= 70 else "medium" if score_scaled_1_100 >= 40 else "low"

    # Centralized output paths
    os.makedirs(AUX_OUT_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(graph_path))[0]
    eval_json_path = os.path.join(AUX_OUT_DIR, f"{base}_evaluation.json")
    done_txt = os.path.join(AUX_OUT_DIR, f"EVAL_DONE_{base}.txt")

    out = {
        "job_dir": AUX_OUT_DIR,
        "input_path": os.path.abspath(graph_path),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
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
            "scaled_1_100": score_scaled_1_100,
            "scaled_rating": rating_norm,
            "reference": {
                "raw_x1000": REFERENCE_SCORES,
                "scaled_1_100": reference_scores_scaled
            }
        },
        "elapsed_s": elapsed,
        "elapsed_total_s": time.time() - t_all0,
    }

    _save_json(eval_json_path, out)
    with open(done_txt, "w", encoding="utf-8") as f:
        f.write("ok\n")

    print(f"[aux-evaluation] {os.path.basename(graph_path)} -> {score_x1000:.3f}  ({score_scaled_1_100}/100 → {verdict})")

    return {
        "input": os.path.abspath(graph_path),
        "output": eval_json_path,
        "ok": True,
        "score_x1000": score_x1000,
        "scaled_1_100": score_scaled_1_100,
        "verdict": verdict,
    }

# =============================
# Runner
# =============================
def main():
    if not GRAPHS_TO_EVALUATE:
        sys.stderr.write("No graphs specified in GRAPHS_TO_EVALUATE.\n")
        print(json.dumps({"ok": False, "error": "No graphs specified", "aux_output_dir": AUX_OUT_DIR, "items": []}))
        sys.exit(2)

    items = []
    ok_count = 0
    os.makedirs(AUX_OUT_DIR, exist_ok=True)

    for raw_path in GRAPHS_TO_EVALUATE:
        graph_path = os.path.abspath(raw_path)
        if not os.path.exists(graph_path):
            items.append({"input": graph_path, "ok": False, "error": "Input JSON not found"})
            continue
        try:
            res = evaluate_graph(graph_path)
            items.append(res)
            if res.get("ok"):
                ok_count += 1
        except Exception as e:
            tb = traceback.format_exc()
            base = os.path.splitext(os.path.basename(graph_path))[0]
            fail_txt = os.path.join(AUX_OUT_DIR, f"EVAL_FAILED_{base}.txt")
            try:
                with open(fail_txt, "w", encoding="utf-8") as f:
                    f.write(str(e) + "\n" + tb)
            except:
                pass
            items.append({"input": graph_path, "ok": False, "error": str(e)})

    summary = {
        "ok": ok_count == len(items) and ok_count > 0,
        "total": len(items),
        "ok_count": ok_count,
        "failed": len(items) - ok_count,
        "aux_output_dir": AUX_OUT_DIR,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": items,
    }
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()