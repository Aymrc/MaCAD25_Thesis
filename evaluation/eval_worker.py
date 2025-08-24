# eval_worker.py - Python 3 evaluation worker (refined KPI)
# Mode (batch-only):
#   - python eval_worker.py <path\to\iteration>  -> process all it*.json in that folder (top-level only)
#   - python eval_worker.py                      -> process all it*.json in DEFAULT_ITERATION_DIR
#
# Outputs:
#   <iteration_dir>\evaluation\itN_evaluation.json
#   <iteration_dir>\evaluation\EVAL_DONE_itN.txt (or EVAL_FAILED_itN.txt)
#   Best iteration copied atomically to: <project_root>\knowledge\enriched\enriched_graph.json

import os
import re
import sys
import time
import json
import math
import shutil
import tempfile
import traceback
from datetime import datetime, timezone

import networkx as nx

try:
    # Polygon kept for compatibility; boundary is not used
    from shapely.geometry import Point, Polygon  # noqa: F401
except Exception:
    print("Shapely is required. pip install shapely")
    raise

# =============================
# Path configuration (relative-friendly)
# =============================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))

# Default directories
DEFAULT_ITERATION_DIR = os.path.join(PROJECT_ROOT, "knowledge", "iteration")
DEFAULT_INPUT_JSON = os.path.join(DEFAULT_ITERATION_DIR, "it1.json")

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
    "good": 2.0,   # 2.0–3.0 -> GOOD
    "great": 3.0   # >3.0    -> EXCEPTIONAL
}

# Stability guards: if typed sample is too small or sparse, fall back to "typed" method
MIN_TYPED = 50
MIN_TYPED_PER_KM2 = 25

# =============================
# Reference scores for normalization (x1000 scale)
# =============================
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

# -----------------------------
# IO helpers
# -----------------------------
def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def _atomic_copy(src, dst):
    """Atomic-ish copy to avoid UI reading half-written file."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    d = os.path.dirname(dst)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_enriched_", dir=d)
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        # os.replace is atomic on modern Windows / NTFS and POSIX
        os.replace(tmp, dst)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

# -----------------------------
# Graph + categorization
# -----------------------------
def _build_graph_from_json(graph_json):
    """Build an undirected NetworkX graph.
    Accepts {edges} or {links}, and 'u'/'v' or 'source'/'target' endpoints.
    Fills 'distance' if missing using XY."""
    G = nx.Graph()

    # Nodes
    for n in (graph_json.get("nodes", []) or []):
        nid = n.get("id")
        if nid is None:
            continue
        G.add_node(nid, **{k: v for k, v in n.items() if k != "id"})

    # Edges (accept both 'edges' and 'links')
    in_edges = graph_json.get("edges") or graph_json.get("links") or []
    for e in in_edges:
        if not e:
            continue
        u = e.get("u", e.get("source"))
        v = e.get("v", e.get("target"))
        if u is None or v is None:
            continue
        attrs = {k: val for k, val in e.items() if k not in ("u", "v", "source", "target")}
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
    # Lowercased copy of attributes to simplify matching
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
def _typed_nodes_all(G):
    """Return dict {node_id: category} for all typed nodes in the graph."""
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

# -----------------------------
# KPI (typed) – stable but slower
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
        # Single-source shortest path lengths with cutoff
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

# -----------------------------
# KPI (street_anchor) – default fast path
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
                dist_edge = G.edges[n, nbr].get("distance", 0.0)
                if dist_edge < best_d:
                    best_d = dist_edge
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
# Normalization helpers (1–100)
# -----------------------------
def _scale_1_100(value, vmin, vmax):
    """Affine map to [1,100] with clamping."""
    if vmax <= vmin:
        return 50.0
    scaled = 1 + (value - vmin) * (99.0 / (vmax - vmin))
    return float(round(max(1.0, min(100.0, scaled)), 1))

# -----------------------------
# Batch helpers
# -----------------------------
_IT_RE = re.compile(r"^it(\d+)\.json$", re.IGNORECASE)

def list_iteration_files(iter_dir):
    """
    Return absolute paths to files named it*.json at the top level of iter_dir.
    Does not descend into subfolders (so it won't touch 'evaluation/').
    Sorted by numeric suffix (it1, it2, ...).
    """
    candidates = []
    for name in os.listdir(iter_dir):
        full = os.path.join(iter_dir, name)
        if not os.path.isfile(full):
            continue
        m = _IT_RE.match(name)
        if m:
            idx = int(m.group(1))
            candidates.append((idx, full))
    candidates.sort(key=lambda x: x[0])
    return [path for _, path in candidates]

def process_one_graph(graph_path, out_dir):
    """
    Process a single JSON graph and write outputs into out_dir.
    Returns (ok: bool, eval_json_path: str, score_x1000: float | None, input_path: str).
    """
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(graph_path))[0]  # e.g., it1
    eval_json_path = os.path.join(out_dir, f"{base}_evaluation.json")
    done_txt = os.path.join(out_dir, f"EVAL_DONE_{base}.txt")
    fail_txt = os.path.join(out_dir, f"EVAL_FAILED_{base}.txt")

    # Clean previous markers for this base
    for marker in (done_txt, fail_txt):
        try:
            if os.path.exists(marker):
                os.remove(marker)
        except Exception:
            pass

    try:
        t_all0 = time.time()

        graph_json = _load_json(graph_path)
        G = _build_graph_from_json(graph_json)

        # Area from bounding box (m^2 -> km^2)
        xs = [d.get("x") for _, d in G.nodes(data=True) if d.get("x") is not None]
        ys = [d.get("y") for _, d in G.nodes(data=True) if d.get("y") is not None]
        if len(xs) >= 2 and len(ys) >= 2:
            area_km2 = max(0.0, (max(xs) - min(xs)) * (max(ys) - min(ys)) / 1e6)
        else:
            area_km2 = 0.0

        # Typed nodes across the whole graph (no boundary filter)
        typed_inside = _typed_nodes_all(G)
        inside_typed_ids = list(typed_inside.keys())
        typed_N = len(inside_typed_ids)
        cat_counts = _counts_for(inside_typed_ids, typed_inside)
        typed_per_km2 = (typed_N / area_km2) if area_km2 > 0 else 0.0

        # KPI fast path + fallback
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

        # Scoring and normalization
        score_x1000 = avg * 1000.0
        verdict = _classify(score_x1000)

        ref_min = min(REFERENCE_SCORES.values())
        ref_max = max(REFERENCE_SCORES.values())
        score_scaled_1_100 = _scale_1_100(score_x1000, ref_min, ref_max)
        reference_scores_scaled = {
            city: _scale_1_100(val, ref_min, ref_max) for city, val in REFERENCE_SCORES.items()
        }

        rating_norm = "high" if score_scaled_1_100 >= 70 else "medium" if score_scaled_1_100 >= 40 else "low"

        out = {
            "job_dir": out_dir,
            "input_path": graph_path,
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

        print(f"[evaluation] {os.path.basename(graph_path)} -> {score_x1000:.3f}  ({score_scaled_1_100}/100 → {rating_norm.upper()})")
        return True, eval_json_path, score_x1000, graph_path

    except Exception:
        tb = traceback.format_exc()
        try:
            with open(fail_txt, "w", encoding="utf-8") as f:
                f.write(tb)
        except Exception:
            pass
        sys.stderr.write(tb + "\n")
        return False, eval_json_path, None, graph_path

# -----------------------------
# Main (batch-only)
# -----------------------------
def main():
    """
    Batch-only mode:
      - python eval_worker.py <path\to\iteration>  -> process all it*.json in that folder
      - python eval_worker.py                      -> process all it*.json in DEFAULT_ITERATION_DIR
    """
    # 1) Resolve iteration directory
    if len(sys.argv) > 1:
        iter_dir = os.path.abspath(sys.argv[1])
    else:
        iter_dir = os.path.abspath(DEFAULT_ITERATION_DIR)

    if not os.path.isdir(iter_dir):
        raise RuntimeError(f"Iteration directory not found or invalid: {iter_dir}")

    # 2) Collect candidate files (top-level it*.json only)
    files = list_iteration_files(iter_dir)
    if not files:
        raise RuntimeError(f"No it*.json files found in: {iter_dir}")

    # 3) Prepare output folder
    out_dir = os.path.join(iter_dir, "evaluation")
    os.makedirs(out_dir, exist_ok=True)

    # 4) Process all iterations
    results = []
    ok_count = 0
    best = {"score": float("-inf"), "input": None, "eval": None}
    for fp in files:
        ok, eval_path, score_x1000, input_path = process_one_graph(fp, out_dir)
        item = {
            "input": input_path,
            "output": eval_path,
            "ok": bool(ok),
            "score_x1000": score_x1000,
        }
        results.append(item)
        if ok:
            ok_count += 1
            if score_x1000 is not None and score_x1000 > best["score"]:
                best = {"score": score_x1000, "input": input_path, "eval": eval_path}

    # 5) Write enriched_graph.json (copy of the best iteration JSON) into <project_root>\knowledge\enriched\enriched_graph.json
    enriched_dir = os.path.join(PROJECT_ROOT, "knowledge", "enriched")
    os.makedirs(enriched_dir, exist_ok=True)
    enriched_graph_path = os.path.join(enriched_dir, "enriched_graph.json")

    if best["input"]:
        _atomic_copy(best["input"], enriched_graph_path)
        print(f"[batch] Best iteration: {os.path.basename(best['input'])} (score_x1000={best['score']:.3f})")
        print(f"[batch] Wrote enriched_graph.json to: {enriched_graph_path}")
    else:
        print("[batch] No successful iterations to enrich.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        tb = traceback.format_exc()
        sys.stderr.write(tb + "\n")
        # Generic failure marker in default evaluation dir
        try:
            fallback_dir = os.path.join(DEFAULT_ITERATION_DIR, "evaluation")
            os.makedirs(fallback_dir, exist_ok=True)
            with open(os.path.join(fallback_dir, "EVAL_FAILED.txt"), "w", encoding="utf-8") as f:
                f.write(str(e) + "\n" + tb)
        finally:
            pass
        sys.exit(1)
