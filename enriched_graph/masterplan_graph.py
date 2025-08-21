# -*- coding: utf-8 -*-
"""
masterplan_graph.py  (IronPython 2.7 compatible) — Option B (no PLOT hub)

Merges:
  - knowledge/merge/empty_plot_graph.json      (outside-only, open boundary)
  - knowledge/massing_graph.json               (massing + internal streets)

Then, instead of creating/using a synthetic 'PLOT' hub, it:
  - Finds "connector" street nodes: outside nodes that were adjacent to inside
    nodes in the original OSM graph (JOB_DIR/graph.json + JOB_DIR/boundary.json).
  - For each connector, connects it with an 'access' edge to the nearest
    massing node (type 'street' or 'level') within MAX_CONNECT_DIST.
  - If no nearby massing node is found, the connector remains a dead-end.

Output:
  - knowledge/merge/masterplan_graph.json
"""

import os, json, time, math, re, tempfile

# ---------------- Configuration ----------------
# Max distance (in the same XY units as your graphs) to attach connectors.
MAX_CONNECT_DIST = 50.0

# Prefer connecting to massing street nodes; if none nearby, allow level nodes.
PREFER_MASSING_STREETS = True

# ---------------- Py2 compatibility ----------------
try:
    basestring
except NameError:
    basestring = str
try:
    unicode
except NameError:
    unicode = str

# ---------------- Paths ----------------
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HERE)
KNOWLEDGE_DIR = os.path.join(PROJECT_DIR, "knowledge")
OSM_ROOT = os.path.join(KNOWLEDGE_DIR, "osm")
MERGE_DIR = os.path.join(KNOWLEDGE_DIR, "merge")
EMPTY_PLOT_PATH = os.path.join(MERGE_DIR, "empty_plot_graph.json")
MASSING_PATH = os.path.join(KNOWLEDGE_DIR, "massing_graph.json")
OUT_PATH = os.path.join(MERGE_DIR, "masterplan_graph.json")

# ---------------- I/O utils ----------------
def _ensure_dir(p):
    try:
        if not os.path.exists(p):
            os.makedirs(p)
    except:
        pass

def _file_ready(path, checks=4, interval=0.20):
    """Heuristic: consider file ready if size/mtime stop changing."""
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
    """Read bytes; strip UTF-8 BOM; decode UTF-8 with fallback."""
    f = open(path, "rb")
    try:
        b = f.read()
    finally:
        try: f.close()
        except: pass
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    try:
        return b.decode("utf-8")
    except:
        try:
            return str(b)
        except:
            return b

def _sanitize_json_text(s):
    """Tolerate NaN/Infinity and trailing commas."""
    s = re.sub(r'(?<!")\bNaN\b(?!")', 'null', s)
    s = re.sub(r'(?<!")\bInfinity\b(?!")', 'null', s)
    s = re.sub(r'(?<!")\b-Infinity\b(?!")', 'null', s)
    s = re.sub(r',\s*([\]\}])', r'\1', s)
    return s

def _extract_last_braced_json(s):
    """Try to salvage the last {...} block from noisy logs."""
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
    """Robust JSON loader tolerant to late writes and minor format issues."""
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
        import io
        f = io.open(tmp, "w", encoding="utf-8")
        try:
            txt = json.dumps(data, indent=2, ensure_ascii=False)
            try: f.write(unicode(txt))  # IronPython 2.7
            except NameError: f.write(txt)
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
            # Fallback copy
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
    """Pick most recently modified 'osm_*' directory."""
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
    """Resolve JOB_DIR from env or fall back to latest osm_* folder."""
    env = os.environ.get("JOB_DIR")
    if env and os.path.isdir(env):
        return env
    return _latest_osm_dir(OSM_ROOT)

# ---------------- Geometry helpers ----------------
def point_in_polygon(x, y, poly):
    """Ray casting with on-edge detection (inclusive on edges)."""
    n = len(poly)
    if n < 3: return False
    pts = (poly if poly and poly[0] == poly[-1] else list(poly) + [poly[0]])
    inside = False
    for i in range(len(pts)-1):
        x1,y1 = pts[i]; x2,y2 = pts[i+1]
        # on-edge test
        if (min(x1,x2) <= x <= max(x1,x2)) and (min(y1,y2) <= y <= max(y1,y2)):
            dx = (x2-x1); dy = (y2-y1)
            if abs(dx) >= abs(dy) and abs(dx) > 1e-12:
                t = (x-x1)/float(dx); y_line = y1 + t*dy
                if -1e-9 <= t <= 1+1e-9 and abs(y-y_line) <= 1e-6: return True
            elif abs(dy) > 1e-12:
                t = (y-y1)/float(dy); x_line = x1 + t*dx
                if -1e-9 <= t <= 1+1e-9 and abs(x-x_line) <= 1e-6: return True
        # crossing number
        if ((y1 > y) != (y2 > y)):
            try: xinters = x1 + (y-y1)*(x2-x1)/float(y2-y1)
            except ZeroDivisionError: xinters = x1
            if x <= xinters: inside = not inside
    return inside

# ---------------- Graph helpers ----------------
def _normalize_graph(raw):
    """Normalize to {'nodes': [...], 'edges': [...]} using 'u'/'v' for edges."""
    nodes = raw.get("nodes", []) or []
    edges_raw = raw.get("edges", []) or raw.get("links", []) or []
    edges = []
    for e in edges_raw:
        if e is None: continue
        if "u" in e and "v" in e:
            d = {"u": e.get("u"), "v": e.get("v")}
            for k in e:
                if k not in ("u","v"):
                    d[k] = e[k]
            edges.append(d)
        elif "source" in e and "target" in e:
            d = {"u": e.get("source"), "v": e.get("target")}
            for k in e:
                if k not in ("source","target"):
                    d[k] = e[k]
            edges.append(d)
    return {"nodes": nodes, "edges": edges}

def _index_nodes_by_id(nodes):
    """Build id -> node dict (first occurrence wins)."""
    idx = {}
    for n in nodes:
        nid = n.get("id")
        if nid is not None and nid not in idx:
            idx[nid] = n
    return idx

def _node_xy(node):
    """
    Return (x,y) for a node:
      - If 'x'/'y' exist, use them.
      - Else if 'centroid' exists (e.g., massing 'level' nodes), use centroid XY.
      - Else if 'X'/'Y' exist, use those.
      - Otherwise (None, None).
    """
    if node is None: return (None, None)
    x = node.get("x", node.get("X"))
    y = node.get("y", node.get("Y"))
    if x is not None and y is not None:
        try: return float(x), float(y)
        except: return (None, None)
    c = node.get("centroid")
    if isinstance(c, (list, tuple)) and len(c) >= 2:
        try: return float(c[0]), float(c[1])
        except: return (None, None)
    return (None, None)

def _safe_add_node(target_nodes, existing_ids, node, namespace="massing::"):
    """
    Add node; on id collision, clone with namespaced id.
    Returns the (possibly new) id inserted.
    """
    nid = node.get("id")
    if nid is None:
        return None
    if nid in existing_ids:
        new_id = namespace + str(nid)
        clone = dict(node); clone["id"] = new_id
        target_nodes.append(clone); existing_ids.add(new_id)
        return new_id
    target_nodes.append(node); existing_ids.add(nid)
    return nid

def _safe_add_edge(target_edges, u, v, attrs):
    """Append edge with 'u','v' and copy other attributes, ignoring source/target."""
    d = {"u": u, "v": v}
    if attrs:
        for k in attrs:
            if k not in ("u","v","source","target"):
                d[k] = attrs[k]
    target_edges.append(d)

def _compute_connectors_from_original(job_dir, boundary_xy):
    """
    From the original OSM graph + boundary, find OUTSIDE nodes that had an edge to an INSIDE node.
    These are the "connectors" we need to reattach.
    """
    gpath = os.path.join(job_dir, "graph.json")
    raw = _load_json_robust(gpath, "original OSM graph.json")
    G = _normalize_graph(raw)

    inside, outside = set(), set()
    for n in G["nodes"]:
        nid = n.get("id")
        if nid is None: continue
        x,y = _node_xy(n)
        if x is None or y is None:  # no coords → treat as outside to avoid dropping hubs
            outside.add(nid)
            continue
        if point_in_polygon(x, y, boundary_xy):
            inside.add(nid)
        else:
            outside.add(nid)

    connectors = set()
    for e in G["edges"]:
        u, v = e.get("u"), e.get("v")
        if u is None or v is None: continue
        u_in = (u in inside); v_in = (v in inside)
        if u_in and (v in outside): connectors.add(v)
        elif v_in and (u in outside): connectors.add(u)
    return connectors

# ---------------- Main ----------------
def save_graph():
    job_dir = _resolve_job_dir()
    if not job_dir or not os.path.isdir(job_dir):
        raise RuntimeError("JOB_DIR not set or invalid: " + str(job_dir))

    boundary_xy = _load_json_robust(os.path.join(job_dir, "boundary.json"), "boundary.json")
    empty_raw   = _load_json_robust(EMPTY_PLOT_PATH, "empty_plot_graph.json")
    massing_raw = _load_json_robust(MASSING_PATH, "massing_graph.json")

    E = _normalize_graph(empty_raw)   # outside-only, open boundary (no PLOT)
    M = _normalize_graph(massing_raw)

    # Seed with empty-plot content
    combined_nodes, combined_edges = [], []
    for n in E["nodes"]: combined_nodes.append(n)
    for e in E["edges"]: combined_edges.append(e)

    # Track existing ids to avoid collisions
    existing_ids = set([n.get("id") for n in combined_nodes if n.get("id") is not None])

    # Add massing nodes with collision handling
    collisions = {}
    for n in M["nodes"]:
        orig = n.get("id")
        new_id = _safe_add_node(combined_nodes, existing_ids, n, namespace="massing::")
        if orig is not None and new_id is not None and orig != new_id:
            collisions[orig] = new_id

    # Add massing edges, remapping endpoints if node ids collided
    for e in M["edges"]:
        u,v = e.get("u"), e.get("v")
        if u is None or v is None: continue
        if u in collisions: u = collisions[u]
        if v in collisions: v = collisions[v]
        _safe_add_edge(combined_edges, u, v, e)

    # Build node index after merge
    idx = _index_nodes_by_id(combined_nodes)

    # Identify massing candidate targets and their XY
    # Prefer massing 'street' nodes first (if requested), else 'level' nodes.
    massing_streets = []
    massing_levels  = []
    for n in combined_nodes:
        # Heuristic: massing-origin nodes can be recognized either by type or by id namespace.
        t = n.get("type")
        nid = n.get("id")
        is_massing_namespaced = isinstance(nid, basestring) and nid.startswith("massing::")
        # We keep any street/level regardless of namespace — if it came from E it likely won't be type 'level'.
        if t == "street":
            massing_streets.append(n)
        elif t == "level":
            massing_levels.append(n)

    # Prepare candidate lists according to preference
    candidate_lists = []
    if PREFER_MASSING_STREETS:
        candidate_lists = [massing_streets, massing_levels]
    else:
        candidate_lists = [massing_levels, massing_streets]

    # Compute connectors (outside nodes that used to touch inside)
    connectors = _compute_connectors_from_original(job_dir, boundary_xy)

    # Helper: nearest massing node within MAX_CONNECT_DIST
    def nearest_massing(nid):
        src_node = idx.get(nid)
        if not src_node:
            return (None, None)
        ux, uy = _node_xy(src_node)
        if ux is None or uy is None:
            return (None, None)
        best_id = None
        best_d  = MAX_CONNECT_DIST
        # Search in preferred order
        for group in candidate_lists:
            for tgt in group:
                tid = tgt.get("id")
                if tid is None or tid == nid:
                    continue
                tx, ty = _node_xy(tgt)
                if tx is None or ty is None:
                    continue
                try:
                    d = math.hypot(ux - tx, uy - ty)
                except:
                    continue
                if d < best_d:
                    best_d = d
                    best_id = tid
            if best_id is not None:
                break  # already found the best within preferred class
        if best_id is None:
            return (None, None)
        return (best_id, best_d)

    # Attach connectors to nearest massing node (if within distance)
    added_edges = 0
    for nid in connectors:
        if nid not in idx:
            continue
        tgt_id, dist = nearest_massing(nid)
        if tgt_id is None:
            continue  # dead-end (no nearby massing node)
        _safe_add_edge(combined_edges, nid, tgt_id, {"type": "access", "distance": float(dist)})
        added_edges += 1

    # Deduplicate edges (since we might add duplicates when re-running)
    seen = set(); filtered_edges = []
    for e in combined_edges:
        try:
            k = json.dumps(e, sort_keys=True)
        except Exception:
            extras = sorted([(k2, e.get(k2)) for k2 in e if k2 not in ("u","v")])
            k = "%s|%s|%s" % (str(e.get("u")), str(e.get("v")), str(extras))
        if k not in seen:
            seen.add(k)
            filtered_edges.append(e)

    # Compose metadata
    meta = {
        "job_dir": job_dir,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "merge": {
            "empty_plot_graph": EMPTY_PLOT_PATH,
            "massing_graph": MASSING_PATH,
            "connectors_detected": int(len(connectors)),
            "connectors_attached": int(added_edges),
            "max_connect_dist": float(MAX_CONNECT_DIST),
            "prefer_massing_streets": bool(PREFER_MASSING_STREETS)
        }
    }

    # Write output
    out = {"nodes": combined_nodes, "edges": filtered_edges, "meta": meta}
    _atomic_write_json(OUT_PATH, out)

    try:
        print("[masterplan_graph] written:", OUT_PATH)
        print("[masterplan_graph] connectors:", len(connectors), "attached:", added_edges,
              "max_dist:", MAX_CONNECT_DIST, "prefer_streets:", PREFER_MASSING_STREETS)
    except:
        pass

    return OUT_PATH

if __name__ == "__main__":
    save_graph()
