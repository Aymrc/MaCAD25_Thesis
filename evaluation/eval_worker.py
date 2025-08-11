# eval_worker.py - Python 3 evaluation worker
# Reads: JOB_DIR\graph.json and JOB_DIR\boundary.json
# Writes: JOB_DIR\evaluation.json and JOB_DIR\EVAL_DONE.txt / EVAL_FAILED.txt

import os, json, sys, traceback
from datetime import datetime

import networkx as nx

try:
    from shapely.geometry import Point, Polygon
except Exception as e:
    print("Shapely is required. pip install shapely")
    raise

def _load_json(path):
    with open(path, "r") as f:
        return json.load(f)

def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def _categorize_node(data):
    tags = {str(k).lower(): str(v).lower() for k, v in data.items()}
    building = tags.get("building")

    residential_buildings = {
        "apartments","house","residential","semidetached_house","terrace",
        "bungalow","detached","dormitory"
    }
    office_buildings = {
        "office","commercial","industrial","retail","manufacture","warehouse","service"
    }
    cultural_buildings = {"college","school","kindergarten","government","civic","church","fire_station","prison"}
    leisure_buildings = {"hotel","boathouse","houseboat","bridge"}
    green_buildings = {"greenhouse","allotment_house"}

    if building == "yes":
        return "Residential"
    if building in residential_buildings:
        return "Residential"
    elif building in office_buildings:
        return "Office"
    elif building in cultural_buildings:
        return "Cultural"
    elif building in leisure_buildings:
        return "Leisure"
    elif building in green_buildings:
        return "Green"
    if any(kw in tags.get("amenity", "") for kw in ["museum","theatre","gallery"]):
        return "Cultural"
    if any(kw in tags.get("leisure", "") for kw in ["park","recreation","garden"]):
        return "Leisure"
    if tags.get("landuse") in ["grass","meadow"] or "green" in tags.get("type", ""):
        return "Green"
    return None

COMPATIBILITY = {
    "Cultural":    {"Cultural": 0.50, "Leisure": 0.92, "Office": 0.75, "Residential": 1.00, "Green": 0.83},
    "Leisure":     {"Cultural": 0.92, "Leisure": 0.66, "Office": 0.83, "Residential": 0.92, "Green": 1.00},
    "Office":      {"Cultural": 0.75, "Leisure": 0.83, "Office": 0.41, "Residential": 0.83, "Green": 0.66},
    "Residential": {"Cultural": 1.00, "Leisure": 0.91, "Office": 0.83, "Residential": 0.50, "Green": 1.00},
    "Green":       {"Cultural": 0.83, "Leisure": 1.00, "Office": 0.66, "Residential": 1.00, "Green": 0.58},
}

NODE_WEIGHTS = {
    "Cultural": 1.2, "Leisure": 1.1, "Office": 1.0, "Residential": 0.9, "Green": 1.3
}

def _build_graph_from_json(graph_json):
    G = nx.Graph()
    for n in graph_json.get("nodes", []):
        nid = n["id"]
        G.add_node(nid, **{k:v for k,v in n.items() if k!="id"})
    for e in graph_json.get("edges", []):
        u = e["u"]; v = e["v"]
        attrs = {k:v for k,v in e.items() if k not in ("u","v")}
        # ensure distance exists
        if "distance" not in attrs:
            try:
                x1,y1 = G.nodes[u]["x"], G.nodes[u]["y"]
                x2,y2 = G.nodes[v]["x"], G.nodes[v]["y"]
                attrs["distance"] = ((x2-x1)**2 + (y2-y1)**2) ** 0.5
            except:
                attrs["distance"] = 1.0
        G.add_edge(u, v, **attrs)
    return G

def main():
    job_dir = os.environ.get("JOB_DIR")
    if not job_dir or not os.path.isdir(job_dir):
        raise RuntimeError("JOB_DIR not set or invalid: {}".format(job_dir))

    graph_path = os.path.join(job_dir, "graph.json")
    boundary_path = os.path.join(job_dir, "boundary.json")

    if not os.path.exists(graph_path):
        raise RuntimeError("graph.json not found at {}".format(graph_path))
    if not os.path.exists(boundary_path):
        raise RuntimeError("boundary.json not found at {}".format(boundary_path))

    graph_json = _load_json(graph_path)
    boundary_xy = _load_json(boundary_path)

    # Build graph and boundary polygon
    G = _build_graph_from_json(graph_json)
    poly = Polygon(boundary_xy)

    # Collect inside nodes
    node_ids_inside = []
    for nid, data in G.nodes(data=True):
        x = data.get("x"); y = data.get("y")
        if x is None or y is None: 
            continue
        if poly.contains(Point(x, y)):
            node_ids_inside.append(nid)

    typed_nodes = {}
    for nid in node_ids_inside:
        cat = _categorize_node(G.nodes[nid])
        if cat in NODE_WEIGHTS:
            typed_nodes[nid] = cat

    # Score
    score_sum = 0.0
    pair_count = 0
    path_found = 0

    inside_typed = list(typed_nodes.keys())
    N = len(inside_typed)

    for i in range(N):
        for j in range(i+1, N):
            u = inside_typed[i]; v = inside_typed[j]
            try:
                dist = nx.shortest_path_length(G, source=u, target=v, weight="distance")
                if dist > 0:
                    cu = typed_nodes[u]; cv = typed_nodes[v]
                    ku = NODE_WEIGHTS[cu]; kv = NODE_WEIGHTS[cv]
                    fuv = COMPATIBILITY[cu][cv]
                    score_sum += (ku * kv * fuv) / float(dist)
                    pair_count += 1
                    path_found += 1
            except:
                continue

    score = (score_sum / (max(1, N) * max(1, pair_count))) if (N > 0 and pair_count > 0) else 0.0

    out = {
        "job_dir": job_dir,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "stats": {
            "total_nodes": G.number_of_nodes(),
            "total_edges": G.number_of_edges(),
            "nodes_inside": len(node_ids_inside),
            "typed_nodes_inside": len(typed_nodes),
            "pairs_evaluated": pair_count,
            "paths_found": path_found
        },
        "score": score
    }

    _save_json(os.path.join(job_dir, "evaluation.json"), out)
    open(os.path.join(job_dir, "EVAL_DONE.txt"), "w").write("ok\n")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        tb = traceback.format_exc()
        sys.stderr.write(tb + "\n")
        open(os.path.join(os.environ.get("JOB_DIR","."), "EVAL_FAILED.txt"), "w").write(str(e) + "\n" + tb)
        sys.exit(1)
