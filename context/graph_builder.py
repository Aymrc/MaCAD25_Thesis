# context/graph_builder.py
# Build an urban graph from OSM GeoJSON outputs and write a compact graph.json.

import os
import sys
import json
import math
import time
from typing import List, Tuple, Dict, Any

import networkx as nx
from shapely.geometry import shape

# Optional SciPy KDTree; fall back to linear scan if unavailable (e.g., Python 3.13)
try:
    from scipy.spatial import cKDTree
except Exception:
    cKDTree = None

TOLERANCE_M = 1.0  # merge tolerance for street vertices (meters)

def load_geojson(path: str) -> Dict[str, Any]:
    # Read as UTF-8 first; fall back to UTF-8 with BOM or replace on error.
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except UnicodeDecodeError:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return json.load(f)

def line_coords_from_feature(geom: Dict[str, Any]) -> List[List[Tuple[float, float]]]:
    gtype = geom.get("type")
    if gtype == "LineString":
        return [geom["coordinates"]]
    if gtype == "MultiLineString":
        return geom["coordinates"]
    return []

def build_graph(streets_json: Dict, buildings_json: Dict, greens_json: Dict) -> nx.Graph:
    G = nx.Graph()

    # ---- 1) Streets: create/merge vertices and edges ----
    coord_list: List[Tuple[float, float]] = []
    id_list: List[str] = []
    kdt = None
    vcount = 0

    def _rebuild_kdt():
        nonlocal kdt
        if cKDTree and coord_list:
            kdt = cKDTree(coord_list)

    def _nearest_existing(x: float, y: float):
        """Return (node_id, distance) or (None, +inf) if empty."""
        if not coord_list:
            return None, float("inf")
        if cKDTree and kdt is not None:
            dist, idx = kdt.query([x, y], k=1)
            return id_list[int(idx)], float(dist)
        # Fallback: linear scan
        best_id, best_d2 = None, float("inf")
        for nid, (cx, cy) in zip(id_list, coord_list):
            d2 = (cx - x) * (cx - x) + (cy - y) * (cy - y)
            if d2 < best_d2:
                best_d2, best_id = d2, nid
        return best_id, math.sqrt(best_d2)

    def get_or_create_vertex(x: float, y: float) -> str:
        nonlocal vcount
        nid, dist = _nearest_existing(x, y)
        if dist < TOLERANCE_M:
            return nid
        node_id = "street_v{}".format(vcount)
        vcount += 1
        G.add_node(node_id, x=float(x), y=float(y), type="street")
        coord_list.append((float(x), float(y)))
        id_list.append(node_id)
        _rebuild_kdt()
        return node_id

    for feat in streets_json.get("features", []):
        parts = line_coords_from_feature(feat.get("geometry", {}))
        for coords in parts:
            if len(coords) < 2:
                continue
            vids = [get_or_create_vertex(float(x), float(y)) for x, y in coords]
            for i in range(len(vids) - 1):
                a, b = vids[i], vids[i + 1]
                ax, ay = G.nodes[a]["x"], G.nodes[a]["y"]
                bx, by = G.nodes[b]["x"], G.nodes[b]["y"]
                dist = math.hypot(bx - ax, by - ay)
                G.add_edge(a, b, type="street", line=[(ax, ay), (bx, by)], distance=dist)

    # ---- 2) POIs: connect centroid to nearest street vertex ----
    street_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "street"]
    street_coords = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in street_nodes]

    if cKDTree and street_coords:
        street_kdt = cKDTree(street_coords)
    else:
        street_kdt = None  # linear scan fallback

    def _nearest_street(x: float, y: float):
        if street_kdt is not None:
            dist, idx = street_kdt.query([x, y], k=1)
            sid = street_nodes[int(idx)]
            return sid, float(dist)
        best_sid, best_d2 = None, float("inf")
        for sid, (sx, sy) in zip(street_nodes, street_coords):
            d2 = (sx - x) * (sx - x) + (sy - y) * (sy - y)
            if d2 < best_d2:
                best_d2, best_sid = d2, sid
        return (best_sid, math.sqrt(best_d2)) if best_sid is not None else (None, float("inf"))

    def add_pois(src_json: Dict, prefix: str, node_type: str):
        if not src_json:
            return
        # If there are no streets, keep old behavior (skip attaching if none)
        idx = 0
        for feat in src_json.get("features", []):
            try:
                geom = shape(feat.get("geometry"))
                if geom.is_empty:
                    continue
                c = geom.centroid
                x, y = float(c.x), float(c.y)
            except Exception:
                continue

            # Copy properties and remove keys that collide with our node schema
            props = dict(feat.get("properties", {}) or {})
            props.pop("type", None)   # <-- avoid collision with our 'type=node_type'
            props.pop("id", None)     # (optional) we already use our own node_id

            node_id = f"{prefix}_{idx}"
            idx += 1

            # Store node with our canonical 'type' plus remaining properties
            G.add_node(node_id, x=x, y=y, type=node_type, **props)

            # Optional access edge to nearest street
            if street_nodes:
                sid, dist = _nearest_street(x, y)
                if sid is not None:
                    sx, sy = G.nodes[sid]["x"], G.nodes[sid]["y"]
                    G.add_edge(
                        node_id, sid,
                        type="access",
                        line=[(x, y), (sx, sy)],
                        distance=float(dist)
                    )

    add_pois(buildings_json, "building", "building")
    add_pois(greens_json, "green", "green")

    return G

def export_graph_json(G: nx.Graph, out_path: str):
    data = {
        "nodes": [
            {
                "id": n,
                "x": d.get("x"),
                "y": d.get("y"),
                "type": d.get("type"),
                # export all other attributes for categorization
                **{k: v for k, v in d.items() if k not in ("x", "y", "type")}
            }
            for n, d in G.nodes(data=True)
            if "x" in d and "y" in d
        ],
        "edges": [
            {"u": u, "v": v, "type": d.get("type"), "distance": d.get("distance"), "line": d.get("line")}
            for u, v, d in G.edges(data=True)
            if "line" in d
        ]
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("[graph_builder] Wrote graph.json: nodes={}, edges={}".format(len(data["nodes"]), len(data["edges"])))

def _resolve_out_dir() -> str:
    """
    Priority: CLI arg > OUT_DIR env > newest folder under knowledge/osm
    that already contains the three required GeoJSON files OR a DONE.txt sentinel.
    This avoids racing a half-written job folder.
    """
    if len(sys.argv) >= 2 and sys.argv[1]:
        return os.path.abspath(sys.argv[1])
    env_dir = os.environ.get("OUT_DIR")
    if env_dir:
        return os.path.abspath(env_dir)

    here = os.path.dirname(__file__)
    project_root = os.path.abspath(os.path.join(here, ".."))
    base_dir = os.path.join(project_root, "knowledge", "osm")
    subdirs = [os.path.join(base_dir, d) for d in os.listdir(base_dir)
               if os.path.isdir(os.path.join(base_dir, d))]
    if not subdirs:
        raise IOError("No OSM job folders found under {}".format(base_dir))

    REQUIRED = {"streets.geojson", "buildings.geojson", "greens.geojson"}

    # prefer newest dir that looks complete
    complete = []
    for d in subdirs:
        files = set(os.listdir(d))
        if REQUIRED.issubset(files) or "DONE.txt" in files:
            complete.append(d)

    if complete:
        return max(complete, key=os.path.getmtime)

    # fallback: newest dir, even if incomplete
    return max(subdirs, key=os.path.getmtime)

def main():
    out_dir = _resolve_out_dir()
    print("[graph_builder] Using OUT_DIR:", out_dir)

    streets_p   = os.path.join(out_dir, "streets.geojson")
    buildings_p = os.path.join(out_dir, "buildings.geojson")
    greens_p    = os.path.join(out_dir, "greens.geojson")

    # Simple wait loop in case files are being written right now
    deadline = time.time() + 30.0  # up to 30s
    while time.time() < deadline and not all(os.path.exists(p) for p in (streets_p, buildings_p, greens_p)):
        time.sleep(0.5)

    missing = [p for p in (streets_p, buildings_p, greens_p) if not os.path.exists(p)]
    if missing:
        raise IOError("Missing required GeoJSON files:\n  " + "\n  ".join(missing))

    streets   = load_geojson(streets_p)
    buildings = load_geojson(buildings_p)
    greens    = load_geojson(greens_p)

    G = build_graph(streets, buildings, greens)
    export_graph_json(G, os.path.join(out_dir, "graph.json"))

    with open(os.path.join(out_dir, "GRAPH_DONE.txt"), "w", encoding="utf-8") as f:
        f.write("ok")

if __name__ == "__main__":
    main()
