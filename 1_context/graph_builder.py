# 1_context/graph_builder.py
# Build an urban graph from OSM GeoJSON outputs and write a compact graph.json for Rhino preview.

import os
import sys
import json
import math
from typing import List, Tuple, Dict, Any

import networkx as nx
from shapely.geometry import shape
from scipy.spatial import cKDTree

TOLERANCE_M = 1.0  # merge tolerance for street vertices (meters)

def load_geojson(path: str) -> Dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)

def line_coords_from_feature(geom: Dict[str, Any]) -> List[List[Tuple[float, float]]]:
    """Return list of parts, each part is list of (x,y). Handles LineString and MultiLineString."""
    gtype = geom.get("type")
    if gtype == "LineString":
        return [geom["coordinates"]]
    if gtype == "MultiLineString":
        return geom["coordinates"]
    return []

def build_graph(streets_json: Dict, buildings_json: Dict, greens_json: Dict) -> nx.Graph:
    G = nx.Graph()

    # 1) Build street vertex set with KDTree-based merging
    coord_list: List[Tuple[float, float]] = []
    id_list: List[str] = []
    kdt = None
    vcount = 0

    def get_or_create_vertex(x: float, y: float) -> str:
        nonlocal vcount, kdt
        if coord_list:
            dist, idx = kdt.query([x, y], k=1)
            if dist < TOLERANCE_M:
                return id_list[idx]
        node_id = "street_v{}".format(vcount)
        vcount += 1
        G.add_node(node_id, x=float(x), y=float(y), type="street")
        coord_list.append((float(x), float(y)))
        id_list.append(node_id)
        kdt = cKDTree(coord_list)
        return node_id

    # Streets
    for feat in streets_json.get("features", []):
        parts = line_coords_from_feature(feat.get("geometry", {}))
        for coords in parts:
            if len(coords) < 2:
                continue
            vertex_ids = []
            for c in coords:
                x, y = float(c[0]), float(c[1])
                vid = get_or_create_vertex(x, y)
                vertex_ids.append(vid)
            # connect consecutive
            for i in range(len(vertex_ids) - 1):
                a, b = vertex_ids[i], vertex_ids[i+1]
                ax, ay = G.nodes[a]["x"], G.nodes[a]["y"]
                bx, by = G.nodes[b]["x"], G.nodes[b]["y"]
                dist = math.hypot(bx - ax, by - ay)
                G.add_edge(a, b, type="street", line=[(ax, ay), (bx, by)], distance=dist)

    # KDTree for street access edges
    if coord_list:
        street_kdt = cKDTree(coord_list)
    else:
        street_kdt = None

    def add_pois(src_json: Dict, prefix: str, node_type: str):
        if street_kdt is None:
            return
        for i, feat in enumerate(src_json.get("features", [])):
            try:
                geom = shape(feat.get("geometry"))
                c = geom.centroid
                x, y = float(c.x), float(c.y)
            except Exception:
                continue
            node_id = "{}_{}".format(prefix, i)
            G.add_node(node_id, x=x, y=y, type=node_type)
            # connect to nearest street node
            dist, idx = street_kdt.query([x, y], k=1)
            street_id = id_list[idx]
            sx, sy = G.nodes[street_id]["x"], G.nodes[street_id]["y"]
            G.add_edge(node_id, street_id, type="access", line=[(x, y), (sx, sy)], distance=float(dist))

    # Buildings and greens as POIs
    add_pois(buildings_json, "building", "building")
    add_pois(greens_json, "green", "green")

    return G

def export_graph_json(G: nx.Graph, out_path: str):
    data = {
        "nodes": [
            {"id": n, "x": d.get("x"), "y": d.get("y"), "type": d.get("type")}
            for n, d in G.nodes(data=True)
            if "x" in d and "y" in d
        ],
        "edges": [
            {"u": u, "v": v, "type": d.get("type"), "distance": d.get("distance"),
             "line": d.get("line")}
            for u, v, d in G.edges(data=True)
            if "line" in d
        ]
    }
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)

def main():
    # OUT_DIR provided by llm/osm worker; fallback allows manual runs
    out_dir = os.environ.get("OUT_DIR")
    if not out_dir:
        # Default to 1_context/runtime/osm/_tmp for manual testing
        here = os.path.dirname(__file__)
        out_dir = os.path.abspath(os.path.join(here, "runtime", "osm", "_tmp"))
    streets_p   = os.path.join(out_dir, "streets.geojson")
    buildings_p = os.path.join(out_dir, "buildings.geojson")
    greens_p    = os.path.join(out_dir, "greens.geojson")

    if not all(os.path.exists(p) for p in (streets_p, buildings_p, greens_p)):
        raise IOError("Missing streets/buildings/greens GeoJSON in {}".format(out_dir))

    streets   = load_geojson(streets_p)
    buildings = load_geojson(buildings_p)
    greens    = load_geojson(greens_p)

    G = build_graph(streets, buildings, greens)
    export_graph_json(G, os.path.join(out_dir, "graph.json"))

    # Optional flag so the Rhino-side watcher can react
    with open(os.path.join(out_dir, "GRAPH_DONE.txt"), "w") as f:
        f.write("ok")

if __name__ == "__main__":
    main()