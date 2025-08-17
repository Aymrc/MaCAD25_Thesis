# -*- coding: utf-8 -*-
"""
empty_plot_exporter.py  (IronPython 2.7 compatible)

Construye knowledge/merge/empty_plot_graph.json **ya simplificado** a partir del
grafo original del job OSM:
- Lee JOB_DIR/graph.json (contexto original) y JOB_DIR/boundary.json (polígono de parcela).
- Mantiene sólo nodos FUERA de la parcela y asegura un nodo PLOT (id="PLOT").
- Mantiene aristas sólo si ambos extremos sobreviven (fuera->fuera).
- Luego simplifica (contracción de cadenas de calle con grado 2, como graph_builder.py).

Salida:
    knowledge/merge/empty_plot_graph.json  (versión simplificada)
"""

import os
import io
import json
import time
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HERE)
KNOWLEDGE_DIR = os.path.join(PROJECT_DIR, "knowledge")
OSM_ROOT = os.path.join(KNOWLEDGE_DIR, "osm")
MERGE_DIR = os.path.join(KNOWLEDGE_DIR, "merge")
OUT_PATH = os.path.join(MERGE_DIR, "empty_plot_graph.json")


def _ensure_dir(p):
    try:
        if not os.path.exists(p):
            os.makedirs(p)
    except:
        pass


def _latest_osm_dir(root):
    try:
        items = []
        for name in os.listdir(root):
            full = os.path.join(root, name)
            if os.path.isdir(full) and name.startswith("osm_"):
                items.append(full)
        if not items:
            return None
        items.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return items[0]
    except:
        return None


def _resolve_job_dir(job_dir):
    if job_dir and os.path.isdir(job_dir):
        return job_dir
    env = os.environ.get("JOB_DIR")
    if env and os.path.isdir(env):
        return env
    return _latest_osm_dir(OSM_ROOT)


def _file_ready(path, checks=3, interval=0.2):
    if not os.path.exists(path):
        return False
    last = None
    for _ in range(checks):
        try:
            st = os.stat(path)
            sig = (st.st_size, st.st_mtime)
        except:
            sig = None
        if last is not None and sig == last:
            return True
        last = sig
        time.sleep(interval)
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime) == last
    except:
        return False


def _read_text(path):
    f = open(path, "rb")
    try:
        b = f.read()
    finally:
        try: f.close()
        except: pass
    if b.startswith("\xef\xbb\xbf"):
        b = b[3:]
    try:
        return b.decode("utf-8")
    except:
        try:
            return str(b)
        except:
            return b


def _sanitize_json_text(s):
    s = re.sub(r'(?<!")\bNaN\b(?!")', 'null', s)
    s = re.sub(r'(?<!")\bInfinity\b(?!")', 'null', s)
    s = re.sub(r'(?<!")\b-Infinity\b(?!")', 'null', s)
    s = re.sub(r',\s*([\]\}])', r'\1', s)
    return s


def _load_json_robust(path, label):
    if not _file_ready(path, checks=4, interval=0.2):
        if not _file_ready(path, checks=2, interval=0.2):
            raise RuntimeError("%s not ready at: %s" % (label, path))
    if not os.path.exists(path):
        raise RuntimeError("%s not found at: %s" % (label, path))
    txt = _read_text(path)
    try:
        return json.loads(txt)
    except Exception:
        try:
            return json.loads(_sanitize_json_text(txt))
        except Exception as e2:
            raise RuntimeError("Failed to parse %s at %s: %s" % (label, path, str(e2)))


def _normalize_graph(raw):
    nodes = raw.get("nodes", []) or []
    e_raw = raw.get("edges", raw.get("links", [])) or []
    edges = []
    for e in e_raw:
        if e is None:
            continue
        if ("u" in e and "v" in e):
            d = {"u": e.get("u"), "v": e.get("v")}
            for k in e:
                if k not in ("u", "v"):
                    d[k] = e[k]
            edges.append(d)
        elif ("source" in e and "target" in e):
            d = {"u": e.get("source"), "v": e.get("target")}
            for k in e:
                if k not in ("source", "target"):
                    d[k] = e[k]
            edges.append(d)
    return {"nodes": nodes, "edges": edges}


def _node_xy(n):
    x = n.get("x", n.get("X"))
    y = n.get("y", n.get("Y"))
    try:
        return (float(x), float(y))
    except:
        return (None, None)


def _point_in_polygon(x, y, poly):
    n = len(poly or [])
    if n < 3:
        return False
    if poly[0] != poly[-1]:
        pts = list(poly) + [poly[0]]
    else:
        pts = poly
    inside = False
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]

        # borde (inclusivo)
        if (min(x1, x2) <= x <= max(x1, x2)) and (min(y1, y2) <= y <= max(y1, y2)):
            dx = (x2 - x1); dy = (y2 - y1)
            if abs(dx) >= abs(dy) and abs(dx) > 1e-12:
                t = (x - x1) / float(dx)
                y_line = y1 + t * dy
                if -1e-9 <= t <= 1.0 + 1e-9 and abs(y - y_line) <= 1e-6:
                    return True
            elif abs(dy) > 1e-12:
                t = (y - y1) / float(dy)
                x_line = x1 + t * dx
                if -1e-9 <= t <= 1.0 + 1e-9 and abs(x - x_line) <= 1e-6:
                    return True

        if ((y1 > y) != (y2 > y)):
            try:
                xinters = x1 + (y - y1) * (x2 - x1) / float(y2 - y1)
            except ZeroDivisionError:
                xinters = x1
            if x <= xinters:
                inside = not inside
    return inside


def _ensure_plot_node(nodes, boundary_xy):
    # Si ya existe "PLOT", conservarlo
    for n in nodes:
        if n.get("id") == "PLOT":
            return nodes
    # Si no, crearlo (centroide aproximado del polígono)
    cx = 0.0; cy = 0.0
    pts = boundary_xy or []
    if pts:
        for (px, py) in pts:
            try:
                cx += float(px); cy += float(py)
            except:
                pass
        m = float(len(pts))
        if m > 0:
            cx /= m; cy /= m
    nodes.append({"id": "PLOT", "label": "Plot", "x": cx, "y": cy, "type": "plot"})
    return nodes


# ----------------- Simplificador (estilo graph_builder.simplify_graph) -----------------
def _simplify_graph(data):
    # data: {"nodes":[{id,x,y,type,...}], "edges":[{u,v,type,distance,line,...}]}
    from collections import defaultdict, deque

    nodes = data.get("nodes", []) or []
    edges = data.get("edges", []) or []
    node_by_id = {}
    for n in nodes:
        nid = n.get("id")
        if nid is not None and nid not in node_by_id:
            node_by_id[nid] = n

    def ntype(nid):
        nd = node_by_id.get(nid) or {}
        return nd.get("type", "unknown")

    # Adyacencias
    adj_all = defaultdict(list)
    adj_street = defaultdict(list)
    edges_by_pair = defaultdict(list)
    for e in edges:
        u = e.get("u"); v = e.get("v")
        if u is None or v is None:
            continue
        adj_all[u].append(v)
        adj_all[v].append(u)
        edges_by_pair[(u, v)].append(e)
        edges_by_pair[(v, u)].append(e)
        if e.get("type") == "street":
            adj_street[u].append(v)
            adj_street[v].append(u)

    # Nodos de calle removibles: grado 2 y vecinos de tipo 'street'
    to_remove = set()
    for n in nodes:
        if n.get("type") != "street":
            continue
        nid = n.get("id")
        nbrs = adj_all.get(nid, [])
        ok = (len(nbrs) == 2)
        if ok:
            for x in nbrs:
                if ntype(x) != "street":
                    ok = False
                    break
        if ok:
            to_remove.add(nid)

    kept_nodes = set([n.get("id") for n in nodes if n.get("id") is not None]) - to_remove

    def _get_street_edge(a, b):
        for e in edges_by_pair.get((a, b), []):
            if e.get("type") == "street":
                return e
        return None

    def _oriented_line(a, b, e):
        line = e.get("line", []) or []
        if e.get("u") == a and e.get("v") == b:
            return line
        if e.get("u") == b and e.get("v") == a:
            r = list(line)
            r.reverse()
            return r
        return line

    # Contraer cadenas de calle
    visited_pairs = set()
    contracted_edges = []
    for start in list(kept_nodes):
        if ntype(start) != "street":
            continue
        for nbr in adj_street.get(start, []):
            prev = start
            curr = nbr
            total_dist = 0.0
            merged_line = []
            e0 = _get_street_edge(prev, curr)
            if e0:
                try:
                    total_dist += float(e0.get("distance", 0.0) or 0.0)
                except:
                    pass
                seg = _oriented_line(prev, curr, e0)
                merged_line.extend(seg)
            # avanzar por la cadena de grado-2
            while curr in to_remove:
                nbrs = adj_street.get(curr, [])
                if len(nbrs) != 2:
                    break
                nxt = nbrs[0] if nbrs[1] == prev else nbrs[1]
                e = _get_street_edge(curr, nxt)
                if e:
                    try:
                        total_dist += float(e.get("distance", 0.0) or 0.0)
                    except:
                        pass
                    seg = _oriented_line(curr, nxt, e)
                    if merged_line and seg and merged_line[-1] == seg[0]:
                        merged_line.extend(seg[1:])
                    else:
                        merged_line.extend(seg)
                prev, curr = curr, nxt
            end = curr
            if start == end:
                continue
            key = tuple(sorted([start, end]))
            if key in visited_pairs:
                continue
            visited_pairs.add(key)
            contracted_edges.append({
                "u": start, "v": end, "type": "street",
                "distance": total_dist, "line": merged_line
            })

    # Conservar aristas street directas entre kept-kept no cubiertas
    for e in edges:
        if e.get("type") != "street":
            continue
        u = e.get("u"); v = e.get("v")
        if u in kept_nodes and v in kept_nodes:
            key = tuple(sorted([u, v]))
            if key not in visited_pairs:
                visited_pairs.add(key)
                contracted_edges.append(e)

    # Reenganchar aristas de POI si su vértice street fue eliminado
    def _nearest_kept_street(start_removed):
        q = deque([start_removed])
        seen = set([start_removed])
        while q:
            cur = q.popleft()
            if (cur in kept_nodes) and (ntype(cur) == "street"):
                return cur
            for nxt in adj_street.get(cur, []):
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        return None

    other_edges = []
    for e in edges:
        if e.get("type") == "street":
            continue
        u = e.get("u"); v = e.get("v")
        tu = ntype(u); tv = ntype(v)
        is_poi_street = ({"building", "street"} == set([tu, tv])) or ({"green", "street"} == set([tu, tv]))
        if is_poi_street:
            poi = u if tu in ("building", "green") else v
            s   = v if poi == u else u
            if s in kept_nodes:
                other_edges.append(e)
            else:
                tgt = _nearest_kept_street(s)
                if tgt:
                    d = dict(e)
                    if tu in ("building", "green"):
                        d["u"], d["v"] = poi, tgt
                    else:
                        d["u"], d["v"] = tgt, poi
                    other_edges.append(d)
        else:
            if (u in kept_nodes) and (v in kept_nodes):
                other_edges.append(e)

    new_nodes = [n for n in nodes if n.get("id") in kept_nodes]
    return {"nodes": new_nodes, "edges": contracted_edges + other_edges}


# ----------------- Pipeline principal -----------------
def build(job_dir=None):
    job_dir = _resolve_job_dir(job_dir)
    if not job_dir or not os.path.isdir(job_dir):
        raise RuntimeError("JOB_DIR not set or invalid: %s" % str(job_dir))

    bpath = os.path.join(job_dir, "boundary.json")
    gpath = os.path.join(job_dir, "graph.json")

    boundary_xy = _load_json_robust(bpath, "boundary.json")
    raw = _load_json_robust(gpath, "original graph.json")
    G = _normalize_graph(raw)

    # Particionar nodos por boundary
    outside_ids = set()
    inside_ids = set()
    kept_nodes = []
    for n in G["nodes"]:
        nid = n.get("id")
        if nid is None:
            continue
        (x, y) = _node_xy(n)
        if (x is None) or (y is None):
            # Sin coords: conservamos como "fuera" para no romper hubs
            outside_ids.add(nid)
            kept_nodes.append(n)
            continue
        if _point_in_polygon(x, y, boundary_xy):
            inside_ids.add(nid)
        else:
            outside_ids.add(nid)
            kept_nodes.append(n)

    # Asegurar nodo PLOT
    kept_nodes = _ensure_plot_node(kept_nodes, boundary_xy)

    # Conservar sólo aristas cuyos extremos sobreviven (en formato u/v)
    kept_edges = []
    for e in G["edges"]:
        u = e.get("u"); v = e.get("v")
        if u is None or v is None:
            continue
        if (u in outside_ids) and (v in outside_ids):
            d = dict(e)
            d["u"] = u
            d["v"] = v
            # limpiar source/target si existían
            if "source" in d: del d["source"]
            if "target" in d: del d["target"]
            kept_edges.append(d)

    # Simplificar (contracción de calles grado-2, reenganche de POIs)
    simplified = _simplify_graph({"nodes": kept_nodes, "edges": kept_edges})

    meta = {
        "job_dir": job_dir,
        "source": "empty_plot_exporter",
        "note": "Outside-of-plot context, simplified (street chains contracted). "
                "Access edges to PLOT (si se necesitan) los añade masterplan_graph.py."
    }

    out = {"nodes": simplified["nodes"], "edges": simplified["edges"], "meta": meta}

    _ensure_dir(MERGE_DIR)
    # Escritura segura en UTF-8 (IronPython-friendly)
    txt = json.dumps(out, indent=2, ensure_ascii=False)
    f = io.open(OUT_PATH, "w", encoding="utf-8")
    try:
        try:
            f.write(unicode(txt))  # IronPython
        except NameError:
            f.write(txt)
    finally:
        try: f.close()
        except: pass

    try:
        print("[empty_plot_exporter] written (simplified):", OUT_PATH)
    except:
        pass
    return OUT_PATH


if __name__ == "__main__":
    build()