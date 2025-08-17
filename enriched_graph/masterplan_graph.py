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

# IronPython 2.7 / Python compatibility for type checks
try:
    basestring
except NameError:
    basestring = str
try:
    unicode
except NameError:
    unicode = str

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
    """Create directory if missing; ignore errors."""
    try:
        if not os.path.exists(p):
            os.makedirs(p)
    except:
        pass

def _file_ready(path, checks=4, interval=0.20):
    """Heuristic to wait until a file stops changing (size/mtime stable)."""
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
    """Read bytes; strip BOM; decode utf-8 with fallbacks."""
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
    """Make common non-JSON tokens parseable and remove trailing commas."""
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
            # Fallback: copy bytes and remove temp
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

# ---------- Geometry ----------
def point_in_polygon(x, y, poly):
    """Ray casting with on-edge detection."""
    n = len(poly)
    if n < 3: return False
    pts = (poly if poly and poly[0] == poly[-1] else list(poly) + [poly[0]])
    inside = False
    for i in range(len(pts)-1):
        x1,y1 = pts[i]; x2,y2 = pts[i+1]
        # On-edge check (both orientations)
        if (min(x1,x2) <= x <= max(x1,x2)) and (min(y1,y2) <= y <= max(y1,y2)):
            dx = (x2-x1); dy = (y2-y1)
            if abs(dx) >= abs(dy) and abs(dx) > 1e-12:
                t = (x-x1)/float(dx); y_line = y1 + t*dy
                if -1e-9 <= t <= 1+1e-9 and abs(y-y_line) <= 1e-6: return True
            elif abs(dy) > 1e-12:
                t = (y-y1)/float(dy); x_line = x1 + t*dx
                if -1e-9 <= t <= 1+1e-9 and abs(x-x_line) <= 1e-6: return True
        # Crossing number
        if ((y1 > y) != (y2 > y)):
            try: xinters = x1 + (y-y1)*(x2-x1)/float(y2-y1)
            except ZeroDivisionError: xinters = x1
            if x <= xinters: inside = not inside
    return inside

# ---------- Graph helpers ----------
def _normalize_graph(raw):
    """Normalize to {'nodes': [...], 'edges': [...]} using 'u'/'v' for edges."""
    nodes = raw.get("nodes", []) or []
    edges_raw = raw.get("edges", []) or raw.get("links", []) or []
    edges = []
    for e in edges_raw:
        if "u" in e and "v" in e:
            d = {"u": e.get("u"), "v": e.get("v")}
            for k in e:
                if k not in ("u", "v"):
                    d[k] = e[k]
            edges.append(d)
        elif "source" in e and "target" in e:
            d = {"u": e.get("source"), "v": e.get("target")}
            for k in e:
                if k not in ("source", "target"):
                    d[k] = e[k]
            edges.append(d)
    return {"nodes": nodes, "edges": edges}

def _node_xy(n):
    """Return (x,y) as floats if present, else (None,None)."""
    x = n.get("x", n.get("X")); y = n.get("y", n.get("Y"))
    try: return float(x), float(y)
    except: return None, None

def _index_nodes_by_id(nodes):
    """Build id -> node dictionary (first occurrence wins)."""
    idx = {}
    for n in nodes:
        nid = n.get("id")
        if nid is not None and nid not in idx:
            idx[nid] = n
    return idx

def _normalize_plot_id_in_place(nodes):
    """
    Ensure that if a plot-like node exists, its id is exactly 'PLOT'.
    This runs before adding massing nodes to seed existing_ids with 'PLOT'.
    """
    plot_ids = []
    for n in nodes:
        nid = n.get("id")
        if isinstance(nid, basestring):
            try:
                if "plot" in nid.lower():
                    plot_ids.append(nid)
            except:
                pass
    if not plot_ids:
        return
    # Prefer any exact-case 'PLOT'
    if "PLOT" in plot_ids:
        # Already normalized
        return
    # Otherwise rename the first plot-like node to 'PLOT'
    first_id = plot_ids[0]
    for n in nodes:
        if n.get("id") == first_id:
            n["id"] = "PLOT"
            break

def _safe_add_node(target_nodes, existing_ids, node):
    """
    Add node; if id collides:
      - for PLOT (case-insensitive) reuse/normalize to 'PLOT' (no duplicate),
      - otherwise clone with 'massing::' prefix.
    """
    nid = node.get("id")
    if nid is None:
        return None

    # Case-insensitive handling for PLOT
    nid_is_plot = False
    try:
        nid_is_plot = isinstance(nid, basestring) and nid.lower() == "plot"
    except:
        pass

    # If a PLOT-like node arrives and we already have any PLOT-like id, always reuse 'PLOT'
    if nid_is_plot:
        if "PLOT" in existing_ids:
            return "PLOT"
        # Check for case-insensitive existing plot id; normalize registry to 'PLOT'
        for eid in list(existing_ids):
            try:
                if isinstance(eid, basestring) and eid.lower() == "plot":
                    # Update the existing id in-place in target_nodes to 'PLOT'
                    for t in target_nodes:
                        if t.get("id") == eid:
                            t["id"] = "PLOT"
                            break
                    existing_ids.discard(eid)
                    existing_ids.add("PLOT")
                    return "PLOT"
            except:
                pass

    if nid in existing_ids:
        if nid_is_plot:
            # Reuse PLOT
            return "PLOT" if "PLOT" in existing_ids else nid
        # Non-plot collision: create a namespaced copy
        new_id = "massing::" + str(nid)
        clone = dict(node); clone["id"] = new_id
        target_nodes.append(clone); existing_ids.add(new_id)
        return new_id

    # Normal add
    # If this is a plot-like node but no plot exists yet, normalize its id to 'PLOT'
    if nid_is_plot and nid != "PLOT":
        node = dict(node)
        node["id"] = "PLOT"
        nid = "PLOT"

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
    """From original OSM graph + boundary, find outside nodes connected to inside."""
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
        u,v = e.get("u"), e.get("v")
        if u is None or v is None: continue
        u_in = (u in inside); v_in = (v in inside)
        if u_in and (v in outside): connectors.add(v)
        elif v_in and (u in outside): connectors.add(u)
    return connectors

def _find_plot_in_nodes(nodes):
    """Find a PLOT id, preferring exact 'PLOT' (case-insensitive fallback)."""
    for n in nodes:
        if n.get("id") == "PLOT":
            return "PLOT"
    for n in nodes:
        nid = n.get("id")
        try:
            if isinstance(nid, basestring) and nid.lower() == "plot":
                return nid
        except:
            pass
    return None

def _is_plot_id(nid):
    """Case/whitespace-insensitive test for plot-like ids."""
    if not isinstance(nid, basestring):
        return False
    try:
        return "plot" in nid.strip().lower()
    except:
        return False

def _merge_duplicate_plot_nodes(nodes, edges):
    """
    Enforce a single canonical plot node with id exactly 'PLOT'.
    - Rewire all edges to 'PLOT'
    - Merge missing attributes from duplicates into the canonical node
    - Drop any other plot-like nodes (e.g., 'massing::PLOT', 'Plot', ' plot ')
    """
    # Collect plot-like ids
    plot_like_ids = []
    for n in nodes:
        nid = n.get("id")
        if _is_plot_id(nid):
            plot_like_ids.append(nid)

    if not plot_like_ids:
        return nodes, edges, {"canonical": None, "merged": 0}

    # Ensure there is a canonical 'PLOT' node
    have_exact_plot = any((n.get("id") == "PLOT") for n in nodes)
    if not have_exact_plot:
        # Pick a representative and rename it to 'PLOT'
        rep_id = plot_like_ids[0]
        for n in nodes:
            if n.get("id") == rep_id:
                n["id"] = "PLOT"
                break

    # Identify canonical node reference
    canonical_node = None
    for n in nodes:
        if n.get("id") == "PLOT":
            canonical_node = n
            break

    # If for any reason canonical is still None, create one
    if canonical_node is None:
        canonical_node = {"id": "PLOT", "type": "plot", "label": "Plot"}
        nodes.append(canonical_node)

    # Recollect plot-like ids after possible rename
    plot_like_ids = []
    for n in nodes:
        nid = n.get("id")
        if _is_plot_id(nid):
            plot_like_ids.append(nid)

    # All plot-like ids except 'PLOT' must be removed
    to_remove = [nid for nid in plot_like_ids if nid != "PLOT"]

    # Merge attributes from duplicates into canonical (only fill missing keys)
    if to_remove:
        for n in list(nodes):
            nid = n.get("id")
            if nid in to_remove:
                # Copy over keys that canonical does not have yet
                for k, v in n.items():
                    if k == "id":
                        continue
                    if k not in canonical_node and v is not None:
                        canonical_node[k] = v

    # Rewire all edges to 'PLOT'
    for e in edges:
        u = e.get("u"); v = e.get("v")
        if u in to_remove or (isinstance(u, basestring) and _is_plot_id(u) and u != "PLOT"):
            e["u"] = "PLOT"
        if v in to_remove or (isinstance(v, basestring) and _is_plot_id(v) and v != "PLOT"):
            e["v"] = "PLOT"

    # Remove all non-canonical plot-like nodes
    filtered_nodes = [n for n in nodes if n.get("id") not in to_remove]

    # Deduplicate edges after rewiring
    seen = set(); filtered_edges = []
    for e in edges:
        try:
            k = json.dumps(e, sort_keys=True)
        except Exception:
            # Robust fallback for IronPython
            extras = sorted([(k2, e.get(k2)) for k2 in e if k2 not in ("u","v")])
            k = "%s|%s|%s" % (str(e.get("u")), str(e.get("v")), str(extras))
        if k not in seen:
            seen.add(k)
            filtered_edges.append(e)

    # Optional: final sanity print (safe if printing is available)
    try:
        # Count how many plot-like nodes remain and ensure single 'PLOT'
        remaining_plot_like = [n.get("id") for n in filtered_nodes if _is_plot_id(n.get("id"))]
        print("[masterplan_graph] postprocess: remaining plot-like ids:", remaining_plot_like)
    except:
        pass

    return filtered_nodes, filtered_edges, {"canonical": "PLOT", "merged": len(to_remove)}

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

    # Seed with empty plot content
    combined_nodes, combined_edges = [], []
    for n in E["nodes"]: combined_nodes.append(n)
    for e in E["edges"]: combined_edges.append(e)

    # Normalize any plot-like node id to 'PLOT' in the seed nodes
    _normalize_plot_id_in_place(combined_nodes)

    # Track existing ids to avoid node id collisions
    existing_ids = set([n.get("id") for n in combined_nodes if n.get("id") is not None])

    # Add massing nodes with collision handling
    collisions = {}
    for n in M["nodes"]:
        orig = n.get("id")
        new_id = _safe_add_node(combined_nodes, existing_ids, n)
        if orig is not None and new_id is not None and orig != new_id:
            collisions[orig] = new_id

    # Add massing edges, remapping endpoints if node ids collided
    for e in M["edges"]:
        u,v = e.get("u"), e.get("v")
        if u is None or v is None: continue
        if u in collisions: u = collisions[u]
        if v in collisions: v = collisions[v]
        _safe_add_edge(combined_edges, u, v, e)

    # Compute connectors from original graph and boundary
    connectors = _compute_connectors_from_original(job_dir, boundary_xy)

    # Find a PLOT id in merged nodes (prefer 'PLOT')
    plot_id = _find_plot_in_nodes(combined_nodes)
    if plot_id is None:
        # Last resort: look in massing nodes
        plot_id = _find_plot_in_nodes(M["nodes"])
    if plot_id is None:
        raise RuntimeError("PLOT node not found in merged graph")

    idx = _index_nodes_by_id(combined_nodes)
    if plot_id not in idx:
        raise RuntimeError("PLOT node missing from merged graph")

    # Add 'access' edges from outside connectors to PLOT
    px, py = _node_xy(idx.get(plot_id))
    added_edges = 0
    for nid in connectors:
        if nid not in idx:
            continue
        ux, uy = _node_xy(idx.get(nid))
        dist = 1.0
        if (px is not None) and (py is not None) and (ux is not None) and (uy is not None):
            try:
                dist = math.hypot(px-ux, py-uy)
            except:
                dist = 1.0
        _safe_add_edge(combined_edges, nid, plot_id, {"type":"access", "distance":dist})
        added_edges += 1

    # Post-process: enforce a single canonical 'PLOT'
    combined_nodes, combined_edges, plot_merge_info = _merge_duplicate_plot_nodes(combined_nodes, combined_edges)

    # Compose metadata
    meta = {
        "job_dir": job_dir,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "merge": {
            "empty_plot_graph": EMPTY_PLOT_PATH,
            "massing_graph": MASSING_PATH,
            "connectors_detected": int(len(connectors)),
            "connectors_added": int(added_edges)
        },
        "postprocess": {
            "canonical_plot_id": plot_merge_info.get("canonical"),
            "plot_nodes_merged": int(plot_merge_info.get("merged", 0))
        }
    }

    # Write output
    out = {"nodes": combined_nodes, "edges": combined_edges, "meta": meta}
    _atomic_write_json(OUT_PATH, out)

    try:
        print("[masterplan_graph] written:", OUT_PATH)
        print("[masterplan_graph] connectors:", len(connectors), "added edges:", added_edges)
        print("[masterplan_graph] plot merge:", meta["postprocess"])
    except:
        pass
    return OUT_PATH

if __name__ == "__main__":
    save_graph()
