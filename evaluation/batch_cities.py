# testkpi_synthetic.py
# Valida el KPI contra valores "golden" medidos previamente.
# No descarga nada: usa los graph.json ya existentes en knowledge/osm/*

import os, json, math, sys, time
from pathlib import Path
from itertools import combinations

import networkx as nx
from scipy.spatial import cKDTree

# -----------------------------
# Config
# -----------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
KNOWLEDGE_OSM = PROJECT_ROOT / "knowledge" / "osm"

# Tolerancias: pasa si |actual - esperado| <= max(ABS, PCT*esperado)
TOLERANCE_PCT = 10.0    # ±10%
TOLERANCE_ABS = 0.25    # ±0.25 puntos (en "x1000")

# Radio (km) usado en tus lotes (afecta densidad reportada)
RADIUS_KM = 1.0

# Golden scores (última corrida que compartiste)
EXPECTED = {
    "NewYork_US":    {"lat": 40.7128, "lon": -74.0060, "score_x1000": 2.738},
    "Barcelona_ES":  {"lat": 41.3874, "lon":   2.1686, "score_x1000": 2.468},
    "Tokyo_JP":      {"lat": 35.6895, "lon": 139.6917, "score_x1000": 2.331},
    "Madrid_ES":     {"lat": 40.4165, "lon":  -3.7000, "score_x1000": 2.314},
    "Paris_FR":      {"lat": 48.8566, "lon":   2.3522, "score_x1000": 2.081},
    "Copenhagen_DK": {"lat": 55.6758, "lon":  12.5683, "score_x1000": 2.013},
    "MexicoCity_MX": {"lat": 19.4326, "lon": -99.1332, "score_x1000": 1.941},
    "Amsterdam_NL":  {"lat": 52.3676, "lon":   4.9041, "score_x1000": 1.920},
    "London_UK":     {"lat": 51.5074, "lon":  -0.1278, "score_x1000": 1.468},
    "Singapore_SG":  {"lat":  1.3521, "lon": 103.8198, "score_x1000": 0.792},
}

# KPI params (como en batch)
COMPATIBILITY = {
    "Cultural":    {"Cultural": 0.50, "Leisure": 0.92, "Office": 0.75, "Residential": 1.00, "Green": 0.83},
    "Leisure":     {"Cultural": 0.92, "Leisure": 0.66, "Office": 0.83, "Residential": 0.92, "Green": 1.00},
    "Office":      {"Cultural": 0.75, "Leisure": 0.83, "Office": 0.41, "Residential": 0.83, "Green": 0.66},
    "Residential": {"Cultural": 1.00, "Leisure": 0.91, "Office": 0.83, "Residential": 0.50, "Green": 1.00},
    "Green":       {"Cultural": 0.83, "Leisure": 1.00, "Office": 0.66, "Residential": 1.00, "Green": 0.58},
}
NODE_WEIGHTS = {"Cultural": 1.2, "Leisure": 1.1, "Office": 1.0, "Residential": 0.9, "Green": 1.3}

def categorize_node(props):
    t = {str(k).lower(): str(v).lower() for k, v in props.items()}
    b = t.get("building", "").strip()
    amenity  = t.get("amenity", "").strip()
    leisure  = t.get("leisure", "").strip()
    landuse  = t.get("landuse", "").strip()
    typ      = t.get("type", "").strip()

    residential = {"apartments","house","residential","semidetached_house","terrace","bungalow","detached","dormitory","yes"}
    office = {"office","commercial","industrial","retail","manufacture","warehouse","service"}
    cultural = {"college","school","kindergarten","government","civic","church","fire_station","prison"}
    leisure_set = {"hotel","boathouse","houseboat","bridge"}
    green_set = {"greenhouse","allotment_house"}

    if b in residential: return "Residential"
    if b in office:      return "Office"
    if b in cultural:    return "Cultural"
    if b in leisure_set: return "Leisure"
    if b in green_set:   return "Green"

    if amenity in ("university", "place_of_worship"): return "Cultural"
    if b in ("chapel", "synagogue", "university"):    return "Cultural"
    if any(k in leisure for k in ("park", "recreation", "garden")): return "Leisure"
    if any(k in amenity for k in ("museum", "theatre", "gallery")): return "Cultural"
    if landuse in ("grass", "meadow") or "green" in typ: return "Green"
    return None

def load_graph(graph_path: Path) -> nx.Graph:
    with open(graph_path, "r", encoding="utf-8") as f:
        gj = json.load(f)
    G = nx.Graph()
    for n in gj["nodes"]:
        nid = n["id"]
        G.add_node(nid, **{k:v for k,v in n.items() if k != "id"})
    for e in gj["edges"]:
        u, v = e["u"], e["v"]
        attrs = {k:v for k,v in e.items() if k not in ("u","v")}
        if "distance" not in attrs:
            try:
                x1, y1 = G.nodes[u]["x"], G.nodes[u]["y"]
                x2, y2 = G.nodes[v]["x"], G.nodes[v]["y"]
                attrs["distance"] = math.hypot(x2 - x1, y2 - y1)
            except Exception:
                attrs["distance"] = 1.0
        G.add_edge(u, v, **attrs)
    return G

def compute_kpi_street_anchor(G: nx.Graph):
    # 1) nodos tipados
    typed = {}
    for nid, data in G.nodes(data=True):
        cat = categorize_node(data)
        if cat in NODE_WEIGHTS and "x" in data and "y" in data:
            typed[nid] = cat
    T = list(typed.keys())

    cat_counts = {k: 0 for k in NODE_WEIGHTS}
    for n in T:
        cat_counts[typed[n]] += 1

    street_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "street" and "x" in d and "y" in d]
    if len(T) < 2 or not street_nodes:
        return 0.0

    # 2) anclaje a calle + kdtree
    pos_streets = {n: (G.nodes[n]["x"], G.nodes[n]["y"]) for n in street_nodes}
    street_ids = list(pos_streets.keys())
    street_kdt = cKDTree([pos_streets[n] for n in street_ids])

    anchor_of = {}
    for t in T:
        x, y = G.nodes[t]["x"], G.nodes[t]["y"]
        _, idx = street_kdt.query([x, y], k=1)
        anchor_of[t] = street_ids[idx]

    # 3) conteo por ancla
    anchor_counts = {}
    for t in T:
        a = anchor_of[t]
        c = typed[t]
        if a not in anchor_counts:
            anchor_counts[a] = {k: 0 for k in NODE_WEIGHTS}
        anchor_counts[a][c] += 1
    anchors = list(anchor_counts.keys())

    # 4) subgrafo de calles + distancias
    street_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("type") == "street"]
    S = G.edge_subgraph(street_edges).copy()
    for u, v, d in S.edges(data=True):
        if "distance" not in d:
            x1, y1 = S.nodes[u].get("x"), S.nodes[u].get("y")
            x2, y2 = S.nodes[v].get("x"), S.nodes[v].get("y")
            d["distance"] = math.hypot(x2 - x1, y2 - y1) if None not in (x1,y1,x2,y2) else 1.0
    anchors = [a for a in anchors if a in S]
    if len(anchors) < 2:
        return 0.0

    CUTOFF_M = int(RADIUS_KM * 3000)
    dist_from = {a: nx.single_source_dijkstra_path_length(S, a, cutoff=CUTOFF_M, weight="distance") for a in anchors}

    # 5) score agregado por pares de anclas
    pair_count = 0
    score_sum = 0.0
    idx_of = {a: i for i, a in enumerate(anchors)}
    for a in anchors:
        ca = anchor_counts[a]
        da = dist_from[a]
        for b in anchors:
            if idx_of[b] <= idx_of[a]:
                continue
            db = da.get(b)
            if db is None or db <= 0.0 or db > CUTOFF_M:
                continue
            cb = anchor_counts[b]
            pairs_ab = 0
            contrib_ab = 0.0
            for cu, nu in ca.items():
                if nu == 0: continue
                ku = NODE_WEIGHTS[cu]
                for cv, nv in cb.items():
                    if nv == 0: continue
                    kv = NODE_WEIGHTS[cv]
                    fuv = COMPATIBILITY[cu][cv]
                    pairs = nu * nv
                    pairs_ab += pairs
                    contrib_ab += pairs * ((ku * kv * fuv) / float(db))
            if pairs_ab > 0:
                pair_count += pairs_ab
                score_sum += contrib_ab

    avg_per_pair = (score_sum / max(1, pair_count)) if pair_count > 0 else 0.0
    return avg_per_pair * 1000.0  # “x1000” como tus reportes

def find_latest_folder(lat: float, lon: float) -> Path | None:
    """Busca en knowledge/osm la carpeta más reciente que contenga _{lat:.4f}_{lon:.4f}."""
    if not KNOWLEDGE_OSM.exists():
        return None
    needle = f"_{lat:.4f}_{lon:.4f}"
    candidates = [p for p in KNOWLEDGE_OSM.iterdir() if p.is_dir() and needle in p.name]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]

def main():
    results = []
    fails = 0

    print(f"Tolerancia: ±{TOLERANCE_PCT:.1f}% o ±{TOLERANCE_ABS:.3f} (lo mayor)")
    for city, cfg in EXPECTED.items():
        lat, lon, expected = cfg["lat"], cfg["lon"], cfg["score_x1000"]
        folder = find_latest_folder(lat, lon)
        if not folder:
            print(f"[SKIP] {city}: no encuentro carpeta en {KNOWLEDGE_OSM} con _{lat:.4f}_{lon:.4f}")
            continue
        graph = folder / "graph.json"
        if not graph.exists():
            print(f"[SKIP] {city}: falta {graph}")
            continue

        G = load_graph(graph)
        actual = compute_kpi_street_anchor(G)

        tol = max(TOLERANCE_ABS, expected * (TOLERANCE_PCT / 100.0))
        diff = abs(actual - expected)
        ok = diff <= tol

        status = "OK " if ok else "FAIL"
        if not ok:
            fails += 1

        print(f"[{status}] {city:<14} → actual={actual:.3f}  esperado={expected:.3f}  "
              f"Δ={diff:.3f}  tol=±{tol:.3f}  (folder={folder.name})")

        results.append({
            "city": city,
            "lat": lat, "lon": lon,
            "folder": folder.name,
            "score_expected_x1000": expected,
            "score_actual_x1000": actual,
            "diff_abs": diff,
            "tolerance": tol,
            "pass": ok
        })

    # CSV de resultados
    out_csv = KNOWLEDGE_OSM / "testkpi_synthetic_results.csv"
    import csv
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "city","lat","lon","folder",
            "score_expected_x1000","score_actual_x1000","diff_abs","tolerance","pass"
        ])
        w.writeheader()
        for r in results:
            w.writerow(r)

    print(f"\nCSV → {out_csv}")
    if fails > 0:
        print(f"\nResultado: {fails} casos fuera de tolerancia.")
        sys.exit(1)
    else:
        print("\nResultado: todos dentro de tolerancia ✅")
        sys.exit(0)

if __name__ == "__main__":
    main()