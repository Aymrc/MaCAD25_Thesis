# -*- coding: utf-8 -*-
# massing_graph.py
#
# Build a graph (nodes + links) from Rhino geometry on MASSING/PLOT layers and save to JSON.
# - Breps -> buildings (level nodes + vertical edges)
# - Curves (Line/Polyline/PolyCurve/NurbsCurve) -> line network (endpoints & intersections as nodes + edges)
# - Optional building ↔ line-network connectors ("access" edges)
#
# Notes:
# - Python 2.7 / IronPython compatible (no 'nonlocal'; use list counter).
# - RhinoCommon: CurveCurve returns a CurveIntersections collection (not a tuple).

import os, uuid, json, time, string, math
import Rhino
import Rhino.Geometry as rg
import Rhino.Geometry.Intersect as rgi
import scriptcontext as sc
import rhinoscriptsyntax as rs

# =========================
# CONFIG
# =========================
LAYER_MASSING_ROOT = "MASSING"
LAYER_PLOT = "PLOT"

FLOOR_HEIGHT = 3.0
TOL = sc.doc.ModelAbsoluteTolerance or 0.001
MIN_PIECE_AREA = 0.0

# Line network (curves)
INCLUDE_LINE_NETWORK = True
DETECT_LINE_INTERSECTIONS = True             # compute curve-curve intersections
MAX_CURVE_PAIRS_FOR_INTERSECTIONS = 3000     # safety cap for O(n^2) pairs
CONNECT_BUILDINGS_TO_NEAREST_LINE = True     # add "access" edges from buildings to nearest line node
ACCESS_SEARCH_RADIUS = 50.0                  # max search radius (0 = unlimited)

# JSON output
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(CURRENT_DIR)   # parent of /rhino
KNOWLEDGE_PATH = os.path.join(PROJECT_DIR, "knowledge", "massing_graph.json")

# Minimal listener (optional)
ENABLE_LISTENER  = False
DEBOUNCE_SECONDS = 1.0

# Sticky keys
STK_LISTENER_ON   = "massing_graph_listener_on"
STK_DEBOUNCE_FLAG = "massing_graph_debounce_flag"

# Friendlier IDs
USE_CLEAN_NODE_IDS = True
INCLUDE_RANDOM_UID = False

# =========================
# utils
# =========================
def _ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        try:
            os.makedirs(d)
        except Exception as e:
            Rhino.RhinoApp.WriteLine("[massing_graph_export] Could not create folder: {0}".format(d))

def _to_brep(g):
    if isinstance(g, rg.Brep): return g
    if isinstance(g, rg.Extrusion):
        try:
            return g.ToBrep(True)
        except:
            return None
    return None

def _bbox_union(bb, other):
    if bb is None: return other
    if other is None: return bb
    try:
        return rg.BoundingBox.Union(bb, other)
    except:
        return bb

def _bbox_z(b):
    bb = b.GetBoundingBox(True)
    return bb.Min.Z, bb.Max.Z, bb

def _make_splitters(z_min, z_max, floor_h):
    height = z_max - z_min
    if height <= 0: return [], []
    num = int((height + 1e-9) // floor_h)
    levels = [z_min + i * floor_h for i in range(1, num + 1)]
    splitters = []
    for z in levels:
        plane = rg.Plane(rg.Point3d(0,0,z), rg.Vector3d.ZAxis)
        srf = rg.PlaneSurface(plane, rg.Interval(-1e6,1e6), rg.Interval(-1e6,1e6))
        splitters.append(rg.Brep.CreateFromSurface(srf))
    return levels, splitters

def _layer_indices_under(root_name):
    idxs = set()
    for lyr in sc.doc.Layers:
        full = lyr.FullPath if lyr.FullPath else lyr.Name
        if not full: continue
        if full == root_name or full.startswith(root_name + "::"):
            idxs.add(lyr.Index)
    return idxs

def _iter_massing_breps():
    idxs = _layer_indices_under(LAYER_MASSING_ROOT)
    if not idxs: return
    for obj in sc.doc.Objects:
        try:
            if obj.Attributes.LayerIndex in idxs:
                b = _to_brep(obj.Geometry)
                if b:
                    yield b
        except:
            pass

def _iter_massing_curves():
    """Yield curves under MASSING (LineCurve / PolylineCurve / PolyCurve / NurbsCurve / Curve)."""
    idxs = _layer_indices_under(LAYER_MASSING_ROOT)
    if not idxs: return
    for obj in sc.doc.Objects:
        try:
            if obj.Attributes.LayerIndex not in idxs:
                continue
            g = obj.Geometry
            if isinstance(g, rg.Curve):
                # drop degenerate curves
                if g.GetLength() > (TOL * 5.0):
                    yield g
        except:
            pass

def _get_plot_center():
    idxs = _layer_indices_under(LAYER_PLOT)
    if not idxs: return None
    pts = []
    for obj in sc.doc.Objects:
        try:
            if obj.Attributes.LayerIndex in idxs:
                bb = obj.Geometry.GetBoundingBox(True)
                pts.append(bb.Center)
        except:
            pass
    if not pts: return None
    sx = sum(pt.X for pt in pts); sy = sum(pt.Y for pt in pts); sz = sum(pt.Z for pt in pts)
    return rg.Point3d(sx/len(pts), sy/len(pts), sz/len(pts))

def _area_of_brep(b):
    try:
        amp = rg.AreaMassProperties.Compute(b)
        return amp.Area if amp else 0.0
    except:
        return 0.0

def _centroid_of_brep(b):
    try:
        amp = rg.AreaMassProperties.Compute(b)
        return amp.Centroid if amp else b.GetBoundingBox(True).Center
    except:
        bb = b.GetBoundingBox(True)
        return bb.Center

# ----- line-network helpers -----
def _curve_segments(curve):
    """Return simple segments from any curve type (polyline/polycurve expanded)."""
    try:
        if isinstance(curve, rg.PolyCurve):
            segs = curve.DuplicateSegments()
            if segs: return list(segs)
        if isinstance(curve, rg.PolylineCurve):
            segs = curve.DuplicateSegments()
            if segs: return list(segs)
        if isinstance(curve, rg.LineCurve):
            return [curve]
        return [curve]
    except:
        return [curve]

def _pt_key(pt, tol):
    """Quantize a point by tolerance to merge nearby nodes."""
    q = 1.0 / max(tol, 1e-9)
    return (int(round(pt.X * q)), int(round(pt.Y * q)), int(round(pt.Z * q)))

def _dist2(a, b):
    dx = a.X - b.X; dy = a.Y - b.Y; dz = a.Z - b.Z
    return dx*dx + dy*dy + dz*dz

# =========================
# geometry → graph
# =========================
def build_graph_from_active_doc(floor_h, tol):
    breps = list(_iter_massing_breps())
    nodes, edges = [], []
    meta = {"source":"ActiveDoc", "floor_height":float(floor_h), "tolerance":float(tol), "floor_levels": []}

    # --- 1) Buildings → level nodes + vertical edges ---
    if not breps:
        pass

    letters = iter(string.ascii_uppercase)
    b_letter = {}
    for b in breps:
        try:
            b_letter[id(b)] = next(letters)
        except StopIteration:
            b_letter[id(b)] = "X"
        except TypeError:
            try:
                b_letter[id(b)] = letters.next()  # IronPython 2.x style
            except StopIteration:
                b_letter[id(b)] = "X"

    zmins, zmaxs = [], []
    for b in breps:
        z0,z1,_ = _bbox_z(b); zmins.append(z0); zmaxs.append(z1)
    if zmins and zmaxs:
        z_min, z_max = min(zmins), max(zmaxs)
        level_Zs, splitters = _make_splitters(z_min, z_max, float(floor_h))
    else:
        level_Zs, splitters = [], []
    meta["floor_levels"] = level_Zs

    per_building_levels = {}
    for b in breps:
        bl = b_letter[id(b)]
        try:
            parts = rg.Brep.CreateBooleanSplit([b], splitters, tol) if splitters else [b]
        except:
            parts = [b]
        parts = list(parts) if parts else [b]
        accum = {}
        for p in parts:
            a = _area_of_brep(p)
            if MIN_PIECE_AREA > 0.0 and a < MIN_PIECE_AREA:
                continue
            z0,z1,bb = _bbox_z(p)
            zc = 0.5*(z0+z1)
            lvl = int((zc - (zmins and min(zmins) or 0.0))//float(floor_h))
            c = _centroid_of_brep(p)
            rec = accum.get(lvl)
            if rec is None:
                rec = {"area":0.0, "cx":0.0, "cy":0.0, "cz":0.0, "bbox":None, "z0":z0, "z1":z1}
            rec["area"] += a
            rec["cx"] += c.X * a; rec["cy"] += c.Y * a; rec["cz"] += c.Z * a
            rec["bbox"] = _bbox_union(rec["bbox"], bb)
            if z0 < rec["z0"]: rec["z0"] = z0
            if z1 > rec["z1"]: rec["z1"] = z1
            accum[lvl] = rec
        per_building_levels[bl] = accum

    node_id = {}
    building_ground_node = {}  # keep ground (or lowest) node per building for "access" edges
    for bl, levels_dict in per_building_levels.items():
        for lvl, rec in levels_dict.items():
            if rec["area"] <= 0.0:
                continue
            area = rec["area"] if rec["area"] != 0 else 1.0
            cx = rec["cx"]/area; cy = rec["cy"]/area; cz = rec["cz"]/area
            bb = rec["bbox"]

            clean_id = "{}-L{:02d}".format(bl, int(lvl))
            short_uid = uuid.uuid4().hex[:6] if INCLUDE_RANDOM_UID else None
            nid = clean_id if USE_CLEAN_NODE_IDS else "{}|L{:02d}|{}".format(bl, int(lvl), uuid.uuid4().hex[:6])

            node_obj = {
                "id": nid,
                "type": "level",
                "building_id": bl,
                "level": int(lvl),
                "z_span": [float(rec["z0"]), float(rec["z1"])],
                "centroid": [float(cx), float(cy), float(cz)],
                "bbox": [float(bb.Min.X), float(bb.Min.Y), float(bb.Min.Z),
                         float(bb.Max.X), float(bb.Max.Y), float(bb.Max.Z)],
                "area": float(rec["area"]),
                "label": "{} • L{:02d}".format(bl, int(lvl)),
                "clean_id": clean_id
            }
            if short_uid:
                node_obj["uid"] = short_uid

            nodes.append(node_obj)
            node_id[(bl, int(lvl))] = nid

        # vertical edges
        lvls = sorted([int(k) for k in levels_dict.keys()])
        for i in range(len(lvls)-1):
            l0, l1 = lvls[i], lvls[i+1]
            if (bl,l0) in node_id and (bl,l1) in node_id:
                edges.append({"source": node_id[(bl,l0)], "target": node_id[(bl,l1)],
                              "type": "vertical", "weight": 1.0})

        # keep L0 (or lowest) for access
        base = 0 if (bl,0) in node_id else (min(lvls) if lvls else None)
        if base is not None and (bl,base) in node_id:
            building_ground_node[bl] = node_id[(bl,base)]

    # ---------------------------------------------------------------------
    # // PLOT hub (disabled): we no longer connect buildings to a PLOT node
    # // because building connectivity will be resolved via the street graph.
    # //
    # // pc = _get_plot_center()
    # // if pc:
    # //     nodes.append({"id":"PLOT","type":"plot","centroid":[float(pc.X),float(pc.Y),float(pc.Z)]})
    # //     for bl, levels_dict in per_building_levels.items():
    # //         nid = node_id.get((bl,0))
    # //         if nid is None:
    # //             avail = sorted([int(k) for k in levels_dict.keys()])
    # //             if avail: nid = node_id.get((bl, avail[0]))
    # //         if nid:
    # //             edges.append({"source": nid, "target": "PLOT", "type": "plot", "weight": 1.0})
    # ---------------------------------------------------------------------

    # --- 2) Curves → line network (nodes + edges) ---
    line_node_ids = {}   # quantized point key -> node_id
    line_nodes_pts = {}  # node_id -> Point3d
    line_node_seq = [0]  # mutable counter (Py2.7 compatible)

    def _ensure_line_node(pt):
        k = _pt_key(pt, tol)
        nid = line_node_ids.get(k)
        if nid:
            return nid
        line_node_seq[0] += 1
        seq = line_node_seq[0]
        if USE_CLEAN_NODE_IDS:
            nid   = "N%05d" % seq
            label = "N%d" % seq
            clean = "N%d" % seq
        else:
            nid   = "N|%d|%s" % (seq, uuid.uuid4().hex[:6])
            label = "N%d" % seq
            clean = label
        nodes.append({
            "id": nid,
            "type": "line_node",
            "centroid": [float(pt.X), float(pt.Y), float(pt.Z)],
            "label": label,
            "clean_id": clean
        })
        line_node_ids[k] = nid
        line_nodes_pts[nid] = rg.Point3d(pt.X, pt.Y, pt.Z)
        return nid

    if INCLUDE_LINE_NETWORK:
        curves = list(_iter_massing_curves())

        # flatten to simple segments
        simple_curves = []
        for c in curves:
            simple_curves.extend(_curve_segments(c))

        # collect intersection parameters per curve (normalized [0..1])
        curve_to_t_hits = {}
        if DETECT_LINE_INTERSECTIONS and len(simple_curves) > 1:
            n = len(simple_curves)
            max_pairs = min(MAX_CURVE_PAIRS_FOR_INTERSECTIONS, n*(n-1)//2)
            cnt = 0
            for i in range(n):
                ci = simple_curves[i]
                di = ci.Domain
                for j in range(i+1, n):
                    if cnt >= max_pairs:
                        break
                    cj = simple_curves[j]
                    dj = cj.Domain

                    # RhinoCommon: returns CurveIntersections (not a (rc,events) tuple)
                    events = rgi.Intersection.CurveCurve(ci, cj, tol, tol)
                    cnt += 1
                    if events and events.Count > 0:
                        for ev in events:
                            # only intersection points (ignore infinite overlaps)
                            try:
                                ti = (ev.ParameterA - di.T0) / (di.T1 - di.T0) if (di.T1 - di.T0) != 0 else 0.0
                                tj = (ev.ParameterB - dj.T0) / (dj.T1 - dj.T0) if (dj.T1 - dj.T0) != 0 else 0.0
                            except:
                                continue
                            ti = max(0.0, min(1.0, float(ti)))
                            tj = max(0.0, min(1.0, float(tj)))
                            curve_to_t_hits.setdefault(ci, set()).add(ti)
                            curve_to_t_hits.setdefault(cj, set()).add(tj)

        # split each curve by its endpoints + intersections → create edges
        for c in simple_curves:
            d = c.Domain
            ts = [0.0, 1.0]
            if c in curve_to_t_hits:
                ts.extend(list(curve_to_t_hits[c]))
            ts = sorted(ts)
            ts_clean = []
            for t in ts:
                if not ts_clean or abs(t - ts_clean[-1]) > 1e-6:
                    ts_clean.append(t)
            for a, b in zip(ts_clean[:-1], ts_clean[1:]):
                if (b - a) < 1e-6:
                    continue
                ta = d.T0 + a * (d.T1 - d.T0)
                tb = d.T0 + b * (d.T1 - d.T0)
                pa = c.PointAt(ta)
                pb = c.PointAt(tb)
                if pa.DistanceTo(pb) < (tol * 2.0):
                    continue
                na = _ensure_line_node(pa)
                nb = _ensure_line_node(pb)
                edges.append({
                    "source": na,
                    "target": nb,
                    "type": "line_edge",
                    "weight": float(pa.DistanceTo(pb))
                })

    # --- 3) Connect buildings to nearest line node (access) ---
    if INCLUDE_LINE_NETWORK and CONNECT_BUILDINGS_TO_NEAREST_LINE and line_nodes_pts:
        ln_items = list(line_nodes_pts.items())
        for bl, nid in building_ground_node.items():
            bnode = next((n for n in nodes if n["id"] == nid), None)
            if not bnode: continue
            bp = rg.Point3d(bnode["centroid"][0], bnode["centroid"][1], bnode["centroid"][2])
            best = None
            best_d2 = None
            for lnid, lpt in ln_items:
                d2 = _dist2(bp, lpt)
                if ACCESS_SEARCH_RADIUS > 0.0 and d2 > (ACCESS_SEARCH_RADIUS * ACCESS_SEARCH_RADIUS):
                    continue
                if (best is None) or (d2 < best_d2):
                    best, best_d2 = lnid, d2
            if best:
                edges.append({
                    "source": nid,
                    "target": best,
                    "type": "access",
                    "weight": math.sqrt(best_d2)
                })

    return {"nodes": nodes, "links": edges, "meta": meta}

# =========================
# save
# =========================
def save_graph(path=KNOWLEDGE_PATH, floor_h=FLOOR_HEIGHT, tol=TOL):
    try:
        data = build_graph_from_active_doc(floor_h, tol)
        _ensure_dir(path)
        with open(path, "w") as f:
            json.dump({"nodes": data["nodes"], "links": data["links"], "meta": data["meta"]}, f, indent=2)
        Rhino.RhinoApp.WriteLine("[massing_graph_export] Saved graph to: {0}".format(path))
        Rhino.RhinoApp.WriteLine("[massing_graph_export] Nodes: {0}, Edges: {1}".format(len(data["nodes"]), len(data["links"])))
    except Exception as e:
        Rhino.RhinoApp.WriteLine("[massing_graph_export] Save error: {0}".format(e))

# =========================
# minimal listener (optional)
# =========================
_is_debouncing = False
def _debounce_trigger():
    global _is_debouncing
    if _is_debouncing:
        return
    _is_debouncing = True
    def _run_later():
        time.sleep(DEBOUNCE_SECONDS)
        try:
            save_graph()
            Rhino.RhinoApp.WriteLine("[massing_graph_export] Debounced export complete.")
        except Exception as e:
            Rhino.RhinoApp.WriteLine("[massing_graph_export] Export error: {0}".format(e))
        finally:
            global _is_debouncing
            _is_debouncing = False
    import threading
    t = threading.Thread(target=_run_later)
    try: t.setDaemon(True)
    except: pass
    t.start()

def _layer_name_from_event_obj(ev_obj):
    try:
        attrs = getattr(ev_obj, "Attributes", None) or getattr(ev_obj, "ObjectAttributes", None)
        if not attrs: return None
        idx = attrs.LayerIndex
        if idx is None or idx < 0: return None
        layer = sc.doc.Layers[idx]
        return layer.FullPath if layer and layer.FullPath else (layer.Name if layer else None)
    except:
        return None

def _layer_matches(lname, target):
    if not lname: return False
    return (lname == target) or lname.endswith("::" + target)

def _is_on_watched_layer(ev_obj):
    lname = _layer_name_from_event_obj(ev_obj)
    return _layer_matches(lname, LAYER_MASSING_ROOT) or _layer_matches(lname, LAYER_PLOT)

def _on_add(sender, e):
    if not ENABLE_LISTENER:
        return
    try:
        lname = _layer_name_from_event_obj(e.Object)
        if lname is None or _is_on_watched_layer(e.Object):
            _debounce_trigger()
    except:
        _debounce_trigger()

def _on_modify(sender, e):
    if not ENABLE_LISTENER:
        return
    try:
        lname = _layer_name_from_event_obj(e.Object)
        if lname is None or _is_on_watched_layer(e.Object):
            _debounce_trigger()
    except:
        _debounce_trigger()

def _on_replace(sender, e):
    if not ENABLE_LISTENER:
        return
    try:
        target = e.NewObject if hasattr(e, "NewObject") else e.Object
        lname = _layer_name_from_event_obj(target)
        if lname is None or _is_on_watched_layer(target):
            _debounce_trigger()
    except:
        _debounce_trigger()

def _on_delete(sender, e):
    if ENABLE_LISTENER:
        _debounce_trigger()

def setup_listener():
    if sc.sticky.get(STK_LISTENER_ON):
        Rhino.RhinoApp.WriteLine("[massing_graph_export] Listener already active.")
        return
    Rhino.RhinoDoc.AddRhinoObject += _on_add
    Rhino.RhinoDoc.ModifyObjectAttributes += _on_modify
    Rhino.RhinoDoc.ReplaceRhinoObject += _on_replace
    Rhino.RhinoDoc.DeleteRhinoObject += _on_delete
    sc.sticky[STK_LISTENER_ON] = True
    Rhino.RhinoApp.WriteLine("[massing_graph_export] Listener attached (MASSING/PLOT).")

def remove_listener():
    try: Rhino.RhinoDoc.AddRhinoObject -= _on_add
    except: pass
    try: Rhino.RhinoDoc.ModifyObjectAttributes -= _on_modify
    except: pass
    try: Rhino.RhinoDoc.ReplaceRhinoObject -= _on_replace
    except: pass
    try: Rhino.RhinoDoc.DeleteRhinoObject -= _on_delete
    except: pass
    sc.sticky[STK_LISTENER_ON] = False
    Rhino.RhinoApp.WriteLine("[massing_graph_export] Listener removed.")

# =========================
# entry point
# =========================
def main():
    save_graph()
    if ENABLE_LISTENER:
        setup_listener()

if __name__ == "__main__":
    main()
