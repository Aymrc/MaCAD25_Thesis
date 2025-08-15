# merged.py
# Uso:
#   python merged.py "C:\Users\CDH\Documents\GitHub\MaCAD25_Thesis\knowledge\osm\osm_20250815_095943"
# ó con env JOB_DIR
# Requisitos: shapely (para el test punto-en-polígono)

import os, sys, json, shutil
from pathlib import Path

try:
    from shapely.geometry import Point, Polygon
except Exception as e:
    print("[merged] ERROR: requiere 'shapely' (pip install shapely)")
    raise

PROJECT_DIR = Path(__file__).resolve().parents[1]  # .../MaCAD25_Thesis
KNOWLEDGE_DIR = PROJECT_DIR / "knowledge"
MASSING_GRAPH_PATH = KNOWLEDGE_DIR / "massing_graph.json"

def load_json(p: Path):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(p: Path, data: dict):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def endpoints_of(edge: dict):
    """Soporta formatos {u,v} y {source,target}."""
    if "u" in edge and "v" in edge:
        return edge["u"], edge["v"], "uv"
    return edge.get("source"), edge.get("target"), "st"

def set_endpoints(edge: dict, a, b, mode: str):
    if mode == "uv":
        edge["u"], edge["v"] = a, b
    else:
        edge["source"], edge["target"] = a, b

def unique_node_id(base_id: str, used: set):
    """Devuelve un id no usado, aplicando sufijos _m, _m2, ..."""
    if base_id not in used:
        return base_id
    i = 1
    cand = f"{base_id}_m"
    while cand in used:
        i += 1
        cand = f"{base_id}_m{i}"
    return cand

def run(job_dir: Path):
    graph_path = job_dir / "graph.json"
    boundary_path = job_dir / "boundary.json"

    if not graph_path.exists():
        raise FileNotFoundError(f"No existe graph.json en {graph_path}")
    if not MASSING_GRAPH_PATH.exists():
        raise FileNotFoundError(f"No existe massing_graph.json en {MASSING_GRAPH_PATH}")

    # 1) Backup
    backup_path = job_dir / "old_graph.json"
    shutil.copyfile(graph_path, backup_path)
    print(f"[merged] Copia creada: {backup_path}")

    # 2) Cargar graph y boundary; borrar nodos/edges dentro de PLOT
    graph = load_json(graph_path)
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    if boundary_path.exists():
        boundary_xy = load_json(boundary_path)
        poly = Polygon(boundary_xy)
        # nodos a eliminar (estrictamente dentro)
        to_delete = set()
        for n in nodes:
            x, y = n.get("x"), n.get("y")
            if x is None or y is None:
                continue
            if poly.contains(Point(float(x), float(y))):
                to_delete.add(n["id"])

        if to_delete:
            nodes = [n for n in nodes if n["id"] not in to_delete]
            pruned_edges = []
            for e in edges:
                a, b, mode = endpoints_of(e)
                if a in to_delete or b in to_delete:
                    continue  # edge con endpoint eliminado
                pruned_edges.append(e)
            edges = pruned_edges
            print(f"[merged] Eliminados {len(to_delete)} nodos y "
                  f"{len(graph.get('edges', [])) - len(edges)} edges dentro del PLOT.")
        else:
            print("[merged] Ningún nodo dentro del PLOT; nada que borrar.")
    else:
        print(f"[merged] WARNING: no hay boundary.json en {boundary_path}; no se recorta por PLOT.")

    # 3) Fusionar massing_graph.json
    massing = load_json(MASSING_GRAPH_PATH)
    m_nodes = massing.get("nodes", [])
    m_edges = massing.get("edges", [])

    used_ids = {n["id"] for n in nodes}
    id_map = {}  # id original massing -> id final

    # añadir nodos (con remapeo si colisionan)
    for n in m_nodes:
        orig_id = n.get("id")
        if orig_id is None:
            continue
        new_id = unique_node_id(str(orig_id), used_ids)
        if new_id != orig_id:
            n = dict(n)  # copia defensiva
            n["id"] = new_id
        id_map[orig_id] = new_id
        used_ids.add(new_id)
        nodes.append(n)

    # añadir edges (respetando {u,v} o {source,target})
    for e in m_edges:
        a, b, mode = endpoints_of(e)
        if a is None or b is None:
            continue
        a2 = id_map.get(a, a if a in used_ids else unique_node_id(str(a), used_ids))
        b2 = id_map.get(b, b if b in used_ids else unique_node_id(str(b), used_ids))
        if a2 not in used_ids:
            used_ids.add(a2)
        if b2 not in used_ids:
            used_ids.add(b2)
        e2 = dict(e)
        set_endpoints(e2, a2, b2, mode)
        edges.append(e2)

    # Guardar resultado en graph.json
    out_graph = {"nodes": nodes, "edges": edges}
    save_json(graph_path, out_graph)
    print(f"[merged] Graph actualizado y guardado en {graph_path}")
    print(f"[merged] Resumen: nodes={len(nodes)}  edges={len(edges)}")

if __name__ == "__main__":
    # Resolución de ruta del job
    job_arg = None
    if len(sys.argv) >= 2:
        job_arg = Path(sys.argv[1])
    else:
        env = os.environ.get("JOB_DIR")
        if env:
            job_arg = Path(env)
        else:
            # fallback al ejemplo del usuario (cámbialo si hace falta)
            job_arg = PROJECT_DIR / "knowledge" / "osm" / "osm_20250815_095943"

    run(job_arg.resolve())
