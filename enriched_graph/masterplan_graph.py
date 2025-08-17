# -*- coding: utf-8 -*-
"""
masterplan_graph.py  (IronPython 2.7 compatible)

Merges:
  - knowledge/merge/empty_plot_graph.json
  - knowledge/massing_graph.json
Then re-connects outside "broken connector" nodes to the 'PLOT' node with 'access' edges,
based on the original JOB_DIR/graph.json + JOB_DIR/boundary.json.

Output:
  - knowledge/merge/masterplan_graph.json
"""

import os, json, time, math, re, tempfile

# ---------- Paths ----------
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HERE)
KNOWLEDGE_DIR = os.path.join(PROJECT_DIR, "knowledge")
OSM_ROOT = os.path.join(KNOWLEDGE_DIR, "osm")
MERGE_DIR = os.path.join(KNOWLEDGE_DIR, "merge")
EMPTY_PLOT_PATH = os.path.join(MERGE_DIR, "empty_plot_graph.json")
MASSING_PATH = os.path.join(KNOWLEDGE_DIR, "massing_graph.json")
OUT_PATH = os.path.join(MERGE_DIR, "masterplan_graph.json")

# ---------- Utils ----------
def _ensure_dir(p):
    try:
        if not os.path.exists(p):
            os.makedirs(p)
    except:
        pass

def _file_ready(path, checks=4, interval=0.20):
    if not os.path.exists(path):
        return False
    last = None
    for _ in range(checks):
        try:
            st = os.stat(path); sig = (st.st_size, st.st_mtime)
        except:
            sig = None
        if last is not None and sig == last:
            return True
        last = sig
        time.sleep(interval)
    try:
        st = os.stat(path); return (st.st_size, st.st_mtime) == last
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

def _extract_last_braced_json(s):
    last = s.rfind('}')
    if last == -1:
        return None
    depth = 0; i = last
    while i >= 0:
        c = s[i]
        if c == '}':
            depth += 1
        elif c == '{':
            depth -= 1
            if depth == 0:
                cand = s[i:last+1].strip()
                if cand.startswith('{') and cand.endswith('}'):
                    return cand
                break
        i -= 1
    return None

def _load_json_robust(path, label, retries=5, wait=0.25):
    if not _file_ready(path, checks=4, interval=0.20):
        for _ in range(retries):
            time.sleep(wait)
            if _file_ready(path, checks=3, interval=0.20):
                break
    if not os.path.exists(path):
        raise RuntimeError("%s not found at: %s" % (label, path))
    txt = _read_text(path)
    try:
        return json.loads(txt)
    except Exception:
        pass
    try:
        return json.loads(_sanitize_json_text(txt))
    except Exception:
        pass
    salvaged = _extract_last_braced_json(txt)
    if salvaged:
        try:
            return json.loads(_sanitize_json_text(salvaged))
        except Exception as e3:
            raise RuntimeError("Failed to parse %s at %s (salvaged): %s" % (label, path, str(e3)))
    raise RuntimeError("Failed to parse %s at %s: Expecting valid single JSON object/array" % (label, path))

def _atomic_write_json(path, data):
    """Windows/IronPython-safe UTF-8 atomic-ish write."""
    _ensure_dir(os.path.dirname(path))
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_masterplan_", dir=d)
    ok = False
    try:
        # open temp file in binary and write UTF-8 bytes explicitly
        import io
        f = io.open(tmp, "w", encoding="utf-8")
        try:
            txt = json.dumps(data, indent=2, ensure_ascii=False)
            try:
                f.write(unicode(txt))  # IronPython
            except NameError:
                f.write(txt)
            f.flush()
            try: os.fsync(f.fileno())
            except: pass
        finally:
            try: f.close()
            except: pass

        if os.path.exists(path):
            try: os.remove(path)
            except: pass
        try:
            os.rename(tmp, path)
            ok = True
        except Exception:
            # fallback copy
            src = open(tmp, "rb"); dst = open(path, "wb")
            try: dst.write(src.read())
            finally:
                try: src.close()
                except: pass
                try: dst.close()
                except: pass
            ok = True
            try: os.remove(tmp)
            except: pass
    finally:
        if not ok:
            try:
                if os.path.exists(tmp): os.remove(tmp)
            except: pass

def _latest_osm_dir(root):
    try:
        items = []
        for name in os.listdir(root):
            full = os.path.join(root, name)
            if os.path.isdir(full) and name.startswith("osm_"):
                items.append(full)
        if not items: return None
        items.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return items[0]
    except:
        return None

def _resolve_job_dir():
    env = os.environ.get("JOB_DIR")
    if env and os.path.isdir(env):
        return env
    return _latest_osm_dir(OSM_ROOT)

# ---------- Geometry ----------
def point_in_polygon(x, y, poly):
    n = len(poly)
    if n < 3: return False
    pts = (poly if poly and poly[0] == poly[-1] else list(poly) + [poly[0]])
    inside = False
    for i in range(len(pts)-1):
        x1,y1 = pts[i]; x2,y2 = pts[i+1]
        if (min(x1,x2) <= x <= max(x1,x2)) and (min(y1,y2) <= y <= max(y1,y2)):
            dx = (x2-x1); dy = (y2-y1)
            if abs(dx) >= abs(dy) and abs(dx) > 1e-12:
                t = (x-x1)/float(dx); y_line = y1 + t*dy
                if -1e-9 <= t <= 1+1e-9 and abs(y-y_line) <= 1e-6: return True
            elif abs(dy) > 1e-12:
                t = (y-y1)/float(dy); x_line = x1 + t*dx
                if -1e-9 <= t <= 1+1e-9 and abs(x-x_line) <= 1e-6: return True
        if ((y1 > y) != (y2 > y)):
            try: xinters = x1 + (y-y1)*(x2-x1)/float(y2-y1)
            except ZeroDivisionError: xinters = x1
            if x <= xinters: inside = not inside
    return inside

# ---------- Graph helpers ----------
def _normalize_graph(raw):
    nodes = raw.get("nodes", []) or []
    edges_raw = raw.get("edges", []) or raw.get("links", []) or []
    edges = []
    for e in edges_raw:
        if "u" in e and "v" in e:
            d = {"u": e.get("u"), "v": e.get("v")}
            for k in e:
                if k not in ("u", "v"): d[k] = e[k]
            edges.append(d)
        elif "source" in e and "target" in e:
            d = {"u": e.get("source"), "v": e.get("target")}
            for k in e:
                if k not in ("source", "target"): d[k] = e[k]
            edges.append(d)
    return {"nodes": nodes, "edges": edges}

def _node_xy(n):
    x = n.get("x", n.get("X")); y = n.get("y", n.get("Y"))
    try: return float(x), float(y)
    except: return None, None

def _index_nodes_by_id(nodes):
    idx = {}
    for n in nodes:
        nid = n.get("id")
        if nid is not None and nid not in idx:
            idx[nid] = n
    return idx

def _is_plot_id(x):
    try:
        return isinstance(x, basestring) and (x.lower() == "plot")
    except:
        try:
            return isinstance(x, str) and (x.lower() == "plot")
        except:
            return False

def _find_plot_in_nodes(nodes):
    for n in nodes:
        nid = n.get("id")
        if _is_plot_id(nid):
            return nid
    return None

# ---------- Main ----------
def save_graph():
    job_dir = _resolve_job_dir()
    if not job_dir or not os.path.isdir(job_dir):
        raise RuntimeError("JOB_DIR not set or invalid: " + str(job_dir))

    boundary_xy = _load_json_robust(os.path.join(job_dir, "boundary.json"), "boundary.json")
    empty_raw   = _load_json_robust(EMPTY_PLOT_PATH, "empty_plot_graph.json")
    massing_raw = _load_json_robust(MASSING_PATH, "massing_graph.json")

    E = _normalize_graph(empty_raw)
    M = _normalize_graph(massing_raw)

    combined_nodes, combined_edges = [], []
    for n in E["nodes"]: combined_nodes.append(n)
    for e in E["edges"]: combined_edges.append(e)

    existing_ids = set([n.get("id") for n in combined_nodes if n.get("id") is not None])

    # --- Elegimos un PLOT canónico (preferimos el de E) ---
    canonical_plot = _find_plot_in_nodes(E["nodes"]) or _find_plot_in_nodes(M["nodes"]) or "PLOT"

    collisions = {}
    for n in M["nodes"]:
        orig = n.get("id")
        if orig is None:
            continue
        # Si el nodo de massing es "plot" (cualquier case), NO lo añadimos: lo remapeamos al canónico
        if _is_plot_id(orig):
            collisions[orig] = canonical_plot
            continue
        # resto igual que tu versión
        new_id = None
        if orig in existing_ids:
            new_id = "massing::" + str(orig)
            clone = dict(n); clone["id"] = new_id
            combined_nodes.append(clone); existing_ids.add(new_id)
        else:
            combined_nodes.append(n); existing_ids.add(orig); new_id = orig
        if orig is not None and new_id is not None and orig != new_id:
            collisions[orig] = new_id

    # Si por cualquier motivo hay variantes "plot" distintas al canónico en combined_nodes, las eliminamos y remapeamos
    to_kill = []
    for n in combined_nodes:
        nid = n.get("id")
        if _is_plot_id(nid) and nid != canonical_plot:
            to_kill.append(nid)
    if to_kill:
        combined_nodes = [n for n in combined_nodes if n.get("id") not in to_kill]
        for nid in to_kill:
            try: existing_ids.remove(nid)
            except: pass
            collisions[nid] = canonical_plot

    # Añadimos aristas de massing remapeando colisiones y PLOT canónico
    for e in M["edges"]:
        u = e.get("u"); v = e.get("v")
        if u is None or v is None: continue
        if _is_plot_id(u): u = canonical_plot
        if _is_plot_id(v): v = canonical_plot
        if u in collisions: u = collisions[u]
        if v in collisions: v = collisions[v]
        _safe_add_edge(combined_edges, u, v, e)

    # --- Remapeo final de TODAS las aristas ya acumuladas (incluidas las de E) para asegurar PLOT único ---
    norm_edges = []
    for e in combined_edges:
        u = e.get("u", e.get("source")); v = e.get("v", e.get("target"))
        if u is None or v is None: continue
        if _is_plot_id(u): u = canonical_plot
        if _is_plot_id(v): v = canonical_plot
        if u in collisions: u = collisions[u]
        if v in collisions: v = collisions[v]
        attrs = dict(e)
        for k in ("u","v","source","target"): attrs.pop(k, None)
        norm_edges.append({"u": u, "v": v, **attrs})
    combined_edges = norm_edges

    # Build connectors from original graph + boundary (se mantienen igual)
    def _compute_connectors_from_original(job_dir, boundary_xy):
        gpath = os.path.join(job_dir, "graph.json")
        raw = _load_json_robust(gpath, "original OSM graph.json")
        G = _normalize_graph(raw)
        inside, outside = set(), set()
        for n in G["nodes"]:
            nid = n.get("id")
            if nid is None: continue
            x,y = _node_xy(n)
            if x is None or y is None: continue
            (inside if point_in_polygon(x,y,boundary_xy) else outside).add(nid)
        connectors = set()
        for e in G["edges"]:
            uu,vv = e.get("u"), e.get("v")
            if uu is None or vv is None: continue
            u_in = (uu in inside); v_in = (vv in inside)
            if u_in and (vv in outside): connectors.add(vv)
            elif v_in and (uu in outside): connectors.add(uu)
        return connectors

    connectors = _compute_connectors_from_original(job_dir, boundary_xy)

    idx = _index_nodes_by_id(combined_nodes)
    if canonical_plot not in idx:
        raise RuntimeError("PLOT node missing from merged graph")

    px, py = _node_xy(idx.get(canonical_plot))
    added_edges = 0
    for nid in connectors:
        if nid not in idx:
            continue
        ux, uy = _node_xy(idx.get(nid))
        dist = 1.0
        if (px is not None) and (py is not None) and (ux is not None) and (uy is not None):
            try: dist = math.hypot(px-ux, py-uy)
            except: dist = 1.0
        _safe_add_edge(combined_edges, nid, canonical_plot, {"type":"access", "distance":dist})
        added_edges += 1

    meta = {
        "job_dir": job_dir,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "merge": {
            "empty_plot_graph": EMPTY_PLOT_PATH,
            "massing_graph": MASSING_PATH,
            "connectors_detected": int(len(connectors)),
            "connectors_added": int(added_edges),
            "plot_id": canonical_plot
        }
    }

    out = {"nodes": combined_nodes, "edges": combined_edges, "meta": meta}
    _atomic_write_json(OUT_PATH, out)

    try:
        print("[masterplan_graph] written:", OUT_PATH)
        print("[masterplan_graph] connectors:", len(connectors), "added edges:", added_edges)
    except:
        pass
    return OUT_PATH

if __name__ == "__main__":
    save_graph()
