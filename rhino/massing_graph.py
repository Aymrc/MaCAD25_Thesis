# -*- coding: utf-8 -*-
# massing_graph_grouped_midplanes_mesh_fallback.py
#
# - Groups multiple Breps into a single "building" (by touching or by sublayer)
# - Slices per-building using group-aligned mid-planes (robust, no bucketing)
# - Mesh fallback if BrepPlane returns nothing
# - Unique building IDs: A..Z, AA..ZZ, ...
# - Optional empty-floor nodes to preserve counts
# - Same JSON shape, preview, and diagnostics

import os, uuid, json, time, math, string
import Rhino
import Rhino.Geometry as rg
import Rhino.Geometry.Intersect as rgi
import scriptcontext as sc
import rhinoscriptsyntax as rs
from System.Drawing import Color

# =========================
# CONFIG
# =========================
LAYER_MASSING_ROOT = "MASSING"
LAYER_PLOT         = "PLOT"

FLOOR_HEIGHT_METERS = 3.0

# Group multiple Breps into one building?
# "none" = each brep is its own building
# "by_touching" = group if bounding boxes touch/overlap (fast, default)
# "by_sublayer" = group every brep that lives under the same first sublayer of MASSING
GROUPING_MODE = "by_touching"  # "none" | "by_touching" | "by_sublayer"
GROUP_GAP_TOL = (sc.doc.ModelAbsoluteTolerance or 0.001) * 2.0  # doc units

# Keep an empty node (area = 0) when a slice returns no geometry
KEEP_EMPTY_LEVEL_NODES = True

# Minimum per-level area to keep (m²) – set >0 to drop tiny/empty floors
MIN_LEVEL_AREA_M2 = 0.0

# JSON output path
CURRENT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR  = os.path.dirname(CURRENT_DIR)
KNOWLEDGE_PATH = os.path.join(PROJECT_DIR, "knowledge", "massing_graph.json")

# Listener
ENABLE_LISTENER  = False
DEBOUNCE_SECONDS = 1.0

# IDs
USE_CLEAN_NODE_IDS = True
INCLUDE_RANDOM_UID = False

# Join/union multipliers
JOIN_TOL_MULT  = 6.0
UNION_TOL_MULT = 8.0

# Mesh fallback params
MESH_MIN_EDGE   = 0.0
MESH_MAX_EDGE   = 0.75   # meters
MESH_ANGLE_DEG  = 10.0
MESH_REFINE     = True

# Preview
SHOW_CONTOUR_PREVIEW = True
PREVIEW_LINE_WIDTH   = 2
PREVIEW_PALETTE = [
    Color.FromArgb(240, 78, 62),
    Color.FromArgb(36, 161, 222),
    Color.FromArgb(67, 176, 71),
    Color.FromArgb(250, 176, 5),
    Color.FromArgb(163, 98, 226),
    Color.FromArgb(36, 199, 191),
    Color.FromArgb(236, 94, 164),
    Color.FromArgb(118, 118, 118)
]
STK_CONDUIT_KEY = "massing_graph_contour_conduit"

# =========================
# UNITS
# =========================
def _uu(from_u, to_u):
    return Rhino.RhinoMath.UnitScale(from_u, to_u)

def _doc_len_from_meters(m):
    return float(m) * _uu(Rhino.UnitSystem.Meters, sc.doc.ModelUnitSystem)

def _meters_from_doc_len(L):
    return float(L) * _uu(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Meters)

def _m2_from_doc_area(a_doc):
    s = _uu(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Meters)
    return float(a_doc) * (s ** 2)

def _doc_area_from_m2(a_m2):
    s = _uu(Rhino.UnitSystem.Meters, sc.doc.ModelUnitSystem)
    return float(a_m2) * (s ** 2)

# =========================
# UTILS
# =========================
def _ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        try: os.makedirs(d)
        except: Rhino.RhinoApp.WriteLine("[massing_graph] Could not create folder: {0}".format(d))

def _to_brep(g):
    if isinstance(g, rg.Brep): return g
    if isinstance(g, rg.Extrusion):
        try: return g.ToBrep(True)
        except: return None
    return None

def _bbox_union(bb, other):
    if bb is None: return other
    if other is None: return bb
    try: return rg.BoundingBox.Union(bb, other)
    except: return bb

def _bbox_z(b):
    bb = b.GetBoundingBox(True)
    return bb.Min.Z, bb.Max.Z, bb

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
                if b: yield obj, b
        except: pass

def _get_plot_center():
    idxs = _layer_indices_under(LAYER_PLOT)
    if not idxs: return None
    pts = []
    for obj in sc.doc.Objects:
        try:
            if obj.Attributes.LayerIndex in idxs:
                bb = obj.Geometry.GetBoundingBox(True)
                pts.append(bb.Center)
        except: pass
    if not pts: return None
    sx = sum(p.X for p in pts); sy = sum(p.Y for p in pts); sz = sum(p.Z for p in pts)
    return rg.Point3d(sx/len(pts), sy/len(pts), sz/len(pts))

# -------- Excel-style labels: A..Z, AA..ZZ, AAA.. --------
def _excel_label(n):  # n>=1
    s = []
    while n > 0:
        n -= 1
        s.append(chr(ord('A') + (n % 26)))
        n //= 26
    s.reverse()
    return ''.join(s)

# =========================
# GROUPING HELPERS
# =========================
def _bbox_overlap(bb1, bb2, pad=0.0):
    if bb1 is None or bb2 is None: return False
    return not (
        bb1.Max.X + pad < bb2.Min.X or bb2.Max.X + pad < bb1.Min.X or
        bb1.Max.Y + pad < bb2.Min.Y or bb2.Max.Y + pad < bb1.Min.Y or
        bb1.Max.Z + pad < bb2.Min.Z or bb2.Max.Z + pad < bb1.Min.Z
    )

def _group_breps_by_sublayer(obj_breps):
    groups = {}
    for obj, b in obj_breps:
        try:
            lyr = sc.doc.Layers[obj.Attributes.LayerIndex]
            full = lyr.FullPath or lyr.Name
            parts = (full or "").split("::")
            key = parts[1] if len(parts) > 1 else parts[0]
        except:
            key = "UNGROUPED"
        groups.setdefault(key, []).append((obj, b))
    return list(groups.values())

def _group_breps_by_touching(obj_breps, pad):
    items = []
    for (obj, b) in obj_breps:
        try:
            items.append({"pair": (obj, b), "bb": b.GetBoundingBox(True), "brep": b})
        except: pass
    n = len(items)
    visited = [False]*n
    groups = []
    for i in range(n):
        if visited[i]: continue
        queue = [i]; visited[i] = True
        comp = [items[i]["pair"]]
        while queue:
            a = queue.pop()
            bb_a = items[a]["bb"]; bA = items[a]["brep"]
            for j in range(n):
                if visited[j]: continue
                bb_b = items[j]["bb"]
                if not _bbox_overlap(bb_a, bb_b, pad): 
                    continue
                # Optionally test real contact (edges/pts); permissive fallback to bbox contact
                try:
                    rc, crv, pts = rgi.Intersection.BrepBrep(bA, items[j]["brep"], sc.doc.ModelAbsoluteTolerance)
                    touching = bool(rc and ((crv and crv.Count>0) or (pts and pts.Count>0)))
                except:
                    touching = False
                if touching or _bbox_overlap(bb_a, bb_b, pad*1.5):
                    visited[j] = True
                    queue.append(j)
                    comp.append(items[j]["pair"])
        groups.append(comp)
    return groups

def _make_building_groups(obj_breps):
    if GROUPING_MODE == "by_sublayer":
        return _group_breps_by_sublayer(obj_breps)
    if GROUPING_MODE == "by_touching":
        return _group_breps_by_touching(obj_breps, GROUP_GAP_TOL)
    return [[p] for p in obj_breps]  # "none"

# =========================
# AREA PIPELINE (planar)
# =========================
def _area_union_centroid_on_plane(curves, plane, tol):
    if not curves: return 0.0, None, []
    projected = []
    for c in curves:
        try:
            projected.append(rg.Curve.ProjectToPlane(c, plane))
        except: pass
    if not projected: return 0.0, None, []
    try:
        joined = rg.Curve.JoinCurves(projected, float(tol) * float(JOIN_TOL_MULT))
    except:
        joined = projected
    unioned = None
    try:
        unioned = rg.Curve.CreateBooleanUnion(joined, float(tol) * float(UNION_TOL_MULT))
    except:
        pass
    candidates = unioned if unioned and len(unioned) > 0 else joined

    total = 0.0; cx = cy = 0.0
    for c in candidates:
        if c is None: continue
        cc = c
        try:
            if not cc.IsClosed:
                cc = c.DuplicateCurve()
                cc.MakeClosed(float(tol) * float(UNION_TOL_MULT))
        except: pass
        try:
            if cc.IsClosed:
                amp = rg.AreaMassProperties.Compute(cc)
                if amp and amp.Area > 0.0:
                    a = amp.Area; cp = amp.Centroid
                    total += a
                    cx += cp.X * a
                    cy += cp.Y * a
        except: pass
    if total > 0.0:
        return total, (cx/total, cy/total), candidates
    try:
        amp = rg.AreaMassProperties.Compute(candidates)
        if amp and amp.Area > 0.0:
            a = amp.Area; cp = amp.Centroid
            return a, (cp.X, cp.Y), candidates
    except: pass
    return 0.0, None, candidates

# =========================
# PREVIEW
# =========================
class _ContourPreviewConduit(Rhino.Display.DisplayConduit):
    def __init__(self, by_level_idx_curves):
        super(_ContourPreviewConduit, self).__init__()
        self.by_level = by_level_idx_curves
    def DrawForeground(self, e):
        if not self.by_level: return
        d = e.Display
        w = PREVIEW_LINE_WIDTH
        palette = PREVIEW_PALETTE
        pcount = len(palette) if palette else 0
        for (bl, idx), curves in self.by_level.items():
            col = palette[idx % pcount] if pcount else Color.Gold
            for c in curves:
                if c is not None:
                    d.DrawCurve(c, col, w)
            try:
                bb = None
                for c in curves:
                    try: bb = _bbox_union(bb, c.GetBoundingBox(True))
                    except: pass
                if bb and bb.IsValid:
                    label_pt = rg.Point3d(bb.Min.X, bb.Max.Y, 0.5*(bb.Min.Z+bb.Max.Z))
                    d.DrawDot(label_pt, "{0}-L{1:02d}".format(bl, idx), col, Color.White)
            except: pass

def _preview_off():
    existing = sc.sticky.get(STK_CONDUIT_KEY)
    if existing:
        try: existing.Enabled = False
        except: pass
        sc.sticky[STK_CONDUIT_KEY] = None
        Rhino.RhinoApp.WriteLine("[massing_graph] Contour preview OFF.")

def _preview_on(by_level_idx_curves):
    _preview_off()
    if not SHOW_CONTOUR_PREVIEW: return
    try:
        cd = _ContourPreviewConduit(by_level_idx_curves)
        cd.Enabled = True
        sc.sticky[STK_CONDUIT_KEY] = cd
        Rhino.RhinoApp.WriteLine("[massing_graph] Contour preview ON (segments: {0}).".format(len(by_level_idx_curves)))
        sc.doc.Views.Redraw()
    except Exception as e:
        Rhino.RhinoApp.WriteLine("[massing_graph] Preview error: {0}".format(e))

# =========================
# MESH FALLBACK
# =========================
def _mesh_from_brep(brep):
    if brep is None: return None
    mp = rg.MeshingParameters()
    mp.JaggedSeams = False
    mp.MaximumEdgeLength = _doc_len_from_meters(MESH_MAX_EDGE) if MESH_MAX_EDGE > 0 else 0.0
    mp.MinimumEdgeLength = _doc_len_from_meters(MESH_MIN_EDGE) if MESH_MIN_EDGE > 0 else 0.0
    mp.RefineGrid = bool(MESH_REFINE)
    mp.SimplePlanes = False
    mp.GridAngle = math.radians(float(MESH_ANGLE_DEG))
    try:
        meshes = rg.Mesh.CreateFromBrep(brep, mp)
    except:
        meshes = None
    if not meshes: return None
    if len(meshes) == 1: return meshes[0]
    m = rg.Mesh()
    for part in meshes:
        try: m.Append(part)
        except: pass
    try: m.UnifyNormals()
    except: pass
    return m

def _polylines_to_nurbs_curves(pls):
    crvs = []
    for pl in pls or []:
        try:
            if pl is None or pl.Count < 2: continue
            # Close if needed
            if pl[0].DistanceTo(pl[-1]) <= sc.doc.ModelAbsoluteTolerance:
                pl = rg.Polyline(pl[:-1]); pl.Add(pl[0])
            crv = pl.ToNurbsCurve()
            if crv: crvs.append(crv)
        except: pass
    return crvs

# =========================
# LEVEL SPANS (group-aligned, no off-by-one)
# =========================
def _level_spans_from_bbox(zmin, zmax, H, tol_z):
    height = max(0.0, zmax - zmin)
    if H <= tol_z or height <= tol_z:
        return []
    q = int(math.floor(height / H))
    rem = height - q * H
    n = q + (1 if rem > tol_z else 0)
    spans = []
    for i in range(n):
        z0 = zmin + i * H
        z1 = min(z0 + H, zmax)
        if i == n - 1 and rem > tol_z:
            z1 = zmin + q * H + rem
            z1 = min(z1, zmax)
        mid = 0.5 * (z0 + z1)
        spans.append((i, z0, mid, z1))
    return spans

# =========================
# CORE
# =========================
def build_graph_from_active_doc(floor_h_doc, tol):
    obj_breps = list(_iter_massing_breps()) or []
    Rhino.RhinoApp.WriteLine("[massing_graph] Polysurfaces under MASSING: {0}".format(len(obj_breps)))
    nodes, edges = [], []

    meta = {
        "source": "ActiveDoc",
        "gfa_mode": "grouped_midplanes_mesh_fallback",
        "doc_unit_system": str(sc.doc.ModelUnitSystem),
        "units": {"length":"m", "area":"m^2", "volume":"m^3"},
        "floor_height_m": float(_meters_from_doc_len(floor_h_doc)),
        "grouping_mode": GROUPING_MODE
    }
    if not obj_breps:
        return {"nodes": [], "links": [], "meta": meta, "_preview": None}

    H = float(floor_h_doc)
    min_area_doc = _doc_area_from_m2(MIN_LEVEL_AREA_M2)
    tol_z = float(sc.doc.ModelAbsoluteTolerance)

    preview_curves = {}
    per_building_levels = {}
    diagnostics = {}

    groups = _make_building_groups(obj_breps)
    Rhino.RhinoApp.WriteLine("[massing_graph] Building groups ({}): {}".format(GROUPING_MODE, len(groups)))

    for gi, group in enumerate(groups):
        bl = _excel_label(gi + 1)  # Unique building id

        # Group overall bbox (for aligned floors and reporting)
        bb_group = None
        for _, b in group:
            bb_group = _bbox_union(bb_group, b.GetBoundingBox(True))
        if not (bb_group and bb_group.IsValid):
            continue

        zmin_g = bb_group.Min.Z
        zmax_g = bb_group.Max.Z
        spans = _level_spans_from_bbox(zmin_g, zmax_g, H, tol_z)
        if not spans:
            continue

        building_accum = {}
        mesh_fallback_hits = 0
        empty_kept = 0

        # Slice every member at the group's mid-planes
        for (obj, b) in group:
            zmin_b, zmax_b, _ = _bbox_z(b)
            mesh = _mesh_from_brep(b)

            for idx, z0, mid, z1 in spans:
                # Skip if this member doesn't span this floor at all (quick bbox gate)
                if z1 < zmin_b - tol_z or z0 > zmax_b + tol_z:
                    continue

                plane = rg.Plane(rg.Point3d(0,0,mid), rg.Vector3d.ZAxis)

                # Brep slice
                crvs = []
                try:
                    rc, crv_list, pts = rgi.Intersection.BrepPlane(b, plane, float(tol))
                    if rc and crv_list: crvs = list(crv_list)
                except: pass

                a_doc, xy_c, joined = _area_union_centroid_on_plane(crvs, plane, float(tol))

                # Mesh fallback
                if a_doc <= 0.0 and mesh is not None:
                    try:
                        pls = rgi.Intersection.MeshPlane(mesh, plane)
                    except:
                        pls = None
                    if pls and len(pls) > 0:
                        crvs2 = _polylines_to_nurbs_curves(pls)
                        a2, c2, j2 = _area_union_centroid_on_plane(crvs2, plane, float(tol))
                        if a2 > 0.0:
                            a_doc, xy_c, joined = a2, c2, j2
                            mesh_fallback_hits += 1

                # Keep / accumulate
                if a_doc <= 0.0:
                    continue  # don't write empties per-member; we may add per-building empties later

                rec = building_accum.get(idx) or {"area_doc":0.0, "cx":0.0, "cy":0.0, "cz":mid, "z0":z0, "z1":z1, "bbox":bb_group}
                rec["area_doc"] += a_doc
                if xy_c:
                    cx, cy = xy_c
                    rec["cx"] += cx * a_doc; rec["cy"] += cy * a_doc
                rec["bbox"] = _bbox_union(rec.get("bbox"), bb_group)
                building_accum[idx] = rec

                if joined:
                    key = (bl, idx); lstp = preview_curves.get(key) or []
                    lstp.extend(joined); preview_curves[key] = lstp

        # After all members, optionally add empty levels so counts match expected
        if KEEP_EMPTY_LEVEL_NODES and MIN_LEVEL_AREA_M2 <= 0.0:
            present = set(building_accum.keys())
            for idx, z0, mid, z1 in spans:
                if idx in present: 
                    continue
                building_accum[idx] = {"area_doc":0.0, "cx":0.0, "cy":0.0, "cz":mid, "z0":z0, "z1":z1, "bbox":bb_group}
                empty_kept += 1

        # Drop tiny floors if requested
        if MIN_LEVEL_AREA_M2 > 0.0:
            min_a = _doc_area_from_m2(MIN_LEVEL_AREA_M2)
            building_accum = {k:v for k,v in building_accum.items() if v.get("area_doc",0.0) >= min_a}

        if building_accum:
            per_building_levels[bl] = building_accum
            diagnostics[bl] = {
                "bbox_height_m": _meters_from_doc_len(zmax_g - zmin_g),
                "levels_targeted": len(spans),
                "nodes_emitted": len(building_accum),
                "mesh_fallback_hits": mesh_fallback_hits,
                "empty_nodes_kept": empty_kept
            }

    # ===== Build nodes
    node_id = {}
    for bl, levels_dict in per_building_levels.items():
        for lvl, rec in levels_dict.items():
            area_doc = rec.get("area_doc", 0.0)
            area_m2 = float(_m2_from_doc_area(area_doc)) if area_doc > 0.0 else 0.0
            w = area_doc if area_doc != 0.0 else 1.0
            cx = rec.get("cx",0.0)/w; cy = rec.get("cy",0.0)/w; cz = rec.get("cz",0.0)
            z0 = rec.get("z0", cz - 0.5*H); z1 = rec.get("z1", cz + 0.5*H)
            bb = rec.get("bbox")
            if bb is None:
                bb = rg.BoundingBox(rg.Point3d(cx,cy,z0), rg.Point3d(cx,cy,z1))

            clean_id = "{0}-L{1:02d}".format(bl, int(lvl))
            nid = clean_id if USE_CLEAN_NODE_IDS else "{0}|L{1:02d}|{2}".format(bl, int(lvl), uuid.uuid4().hex[:6])
            if INCLUDE_RANDOM_UID:
                uid = uuid.uuid4().hex[:6]
            else:
                uid = None

            node_obj = {
                "id": nid,
                "type": "level",
                "building_id": bl,
                "level": int(lvl),
                "z_span_m": [float(_meters_from_doc_len(z0)), float(_meters_from_doc_len(z1))],
                "centroid_m": [float(_meters_from_doc_len(cx)), float(_meters_from_doc_len(cy)), float(_meters_from_doc_len(cz))],
                "bbox_m": [
                    float(_meters_from_doc_len(bb.Min.X)), float(_meters_from_doc_len(bb.Min.Y)), float(_meters_from_doc_len(bb.Min.Z)),
                    float(_meters_from_doc_len(bb.Max.X)), float(_meters_from_doc_len(bb.Max.Y)), float(_meters_from_doc_len(bb.Max.Z))
                ],
                "area_m2": area_m2,
                "area":    area_m2,
                "volume_m3": 0.0,
                "label": "{0} • L{1:02d}".format(bl, int(lvl)),
                "clean_id": clean_id
            }
            if uid: node_obj["uid"] = uid
            nodes.append(node_obj)
            node_id[(bl, int(lvl))] = nid

    # ===== Vertical edges
    edges = []
    for bl, levels_dict in per_building_levels.items():
        lvls = sorted([int(k) for k in levels_dict.keys()])
        for i in range(len(lvls)-1):
            l0, l1 = lvls[i], lvls[i+1]
            if (bl,l0) in node_id and (bl,l1) in node_id:
                edges.append({"source": node_id[(bl,l0)], "target": node_id[(bl,l1)], "type":"vertical", "weight":1.0})

    # ===== PLOT hub
    pc = _get_plot_center()
    if pc:
        nodes.append({"id":"PLOT","type":"plot",
                      "centroid_m":[float(_meters_from_doc_len(pc.X)), float(_meters_from_doc_len(pc.Y)), float(_meters_from_doc_len(pc.Z))]})
        for bl, levels_dict in per_building_levels.items():
            nid = node_id.get((bl,0))
            if nid is None:
                avail = sorted([int(k) for k in levels_dict.keys()])
                if avail: nid = node_id.get((bl, avail[0]))
            if nid:
                edges.append({"source": nid, "target": "PLOT", "type": "plot", "weight": 1.0})

    meta["diagnostics_per_building"] = diagnostics
    return {"nodes": nodes, "links": edges, "meta": meta, "_preview": preview_curves}

# =========================
# SAVE + DIAGNOSTICS + PREVIEW
# =========================
def _diagnose_level_counts(nodes, H_m):
    from collections import defaultdict
    by_bld = defaultdict(list)
    for n in nodes:
        if n.get("type") == "level":
            by_bld[n["building_id"]].append(n)

    tol_z_m = (sc.doc.ModelAbsoluteTolerance or 0.001) * _uu(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Meters)
    Rhino.RhinoApp.WriteLine("— Level diagnostics (H = {:.3f} m) —".format(H_m))
    for bl, lst in sorted(by_bld.items()):
        if not lst:
            Rhino.RhinoApp.WriteLine(" {} : no levels".format(bl))
            continue
        zmins = [n["z_span_m"][0] for n in lst]
        zmaxs = [n["z_span_m"][1] for n in lst]
        zmin = min(zmins); zmax = max(zmaxs); height = max(0.0, zmax - zmin)
        q = int(math.floor(height / H_m))
        rem = height - q * H_m
        expected = q + (1 if rem > tol_z_m else 0)
        Rhino.RhinoApp.WriteLine(" {} : bboxHeight={:.3f} m | floors_expected={} | nodes={}".format(
            bl, height, expected, len(lst)))

def save_graph(path=KNOWLEDGE_PATH):
    try:
        tol = sc.doc.ModelAbsoluteTolerance or 0.001
        floor_h_doc = _doc_len_from_meters(FLOOR_HEIGHT_METERS)
        res = build_graph_from_active_doc(floor_h_doc, tol)
        data = {"nodes": res["nodes"], "links": res["links"], "meta": res["meta"]}

        levels = [n for n in data["nodes"] if n.get("type") == "level"]
        def _area_of(n):
            try: return float(n.get("area", n.get("area_m2", 0.0)))
            except: return 0.0
        total_area_m2 = sum(_area_of(n) for n in levels)

        by_bld = {}
        for n in levels:
            bl = n.get("building_id", "?")
            a  = _area_of(n)
            v = by_bld.get(bl) or {"sum_m2":0.0, "max_level_m2":0.0, "levels":0}
            v["sum_m2"] += a
            if a > v["max_level_m2"]: v["max_level_m2"] = a
            v["levels"] += 1
            by_bld[bl] = v
        site_footprint_ref_m2 = sum(v["max_level_m2"] for v in by_bld.values())
        level_count = sum(v["levels"] for v in by_bld.values())

        data["meta"]["diagnostics"] = {
            "levels_count": int(level_count),
            "total_area_m2": float(total_area_m2),
            "sum_max_level_per_building_m2": float(site_footprint_ref_m2)
        }

        _ensure_dir(path)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        Rhino.RhinoApp.WriteLine("[massing_graph] Saved: {0}".format(path))
        Rhino.RhinoApp.WriteLine("[massing_graph] Nodes: {0}, Edges: {1}".format(len(data["nodes"]), len(data["links"])))
        Rhino.RhinoApp.WriteLine("[massing_graph] Level nodes (total): {0}".format(level_count))
        Rhino.RhinoApp.WriteLine("[massing_graph] Total GFA: {:.3f} m²".format(total_area_m2))
        Rhino.RhinoApp.WriteLine("[massing_graph] Sum(max level per building): {:.3f} m²".format(site_footprint_ref_m2))

        _diagnose_level_counts(data["nodes"], data["meta"]["floor_height_m"])

        if SHOW_CONTOUR_PREVIEW:
            _preview_on(res.get("_preview") or {})
        else:
            _preview_off()

    except Exception as e:
        Rhino.RhinoApp.WriteLine("[massing_graph] Save error: {0}".format(e))

# -------- listener (optional) --------
_is_debouncing = False
def _debounce_trigger():
    global _is_debouncing
    if _is_debouncing: return
    _is_debouncing = True
    def _run_later():
        time.sleep(DEBOUNCE_SECONDS)
        try:
            save_graph()
            Rhino.RhinoApp.WriteLine("[massing_graph] Debounced export complete.")
        except Exception as e:
            Rhino.RhinoApp.WriteLine("[massing_graph] Export error: {0}".format(e))
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
    if not ENABLE_LISTENER: return
    try:
        lname = _layer_name_from_event_obj(e.Object)
        if lname is None or _is_on_watched_layer(e.Object):
            _debounce_trigger()
    except:
        _debounce_trigger()

def _on_modify(sender, e):
    if not ENABLE_LISTENER: return
    try:
        lname = _layer_name_from_event_obj(e.Object)
        if lname is None or _is_on_watched_layer(e.Object):
            _debounce_trigger()
    except:
        _debounce_trigger()

def _on_replace(sender, e):
    if not ENABLE_LISTENER: return
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
    if sc.sticky.get("massing_graph_listener_on"):
        Rhino.RhinoApp.WriteLine("[massing_graph] Listener already active.")
        return
    Rhino.RhinoDoc.AddRhinoObject += _on_add
    Rhino.RhinoDoc.ModifyObjectAttributes += _on_modify
    Rhino.RhinoDoc.ReplaceRhinoObject += _on_replace
    Rhino.RhinoDoc.DeleteRhinoObject += _on_delete
    sc.sticky["massing_graph_listener_on"] = True
    Rhino.RhinoApp.WriteLine("[massing_graph] Listener attached (MASSING/PLOT).")

def remove_listener():
    try: Rhino.RhinoDoc.AddRhinoObject -= _on_add
    except: pass
    try: Rhino.RhinoDoc.ModifyObjectAttributes -= _on_modify
    except: pass
    try: Rhino.RhinoDoc.ReplaceRhinoObject -= _on_replace
    except: pass
    try: Rhino.RhinoDoc.DeleteRhinoObject -= _on_delete
    except: pass
    sc.sticky["massing_graph_listener_on"] = False
    Rhino.RhinoApp.WriteLine("[massing_graph] Listener removed.")

# -------- entry --------
def main():
    save_graph()
    if ENABLE_LISTENER:
        setup_listener()

if __name__ == "__main__":
    main()
