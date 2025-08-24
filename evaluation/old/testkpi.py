import json
import math
import time
from pathlib import Path

import networkx as nx

# =============================
# KPI parameters (your tables)
# =============================
COMPATIBILITY = {
    "Cultural":    {"Cultural": 0.50, "Leisure": 0.92, "Office": 0.75, "Residential": 1.00, "Green": 0.83},
    "Leisure":     {"Cultural": 0.92, "Leisure": 0.66, "Office": 0.83, "Residential": 0.92, "Green": 1.00},
    "Office":      {"Cultural": 0.75, "Leisure": 0.83, "Office": 0.41, "Residential": 0.83, "Green": 0.66},
    "Residential": {"Cultural": 1.00, "Leisure": 0.91, "Office": 0.83, "Residential": 0.50, "Green": 1.00},
    "Green":       {"Cultural": 0.83, "Leisure": 1.00, "Office": 0.66, "Residential": 1.00, "Green": 0.58},
}
NODE_WEIGHTS = {"Cultural": 1.2, "Leisure": 1.1, "Office": 1.0, "Residential": 0.9, "Green": 1.3}

# =============================
# Tunables
# =============================
CUTOFF_M = 3000.0
MODE = "street_anchor"   # "typed" or "street_anchor"
ASSUMED_RADIUS_KM = 1.0  # for density reporting; change if you used a different radius

# Empirical bands for score_x1000 (from your runs: ~0.8–2.8)
BANDS = {
    "low": 0.8,    # below this: LOW
    "ok":  1.0,    # 1.0–2.0: ACCEPTABLE
    "good":2.0,    # 2.0–3.0: GOOD
    "great": 3.0   # >3.0: EXCEPTIONAL (rare but possible)
}
MIN_TYPED = 50            # below this, result may be unstable
MIN_TYPED_PER_KM2 = 25    # minimum reasonable density of typed nodes

# =============================
# Categorization (your mapping)
# =============================
def categorize_node(props):
    tags = {str(k).lower(): str(v).lower() for k, v in props.items()}
    building = tags.get("building", "").strip()
    amenity  = tags.get("amenity", "").strip()
    leisure  = tags.get("leisure", "").strip()
    landuse  = tags.get("landuse", "").strip()
    typ      = tags.get("type", "").strip()

    residential = {"apartments","house","residential","semidetached_house","terrace","bungalow","detached","dormitory","yes"}
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

# =============================
# Graph I/O
# =============================
def load_latest_graph_path():
    osm_dir = Path("knowledge/osm")
    latest_job_dir = max((p for p in osm_dir.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    return latest_job_dir / "graph.json", latest_job_dir

def load_graph(graph_path: Path) -> nx.Graph:
    with open(graph_path, "r", encoding="utf-8") as f:
        gj = json.load(f)
    G = nx.Graph()
    for n in gj["nodes"]:
        nid = n["id"]
        G.add_node(nid, **{k: v for k, v in n.items() if k != "id"})
    for e in gj["edges"]:
        u, v = e["u"], e["v"]
        attrs = {k: v for k, v in e.items() if k not in ("u","v")}
        if "distance" not in attrs:
            try:
                x1, y1 = G.nodes[u]["x"], G.nodes[u]["y"]
                x2, y2 = G.nodes[v]["x"], G.nodes[v]["y"]
                attrs["distance"] = math.hypot(x2 - x1, y2 - y1)
            except Exception:
                attrs["distance"] = 1.0
        G.add_edge(u, v, **attrs)
    return G

def typed_nodes_dict(G: nx.Graph):
    typed = {}
    for nid, data in G.nodes(data=True):
        cat = categorize_node(data)
        if cat in NODE_WEIGHTS:
            typed[nid] = cat
    return typed

# =============================
# KPI: helper for category counts
# =============================
def counts_for(nodes, typed_map):
    counts = {k: 0 for k in NODE_WEIGHTS}
    for n in nodes:
        c = typed_map.get(n)
        if c in counts:
            counts[c] += 1
    return counts

# =============================
# KPI (typed mode)
# =============================
def compute_kpi_typed(G: nx.Graph, cutoff_m: float):
    typed = typed_nodes_dict(G)
    T = list(typed.keys())
    if len(T) < 2:
        return 0.0, 0, 0, len(T), counts_for(T, typed)

    score_sum = 0.0
    pair_count = 0
    paths_found = 0

    for comp in nx.connected_components(G):
        comp_typed = [n for n in comp if n in typed]
        if len(comp_typed) < 2:
            continue
        H = G.subgraph(comp)
        for i, u in enumerate(comp_typed):
            lengths = nx.single_source_dijkstra_path_length(H, u, weight="distance", cutoff=cutoff_m)
            for v in comp_typed[i+1:]:
                d = lengths.get(v)
                if d is None or d <= 0:
                    continue
                cu, cv = typed[u], typed[v]
                score_sum += (NODE_WEIGHTS[cu] * NODE_WEIGHTS[cv] * COMPATIBILITY[cu][cv]) / float(d)
                pair_count += 1
                paths_found += 1

    avg_per_pair = (score_sum / max(1, pair_count)) if pair_count > 0 else 0.0
    return avg_per_pair, pair_count, paths_found, len(T), counts_for(T, typed)

# =============================
# KPI (street_anchor mode)
# =============================
def compute_kpi_street_anchor(G: nx.Graph, cutoff_m: float):
    typed = typed_nodes_dict(G)
    T = list(typed.keys())
    if len(T) < 2:
        return 0.0, 0, 0, len(T), counts_for(T, typed)

    # Build street-only subgraph
    street_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "street"]
    S = nx.Graph()
    for n in street_nodes:
        d = G.nodes[n]
        S.add_node(n, x=d.get("x"), y=d.get("y"))
    for u, v, d in G.edges(data=True):
        if G.nodes[u].get("type") == "street" and G.nodes[v].get("type") == "street":
            S.add_edge(u, v, distance=d.get("distance", 1.0))

    # Anchor and access lengths (typed node -> nearest street via its access edge)
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

    # Keep only typed nodes that have a valid anchor
    T = [n for n in T if n in anchor]
    if len(T) < 2:
        return 0.0, 0, 0, len(T), counts_for(T, typed)

    # Precompute street distances between anchors (cutoff)
    unique_anchors = sorted(set(anchor[n] for n in T))
    anchor_dists = {a: nx.single_source_dijkstra_path_length(S, a, weight="distance", cutoff=max(0.0, cutoff_m))
                    for a in unique_anchors}

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
            cu, cv = typed[u], typed[v]
            score_sum += (NODE_WEIGHTS[cu] * NODE_WEIGHTS[cv] * COMPATIBILITY[cu][cv]) / float(d)
            pair_count += 1
            paths_found += 1

    avg_per_pair = (score_sum / max(1, pair_count)) if pair_count > 0 else 0.0
    return avg_per_pair, pair_count, paths_found, len(T), counts_for(T, typed)

# =============================
# Classifier
# =============================
def classify(score_x1000: float):
    if score_x1000 < BANDS["low"]:
        return "LOW"
    if BANDS["ok"] <= score_x1000 < BANDS["good"]:
        return "ACCEPTABLE"
    if score_x1000 < BANDS["great"]:
        return "GOOD"
    return "EXCEPTIONAL"

# =============================
# Main
# =============================
if __name__ == "__main__":
    graph_path, job_dir = load_latest_graph_path()
    print(f"Using: {graph_path}")
    G = load_graph(graph_path)
    print(f"Total nodes: {G.number_of_nodes()}")
    print(f"Total edges: {G.number_of_edges()}")

    # First pass in configured MODE
    t0 = time.time()
    if MODE == "street_anchor":
        avg, pair_count, paths_found, typed_N, cat_counts = compute_kpi_street_anchor(G, CUTOFF_M)
        method_used = "street_anchor"
    else:
        avg, pair_count, paths_found, typed_N, cat_counts = compute_kpi_typed(G, CUTOFF_M)
        method_used = "typed"
    dt = time.time() - t0

    # Stability checks and optional fallback
    area_km2 = math.pi * (ASSUMED_RADIUS_KM ** 2) if ASSUMED_RADIUS_KM > 0 else 0.0
    typed_per_km2 = (typed_N / area_km2) if area_km2 > 0 else 0.0
    fallback_used = False

    if method_used == "street_anchor" and (typed_N < MIN_TYPED or typed_per_km2 < MIN_TYPED_PER_KM2):
        print("[NOTICE] Low typed sample or density detected. Retrying with 'typed' method for stability.")
        t1 = time.time()
        avg, pair_count, paths_found, typed_N, cat_counts = compute_kpi_typed(G, CUTOFF_M)
        dt = (time.time() - t1)  # report the fallback compute time
        method_used = "typed"
        fallback_used = True
        area_km2 = math.pi * (ASSUMED_RADIUS_KM ** 2) if ASSUMED_RADIUS_KM > 0 else 0.0
        typed_per_km2 = (typed_N / area_km2) if area_km2 > 0 else 0.0

    score_x1000 = avg * 1000.0
    verdict = classify(score_x1000)

    print(f"Typed nodes: {typed_N}")
    print(f"Category counts: {cat_counts}")
    print(f"Pairs evaluated: {pair_count}")
    print(f"Paths found: {paths_found}")
    print(f"Score (average per pair): {avg:.6f}")
    print(f"Score (scaled x1000): {score_x1000:.3f}  -> {verdict}")
    print(f"Typed density: {typed_per_km2:.1f} /km²  (radius≈{ASSUMED_RADIUS_KM} km)")
    print(f"Elapsed: {dt:.2f}s  (method={method_used}, cutoff={CUTOFF_M} m)")
    if fallback_used:
        print("[INFO] Fallback to 'typed' method was applied due to small/sparse sample.")

    # Stability hints
    if typed_N < MIN_TYPED:
        print(f"[WARN] Few typed nodes ({typed_N} < {MIN_TYPED}). KPI may be unstable.")
    if typed_per_km2 < MIN_TYPED_PER_KM2:
        print(f"[WARN] Low typed node density ({typed_per_km2:.1f}/km² < {MIN_TYPED_PER_KM2}/km²).")

    # Save result (for history / UI)
    out = {
        "graph_path": str(graph_path),
        "job_dir": str(job_dir),
        "method": method_used,
        "cutoff_m": CUTOFF_M,
        "score_avg_per_pair": avg,
        "score_x1000": score_x1000,
        "verdict": verdict,
        "typed_nodes": typed_N,
        "category_counts": cat_counts,
        "pairs_evaluated": pair_count,
        "paths_found": paths_found,
        "typed_per_km2": typed_per_km2,
        "area_km2": area_km2,
        "elapsed_s": dt,
        "fallback_used": fallback_used,
    }
    with open(job_dir / "kpi_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"KPI saved to: {job_dir / 'kpi_result.json'}")
