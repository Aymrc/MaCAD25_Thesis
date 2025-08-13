# -*- coding: utf-8 -*-
# massing_graph_export.py
# Build a graph (nodes + links) from Rhino geometry on MASSING/PLOT layers and save to JSON.
# - No conduit / no viewport preview
# - Can run once (manual) or attach a debounced listener to auto-export on changes.

import os, uuid, json, time, string
import Rhino
import Rhino.Geometry as rg
import Rhino.Geometry.Intersect as rgi  # kept in case you later re-add sections
import scriptcontext as sc
import rhinoscriptsyntax as rs

# =========================
# CONFIG
# =========================
LAYER_MASSING_ROOT = "MASSING"
LAYER_PLOT         = "PLOT"

FLOOR_HEIGHT       = 3.0
TOL                = sc.doc.ModelAbsoluteTolerance or 0.001
MIN_PIECE_AREA     = 0.0

# Where to write the JSON graph
REPO_ROOT      = r"C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis"
KNOWLEDGE_PATH = os.path.join(REPO_ROOT, "knowledge", "massing_graph.json")

# Listener settings
ENABLE_LISTENER  = False     # <- set True to auto-export on geometry changes
DEBOUNCE_SECONDS = 1.0

# Sticky keys (for internal state)
STK_LISTENER_ON   = "massing_graph_listener_on"
STK_DEBOUNCE_FLAG = "massing_graph_debounce_flag"
# =========================


# -------- utils --------
def _ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d)

def _to_brep(g):
    if isinstance(g, rg.Brep): return g
    if isinstance(g, rg.Extrusion): return g.ToBrep(True)
    return None

def _bbox_union(bb, other):
    if bb is None: return other
    if other is None: return bb
    return rg.BoundingBox.Union(bb, other)

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
        if obj.Attributes.LayerIndex in idxs:
            b = _to_brep(obj.Geometry)
            if b: yield b

def _get_plot_center():
    idxs = _layer_indices_under(LAYER_PLOT)
    if not idxs: return None
    pts = []
    for obj in sc.doc.Objects:
        if obj.Attributes.LayerIndex in idxs:
            try:
                bb = obj.Geometry.GetBoundingBox(True)
                pts.append(bb.Center)
            except: pass
    if not pts: return None
    sx = sum(pt.X for pt in pts); sy = sum(pt.Y for pt in pts); sz = sum(pt.Z for pt in pts)
    return rg.Point3d(sx/len(pts), sy/len(pts), sz/len(pts))

def _area_of_brep(b):
    amp = rg.AreaMassProperties.Compute(b)
    return amp.Area if amp else 0.0

def _centroid_of_brep(b):
    amp = rg.AreaMassProperties.Compute(b)
    return amp.Centroid if amp else b.GetBoundingBox(True).Center


# -------- geometry → graph --------
def build_graph_from_active_doc(floor_h, tol):
    breps = list(_iter_massing_breps())
    nodes, edges = [], []
    if not breps:
        return {"nodes": [], "links": [], "meta": {"source":"ActiveDoc", "floor_height":float(floor_h), "tolerance":float(tol), "floor_levels": []}}

    # one letter per solid
    letters = iter(string.ascii_uppercase)
    b_letter = {}
    for b in breps:
        try: b_letter[id(b)] = next(letters)
        except StopIteration: b_letter[id(b)] = "X"

    # slicers (levels)
    zmins, zmaxs = [], []
    for b in breps:
        z0,z1,_ = _bbox_z(b); zmins.append(z0); zmaxs.append(z1)
    z_min, z_max = min(zmins), max(zmaxs)
    level_Zs, splitters = _make_splitters(z_min, z_max, float(floor_h))

    # aggregate per building+level
    per_building_levels = {}
    for b in breps:
        bl = b_letter[id(b)]
        parts = rg.Brep.CreateBooleanSplit([b], splitters, tol) if splitters else [b]
        parts = list(parts) if parts else [b]
        accum = {}
        for p in parts:
            a = _area_of_brep(p)
            if MIN_PIECE_AREA > 0.0 and a < MIN_PIECE_AREA: continue
            z0,z1,bb = _bbox_z(p)
            zc = 0.5*(z0+z1)
            lvl = int((zc - z_min)//float(floor_h))
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

    # nodes
    node_id = {}
    for bl, levels_dict in per_building_levels.items():
        for lvl, rec in levels_dict.items():
            if rec["area"] <= 0.0: continue
            cx = rec["cx"]/rec["area"]; cy = rec["cy"]/rec["area"]; cz = rec["cz"]/rec["area"]
            bb = rec["bbox"]
            nid = "{}|L{:02d}|{}".format(bl, int(lvl), uuid.uuid4().hex[:6])
            nodes.append({
                "id": nid, "type": "level", "building_id": bl, "level": int(lvl),
                "z_span": [float(rec["z0"]), float(rec["z1"])],
                "centroid": [float(cx), float(cy), float(cz)],
                "bbox": [float(bb.Min.X), float(bb.Min.Y), float(bb.Min.Z),
                         float(bb.Max.X), float(bb.Max.Y), float(bb.Max.Z)],
                "area": float(rec["area"])
            })
            node_id[(bl, int(lvl))] = nid

    # vertical edges
    for bl, levels_dict in per_building_levels.items():
        lvls = sorted([int(k) for k in levels_dict.keys()])
        for i in range(len(lvls)-1):
            l0, l1 = lvls[i], lvls[i+1]
            if (bl,l0) in node_id and (bl,l1) in node_id:
                edges.append({"source": node_id[(bl,l0)], "target": node_id[(bl,l1)], "type": "vertical", "weight": 1.0})

    # PLOT hub
    pc = _get_plot_center()
    if pc:
        nodes.append({"id":"PLOT","type":"plot","centroid":[float(pc.X),float(pc.Y),float(pc.Z)]})
        for bl, levels_dict in per_building_levels.items():
            nid = node_id.get((bl,0))
            if nid is None:
                avail = sorted([int(k) for k in levels_dict.keys()])
                if avail: nid = node_id.get((bl, avail[0]))
            if nid:
                edges.append({"source": nid, "target": "PLOT", "type": "plot", "weight": 1.0})

    return {
        "nodes": nodes,
        "links": edges,
        "meta": {
            "floor_height": float(floor_h),
            "tolerance": float(tol),
            "source": "ActiveDoc",
            "floor_levels": level_Zs
        }
    }


# -------- save --------
def save_graph(path=KNOWLEDGE_PATH, floor_h=FLOOR_HEIGHT, tol=TOL):
    data = build_graph_from_active_doc(floor_h, tol)
    _ensure_dir(path)
    with open(path, "w") as f:
        json.dump({"nodes": data["nodes"], "links": data["links"], "meta": data["meta"]}, f, indent=2)
    Rhino.RhinoApp.WriteLine("[massing_graph_export] Saved graph to: {0}".format(path))
    Rhino.RhinoApp.WriteLine("[massing_graph_export] Nodes: {0}, Edges: {1}".format(len(data["nodes"]), len(data["links"])))


# -------- minimal listener (optional) --------
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
    if ENABLE_LISTENER and _is_on_watched_layer(e.Object):
        _debounce_trigger()

def _on_modify(sender, e):
    if ENABLE_LISTENER and _is_on_watched_layer(e.Object):
        _debounce_trigger()

def _on_replace(sender, e):
    if ENABLE_LISTENER and _is_on_watched_layer(e.NewObject):
        _debounce_trigger()

def _on_delete(sender, e):
    # layer info is unreliable on delete; still debounce since geometry likely changed
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


# -------- entry point --------
def main():
    # Run once
    save_graph()
    # Or, if you prefer this script to install the listener by default, set ENABLE_LISTENER=True at the top
    if ENABLE_LISTENER:
        setup_listener()

if __name__ == "__main__":
    main()
