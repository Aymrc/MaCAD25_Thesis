# -*- coding: utf-8 -*-
# MASSING 3D -> Graph JSON (IronPython / RunPythonScript)
# - One node per building per level (merged slices)
# - Vertical edges only within the same building
# - Each building's Level 0 connects to single "PLOT" hub
# - Saves to knowledge/massing_graph.json

import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
import uuid
import json
import os
from collections import defaultdict
import string

# ----------------- CONFIG -----------------
LAYER_MASSING_ROOT = "MASSING"
LAYER_PLOT         = "PLOT"
FLOOR_HEIGHT       = 3.0
TOL                = sc.doc.ModelAbsoluteTolerance or 0.001
MIN_PIECE_AREA     = 0.0   # set e.g. 1.0 to drop tiny slivers (sq units)

REPO_ROOT          = r"C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis"  # <— yours
KNOWLEDGE_PATH     = os.path.join(REPO_ROOT, "knowledge", "massing_graph.json")
# ------------------------------------------

def ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d)

def to_brep(g):
    if isinstance(g, rg.Brep): return g
    if isinstance(g, rg.Extrusion): return g.ToBrep(True)
    return None

def bbox_union(bb, other):
    if bb is None: return other
    if other is None: return bb
    return rg.BoundingBox.Union(bb, other)

def bbox_z(b):
    bb = b.GetBoundingBox(True)
    return bb.Min.Z, bb.Max.Z, bb

def make_splitters(z_min, z_max, floor_h):
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

def layer_indices_under(root_name):
    idxs = set()
    for lyr in sc.doc.Layers:
        full = lyr.FullPath if lyr.FullPath else lyr.Name
        if not full: continue
        if full == root_name or full.startswith(root_name + "::"):
            idxs.add(lyr.Index)
    return idxs

def iter_massing_breps():
    """Yield breps from MASSING tree (each brep = one building A,B,C...)."""
    idxs = layer_indices_under(LAYER_MASSING_ROOT)
    if not idxs: return
    for obj in sc.doc.Objects:
        if obj.Attributes.LayerIndex in idxs:
            b = to_brep(obj.Geometry)
            if b: yield b

def get_plot_center():
    """Average bbox center of geometry directly on PLOT (and sublayers if any)."""
    idxs = layer_indices_under(LAYER_PLOT)
    if not idxs: return None
    pts = []
    for obj in sc.doc.Objects:
        if obj.Attributes.LayerIndex in idxs:
            try:
                bb = obj.Geometry.GetBoundingBox(True)
                pts.append(bb.Center)
            except:
                pass
    if not pts: return None
    sx = sum(pt.X for pt in pts); sy = sum(pt.Y for pt in pts); sz = sum(pt.Z for pt in pts)
    return rg.Point3d(sx/len(pts), sy/len(pts), sz/len(pts))

def area_of_brep(b):
    amp = rg.AreaMassProperties.Compute(b)
    return amp.Area if amp else 0.0

def centroid_of_brep(b):
    amp = rg.AreaMassProperties.Compute(b)
    return amp.Centroid if amp else b.GetBoundingBox(True).Center

def build_graph_from_active_doc(floor_h, tol):
    breps = list(iter_massing_breps())
    nodes = []
    edges = []

    if not breps:
        return {"nodes": [], "links": [], "meta": {"source":"ActiveDoc"}}

    # Assign building letters (one per brep)
    letter_iter = iter(string.ascii_uppercase)
    building_id_for = {}
    for b in breps:
        try:
            building_id_for[id(b)] = next(letter_iter)
        except StopIteration:
            building_id_for[id(b)] = "X"  # fallback if >26

    # global z extents (for slicer)
    zmins, zmaxs = [], []
    for b in breps:
        z0,z1,_ = bbox_z(b); zmins.append(z0); zmaxs.append(z1)
    z_min, z_max = min(zmins), max(zmaxs)
    _, splitters = make_splitters(z_min, z_max, float(floor_h))

    # For each building: slice, then MERGE pieces per level -> single node per level
    per_building_levels = {}  # b_letter -> { level_idx: {area, cx,cy,cz, bbox} }
    for b in breps:
        b_letter = building_id_for[id(b)]
        parts = rg.Brep.CreateBooleanSplit([b], splitters, tol) if splitters else [b]
        parts = list(parts) if parts else [b]
        accum = {}  # level -> dict( area_sum, cx_sum, cy_sum, cz_sum, bbox )
        for p in parts:
            a = area_of_brep(p)
            if MIN_PIECE_AREA > 0.0 and a < MIN_PIECE_AREA:
                continue  # drop tiny slivers
            z0,z1,bb = bbox_z(p)
            zc = 0.5*(z0+z1)
            level = int((zc - z_min)//float(floor_h))
            c = centroid_of_brep(p)
            rec = accum.get(level)
            if rec is None:
                rec = {"area":0.0, "cx":0.0, "cy":0.0, "cz":0.0, "bbox":None, "z0":z0, "z1":z1}
            rec["area"] += a
            rec["cx"] += c.X * a
            rec["cy"] += c.Y * a
            rec["cz"] += c.Z * a
            rec["bbox"] = bbox_union(rec["bbox"], bb)
            rec["z0"] = min(rec["z0"], z0)
            rec["z1"] = max(rec["z1"], z1)
            accum[level] = rec
        per_building_levels[b_letter] = accum

    # Create nodes (one per building per level)
    node_id_for = {}  # (b_letter, level) -> node_id
    for b_letter, levels in per_building_levels.items():
        for lvl, rec in levels.items():
            if rec["area"] <= 0.0:  # guard
                continue
            cx = rec["cx"]/rec["area"]; cy = rec["cy"]/rec["area"]; cz = rec["cz"]/rec["area"]
            bb = rec["bbox"]
            nid = "{}|L{:02d}|{}".format(b_letter, int(lvl), uuid.uuid4().hex[:6])
            nodes.append({
                "id": nid,
                "type": "level",
                "building_id": b_letter,
                "level": int(lvl),
                "z_span": [float(rec["z0"]), float(rec["z1"])],
                "centroid": [float(cx), float(cy), float(cz)],
                "bbox": [float(bb.Min.X), float(bb.Min.Y), float(bb.Min.Z),
                         float(bb.Max.X), float(bb.Max.Y), float(bb.Max.Z)],
                "area": float(rec["area"])
            })
            node_id_for[(b_letter, int(lvl))] = nid

    # Vertical edges: connect consecutive levels if both exist
    for b_letter, levels in per_building_levels.items():
        lvls = sorted([int(k) for k in levels.keys()])
        for i in range(len(lvls)-1):
            l0 = lvls[i]; l1 = lvls[i+1]
            nid0 = node_id_for.get((b_letter, l0))
            nid1 = node_id_for.get((b_letter, l1))
            if nid0 and nid1:
                edges.append({"source": nid0, "target": nid1, "type": "vertical", "weight": 1.0})

    # PLOT hub + links from each building's Level 0
    plot_center = get_plot_center()
    if plot_center:
        nodes.append({
            "id": "PLOT",
            "type": "plot",
            "centroid": [float(plot_center.X), float(plot_center.Y), float(plot_center.Z)]
        })
        for b_letter, levels in per_building_levels.items():
            # find the lowest existing level for that building (prefer 0)
            if (b_letter, 0) in node_id_for:
                nid = node_id_for[(b_letter, 0)]
            else:
                # fallback to the minimum available level
                avail = sorted([int(k) for k in levels.keys()])
                if not avail: continue
                nid = node_id_for.get((b_letter, avail[0]))
            if nid:
                edges.append({"source": nid, "target": "PLOT", "type": "plot", "weight": 1.0})

    return {
        "nodes": nodes,
        "links": edges,
        "meta": {
            "floor_height": float(floor_h),
            "tolerance": float(tol),
            "source": "ActiveDoc",
            "note": "1 node per building+level; buildings only connect to PLOT from their lowest level."
        }
    }

def main():
    data = build_graph_from_active_doc(FLOOR_HEIGHT, TOL)
    ensure_dir(KNOWLEDGE_PATH)
    with open(KNOWLEDGE_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print("Saved graph:", KNOWLEDGE_PATH)
    print("Nodes:", len(data["nodes"]), "Edges:", len(data["links"]))

if __name__ == "__main__":
    main()
