# -*- coding: utf-8 -*-
# massing_graph_grouped_midplanes_mesh_fallback_with_branches_by_sections.py
#
# - Groups multiple Breps into a single "building" (by touching or by sublayer)
# - Slices per-building using group-aligned mid-planes (robust, mesh fallback)
# - NEW: Podium→Tower branching detected from per-level section curves,
#        so it works even when podium+towers are a single joined Brep.
# - Per-branch, per-level areas are computed from the component curves (no double count).
# - Same JSON shape, preview, and listener as before.

import os, uuid, json, time, math
import Rhino
import Rhino.Geometry as rg
import Rhino.Geometry.Intersect as rgi
import scriptcontext as sc
import rhinoscriptsyntax as rs
from System.Drawing import Color
from collections import defaultdict

# =========================
# CONFIG
# =========================
LAYER_MASSING_ROOT = "MASSING"
LAYER_PLOT         = "PLOT"

FLOOR_HEIGHT_METERS = 3.0

# "none" | "by_touching" | "by_sublayer"
GROUPING_MODE = "by_touching"
GROUP_GAP_TOL = (sc.doc.ModelAbsoluteTolerance or 0.001) * 2.0  # doc units

# Branching (podium→towers) from section components
BRANCHING_ENABLED   = True
BRANCH_MATCH_PAD    = GROUP_GAP_TOL
BRANCH_Z_TOLERANCE  = (sc.doc.ModelAbsoluteTolerance or 0.001)

# Keep a zero-area node when a level has no section in a branch
KEEP_EMPTY_LEVEL_NODES = True

# Minimum area (m²) to keep a node; 0 = keep all
MIN_LEVEL_AREA_M2 = 0.0

# JSON output path
CURRENT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR   = os.path.dirname(CURRENT_DIR)
KNOWLEDGE_PATH = os.path.join(PROJECT_DIR, "knowledge", "massing_graph.json")

# Listener
ENABLE_LISTENER  = False
DEBOUNCE_SECONDS = 0.8

# Preview
SHOW_CONTOUR_PREVIEW = False
PREVIEW_LINE_WIDTH   = 2
PREVIEW_PALETTE = [
    Color.FromArgb(255, 230,  25,  75),
    Color.FromArgb(255,  60, 180,  75),
    Color.FromArgb(255,   0, 130, 200),
    Color.FromArgb(255, 245, 130,  48),
    Color.FromArgb(255, 145,  30, 180),
    Color.FromArgb(255,  70, 240, 240),
    Color.FromArgb(255, 240,  50, 230),
]

# Node IDs
USE_CLEAN_NODE_IDS = True
INCLUDE_RANDOM_UID = False

UNION_TOL_MULT = 4.0

# =========================
# UNITS
# =========================
def _uu(src, dst):
    try: return Rhino.RhinoMath.UnitScale(src, dst)
    except: return 1.0

def _doc_len_from_meters(L):
    return float(L) * _uu(Rhino.UnitSystem.Meters, sc.doc.ModelUnitSystem)

def _meters_from_doc_len(L):
    return float(L) * _uu(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Meters)

def _m2_from_doc_area(a_doc):
    s = _uu(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Meters)
    return float(a_doc) * (s ** 2)

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
    try:
        bb = b.GetBoundingBox(True)
        return bb.Min.Z, bb.Max.Z, bb
    except:
        return 0.0, 0.0, None

def _layer_indices_under(root_name):
    idx_root = sc.doc.Layers.Find(root_name, True)
    if idx_root < 0: return []
    res = []
    for i, lyr in enumerate(sc.doc.Layers):
        try:
            if lyr is None: continue
            p = lyr
            while p is not None and p.ParentLayerId != Rhino.Geometry.Unset.InstanceGuid:
                p = sc.doc.Layers.FindId(p.ParentLayerId)
            if p and p.Name == root_name:
                res.append(i)
        except: pass
    return res

def _iter_massing_breps():
    idxs = _layer_indices_under(LAYER_MASSING_ROOT)
    if not idxs: return
    for obj in sc.doc.Objects:
        try:
            if obj is None: continue
            if obj.Attributes.LayerIndex not in idxs: continue
            b = _to_brep(obj.Geometry)
            if b: yield (obj, b)
        except: pass

def _get_plot_center():
    try:
        idx = sc.doc.Layers.Find(LAYER_PLOT, True)
        if idx < 0: return None
        rhobjs = sc.doc.Objects.FindByLayer(LAYER_PLOT)
        bb = None
        for o in rhobjs:
            try: bb = _bbox_union(bb, o.Geometry.GetBoundingBox(True))
            except: pass
        if bb:
            c = bb.Center
            return (c.X, c.Y, c.Z)
    except: pass
    return None

def _excel_label(n):
    s = ""
    n0 = int(n)
    while True:
        n0, r = divmod(n0, 26)
        s = chr(ord('A') + r) + s
        if n0 == 0: break
        n0 -= 1
    return s

def _bbox_overlap(bb1, bb2, pad=0.0):
    if bb1 is None or bb2 is None: return False
    return not (
        bb1.Max.X + pad < bb2.Min.X or bb2.Max.X + pad < bb1.Min.X or
        bb1.Max.Y + pad < bb2.Min.Y or bb2.Max.Y + pad < bb1.Min.Y or
        bb1.Max.Z + pad < bb2.Min.Z or bb2.Max.Z + pad < bb1.Min.Z
    )

# =========================
# GROUPING
# =========================
def _group_breps_by_sublayer(obj_breps):
    groups = {}
    for (obj, b) in obj_breps:
        try:
            layer = sc.doc.Layers[obj.Attributes.LayerIndex]
            full = layer.FullPath
            parts = full.split("::", 1)
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
                # permissive: bbox touch is enough
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
    return [[p] for p in obj_breps]

# =========================
# AREA / SECTIONS
# =========================
def _area_union_centroid_on_plane(curves, plane, tol):
    if not curves: return 0.0, None, []
    projected = []
    for c in curves:
        try: projected.append(rg.Curve.ProjectToPlane(c, plane))
        except: pass
    # Join candidates before Area
    try:
        joined = rg.Curve.JoinCurves(projected, float(tol) * float(UNION_TOL_MULT)) or []
    except:
        joined = projected
    # Close where possible
    candidates = []
    for c in joined:
        if c is None: continue
        cc = c
        if not cc.IsClosed:
            cc = c.DuplicateCurve()
            try: cc.MakeClosed(float(tol) * float(UNION_TOL_MULT))
            except: pass
        candidates.append(cc)
    total = 0.0; cx = cy = 0.0
    for c in candidates:
        try:
            if c and c.IsClosed:
                amp = rg.AreaMassProperties.Compute(c)
                if amp and amp.Area > 0.0:
                    a = amp.Area; cp = amp.Centroid
                    total += a; cx += cp.X * a; cy += cp.Y * a
        except: pass
    if total > 0.0:
        return total, (cx/total, cy/total), candidates
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

_preview_conduit = None
def _preview_on(by_level_idx_curves):
    global _preview_conduit
    try:
        if _preview_conduit: _preview_off()
    except: pass
    _preview_conduit = _ContourPreviewConduit(by_level_idx_curves)
    try:
        _preview_conduit.Enabled = True
        sc.doc.Views.Redraw()
    except: pass

def _preview_off():
    global _preview_conduit
    try:
        if _preview_conduit:
            _preview_conduit.Enabled = False
            _preview_conduit = None
            sc.doc.Views.Redraw()
    except: pass

# =========================
# MESH FALLBACK
# =========================
def _mesh_from_brep(b):
    try:
        mp = Rhino.Geometry.MeshingParameters.Default
        parts = Rhino.Geometry.Mesh.CreateFromBrep(b, mp)
        if not parts: return None
        if isinstance(parts, (list, tuple)):
            m = Rhino.Geometry.Mesh()
            for p in parts: 
                try: m.Append(p)
                except: pass
            parts = m
    except:
        return None
    try: parts.UnifyNormals()
    except: pass
    return parts

def _polylines_to_nurbs_curves(pls):
    crvs = []
    for pl in pls or []:
        try:
            if pl is None or pl.Count < 2: continue
            if pl[0].DistanceTo(pl[-1]) <= sc.doc.ModelAbsoluteTolerance:
                pl = rg.Polyline(pl[:-1]); pl.Add(pl[0])
            crv = pl.ToNurbsCurve()
            if crv: crvs.append(crv)
        except: pass
    return crvs

# =========================
# LEVEL SPANS
# =========================
def _level_spans_from_bbox(zmin, zmax, H, tol_z):
    height = max(0.0, zmax - zmin)
    if H <= tol_z or height <= tol_z: return []
    q = int(math.floor(height / H))
    rem = height - q * H
    n = q + (1 if rem > tol_z else 0)
    spans = []
    for i in range(n):
        z0 = zmin + i * H
        mid = z0 + 0.5 * H
        z1 = min(z0 + H, zmax)
        spans.append((i, z0, mid, z1))
    return spans

# =========================
# BRANCHING FROM SECTION CURVES
# =========================
def _curve_bbox_xy(curve):
    bb = curve.GetBoundingBox(True)
    return rg.BoundingBox(rg.Point3d(bb.Min.X, bb.Min.Y, 0.0),
                          rg.Point3d(bb.Max.X, bb.Max.Y, 0.0))

def _xy_bbox_overlap(bb1, bb2, pad=0.0):
    if bb1 is None or bb2 is None: return False
    return not (
        bb1.Max.X + pad < bb2.Min.X or bb2.Max.X + pad < bb1.Min.X or
        bb1.Max.Y + pad < bb2.Min.Y or bb2.Max.Y + pad < bb1.Min.Y
    )

def _connected_components_xy(items, pad=0.0):
    n = len(items)
    adj = [[] for _ in range(n)]
    for i in range(n):
        ii, bb_i = items[i]
        for j in range(i+1, n):
            jj, bb_j = items[j]
            if _xy_bbox_overlap(bb_i, bb_j, pad):
                adj[i].append(j); adj[j].append(i)
    seen = [False]*n
    comps = []
    for i in range(n):
        if seen[i]: continue
        stack = [i]; seen[i] = True; comp = [items[i][0]]
        while stack:
            k = stack.pop()
            for nb in adj[k]:
                if not seen[nb]:
                    seen[nb]=True; stack.append(nb); comp.append(items[nb][0])
        comps.append(comp)
    return comps

def _area_centroid_of_curves(curves):
    area = 0.0; cx = cy = 0.0
    for c in curves:
        try:
            if c and c.IsClosed:
                amp = rg.AreaMassProperties.Compute(c)
                if amp and amp.Area > 0.0:
                    a = amp.Area; cp = amp.Centroid
                    area += a; cx += cp.X*a; cy += cp.Y*a
        except: pass
    if area > 0.0:
        return area, cx/area, cy/area
    return 0.0, 0.0, 0.0

def _build_branches_from_section_components(bl, spans, curves_by_level, pad, bb_group):
    """
    curves_by_level: dict level_idx -> list[rg.Curve] (closed, on plane)
    returns branches dict (branch -> {level_idx -> rec}) and split_idx
    """
    if not spans:
        return {bl:{}}, None

    N = len(spans)
    # 1) Compute components per band from curve XY overlap
    comp_per_band = []      # list of list of components; component = list of curve indices
    compinfo_per_band = []  # list of list of {"area_doc":..., "cx":..., "cy":...}
    for k in range(N):
        curves = curves_by_level.get(k) or []
        if not curves:
            comp_per_band.append([])
            compinfo_per_band.append([])
            continue
        items = [(i, _curve_bbox_xy(curves[i])) for i in range(len(curves))]
        comps = _connected_components_xy(items, pad=pad)
        comp_per_band.append(comps)
        infos = []
        for comp in comps:
            comp_curves = [curves[i] for i in comp]
            a_doc, cx, cy = _area_centroid_of_curves(comp_curves)
            infos.append({"idxs": comp, "area_doc":a_doc, "cx":cx, "cy":cy})
        compinfo_per_band.append(infos)

    # 2) Find first split band
    split_idx = None
    for k, infos in enumerate(compinfo_per_band):
        if len(infos) >= 2:
            split_idx = k
            break

    branches = defaultdict(dict)

    # Helper to make a record
    def make_rec(k, a_doc, cx, cy):
        idx, z0, mid, z1 = spans[k]
        return {
            "area_doc": float(a_doc),
            "cx": float(cx), "cy": float(cy), "cz": float(mid),
            "z0": float(z0), "z1": float(z1),
            "bbox": bb_group
        }

    if split_idx is None:
        # Single column all the way: merge everything into podium branch
        for k in range(N):
            infos = compinfo_per_band[k]
            if not infos: continue
            a_doc = sum(ci["area_doc"] for ci in infos)
            # centroid: area-weighted
            if a_doc > 0:
                cx = sum(ci["cx"]*ci["area_doc"] for ci in infos)/a_doc
                cy = sum(ci["cy"]*ci["area_doc"] for ci in infos)/a_doc
            else:
                cx = cy = 0.0
            branches[bl][k] = make_rec(k, a_doc, cx, cy)
        return branches, None

    # 3) Below split → podium (merge)
    for k in range(0, split_idx):
        infos = compinfo_per_band[k]
        if not infos: continue
        a_doc = sum(ci["area_doc"] for ci in infos)
        if a_doc > 0:
            cx = sum(ci["cx"]*ci["area_doc"] for ci in infos)/a_doc
            cy = sum(ci["cy"]*ci["area_doc"] for ci in infos)/a_doc
        else:
            cx = cy = 0.0
        branches[bl][k] = make_rec(k, a_doc, cx, cy)

    # 4) Name towers at split, ordered by (x,y) centroid
    split_infos = compinfo_per_band[split_idx]
    towers_sorted = sorted(split_infos, key=lambda ci: (ci["cx"], ci["cy"]))
    ref_centroids = [(ci["cx"], ci["cy"]) for ci in towers_sorted]
    tower_names = ["{}{}".format(bl, i+1) for i in range(len(ref_centroids))]

    # 5) From split upward, assign each band component to nearest ref centroid
    def nearest_ref(cx, cy):
        best_i = 0; best_d2 = 1e99
        for i, (rx, ry) in enumerate(ref_centroids):
            d2 = (cx-rx)*(cx-rx) + (cy-ry)*(cy-ry)
            if d2 < best_d2:
                best_d2 = d2; best_i = i
        return best_i

    for k in range(split_idx, N):
        infos = compinfo_per_band[k]
        if not infos: 
            continue
        # aggregate per tower
        agg = [ {"a":0.0,"cx":0.0,"cy":0.0} for _ in ref_centroids ]
        for ci in infos:
            i = nearest_ref(ci["cx"], ci["cy"])
            a = ci["area_doc"]; 
            agg[i]["a"]  += a
            agg[i]["cx"] += ci["cx"] * a
            agg[i]["cy"] += ci["cy"] * a
        for i, name in enumerate(tower_names):
            a = agg[i]["a"]
            if a > 0.0:
                cx = agg[i]["cx"]/a; cy = agg[i]["cy"]/a
                branches[name][k] = make_rec(k, a, cx, cy)

    return branches, split_idx

# =========================
# CORE GRAPH
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
        "grouping_mode": GROUPING_MODE,
        "branching": BRANCHING_ENABLED
    }
    if not obj_breps:
        return {"nodes": [], "links": [], "meta": meta, "_preview": None}

    H = float(floor_h_doc)
    tol_z = float(tol)

    # Group into buildings
    groups = _make_building_groups(obj_breps)
    Rhino.RhinoApp.WriteLine("[massing_graph] Building groups: {0}".format(len(groups)))
    building_ids = [ _excel_label(i) for i in range(len(groups)) ]

    preview_curves = {}
    diagnostics = {}

    for gid, group in enumerate(groups):
        bl = building_ids[gid]
        breps = [b for (_, b) in group]
        if not breps: 
            continue

        # Group bbox and Z extents
        zmin_g = +1e20; zmax_g = -1e20; bb_group = None
        for b in breps:
            zmin_b, zmax_b, bb = _bbox_z(b)
            zmin_g = min(zmin_g, zmin_b)
            zmax_g = max(zmax_g, zmax_b)
            bb_group = _bbox_union(bb_group, bb)
        if zmax_g <= zmin_g + tol_z:
            continue

        spans = _level_spans_from_bbox(zmin_g, zmax_g, H, tol_z)
        Rhino.RhinoApp.WriteLine("  [{}] spans: {}".format(bl, len(spans)))

        mesh_cache = [ _mesh_from_brep(b) for b in breps ]
        building_accum = {}  # per level (merged) for diagnostics

        mesh_fallback_hits = 0
        empty_kept = 0

        # Slice each brep at each mid-plane
        for (obj, b), mesh in zip(group, mesh_cache):
            zmin_b, zmax_b, _ = _bbox_z(b)
            for idx, z0, mid, z1 in spans:
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

                # Accumulate per-building (diagnostics)
                if a_doc > 0.0:
                    rec = building_accum.get(idx) or {"area_doc":0.0, "cx":0.0, "cy":0.0, "cz":mid, "z0":z0, "z1":z1, "bbox":bb_group}
                    rec["area_doc"] += a_doc
                    if xy_c:
                        cx, cy = xy_c
                        rec["cx"] += cx * a_doc; rec["cy"] += cy * a_doc
                    rec["bbox"] = _bbox_union(rec.get("bbox"), bb_group)
                    building_accum[idx] = rec

                # Keep all joined curves for branching-from-sections
                if joined:
                    key = (bl, idx); lstp = preview_curves.get(key) or []
                    lstp.extend(joined); preview_curves[key] = lstp

        # Fill empties for diagnostics
        if KEEP_EMPTY_LEVEL_NODES:
            for idx, z0, mid, z1 in spans:
                if idx not in building_accum:
                    building_accum[idx] = {"area_doc":0.0, "cx":0.0, "cy":0.0, "cz":mid, "z0":z0, "z1":z1, "bbox":bb_group}
                    empty_kept += 1

        diagnostics[bl] = {
            "members": len(breps),
            "bbox_height_m": _meters_from_doc_len(zmax_g - zmin_g),
            "levels_targeted": len(spans),
            "mesh_fallback_hits": mesh_fallback_hits,
            "empty_nodes_kept": empty_kept
        }

        # -------- Branches from section components (works for single joined Brep) --------
        curves_by_level = { idx: (preview_curves.get((bl, idx)) or []) for (idx,_,_,_) in spans }
        if BRANCHING_ENABLED:
            branches, split_idx = _build_branches_from_section_components(bl, spans, curves_by_level, BRANCH_MATCH_PAD, bb_group)
        else:
            # single branch using diagnostics accumulation
            branches = defaultdict(dict)
            for idx, z0, mid, z1 in spans:
                rec = building_accum.get(idx)
                if rec: branches[bl][idx] = rec
            split_idx = None

        # -------- Emit nodes/edges --------
        node_id = {}  # (branch, level_idx) -> node_id

        def _emit_node(branch_name, lvl_idx, rec):
            area_doc = float(rec.get("area_doc", 0.0))
            area_m2 = float(_m2_from_doc_area(area_doc)) if area_doc > 0.0 else 0.0
            if MIN_LEVEL_AREA_M2 > 0.0 and area_m2 < MIN_LEVEL_AREA_M2:
                return None
            w = area_doc if area_doc != 0.0 else 1.0
            cx = rec.get("cx",0.0)/w; cy = rec.get("cy",0.0)/w; cz = rec.get("cz",0.0)
            z0 = rec.get("z0", cz - 0.5*H); z1 = rec.get("z1", cz + 0.5*H)
            bb = rec.get("bbox") or rg.BoundingBox(rg.Point3d(cx,cy,z0), rg.Point3d(cx,cy,z1))

            clean_id = "{0}-L{1:02d}".format(branch_name, int(lvl_idx))
            nid = clean_id if USE_CLEAN_NODE_IDS else "{0}|L{1:02d}|{2}".format(branch_name, int(lvl_idx), uuid.uuid4().hex[:6])
            uid = uuid.uuid4().hex[:6] if INCLUDE_RANDOM_UID else None

            node_obj = {
                "id": nid,
                "type": "level",
                "building_id": bl,
                "branch_id": branch_name,
                "excel": branch_name,
                "level_index": int(lvl_idx),
                "z_span_m": [ float(_meters_from_doc_len(z0)), float(_meters_from_doc_len(z1)) ],
                "z_mid_m": float(_meters_from_doc_len(0.5*(z0+z1))),
                "area_m2": float(area_m2),
                "area_doc": float(area_doc),
                "centroid_doc": [float(cx), float(cy), float(cz)],
                "bbox_doc": [ [float(bb.Min.X), float(bb.Min.Y), float(bb.Min.Z)],
                              [float(bb.Max.X), float(bb.Max.Y), float(bb.Max.Z)] ]
            }
            if uid: node_obj["uid"] = uid
            nodes.append(node_obj)
            node_id[(branch_name, lvl_idx)] = nid
            return nid

        # emit vertical edges per branch
        for branch_name, lvlmap in branches.items():
            idxs = sorted(lvlmap.keys())
            prev_n = None
            for k in idxs:
                rec = lvlmap.get(k)
                if rec is None and KEEP_EMPTY_LEVEL_NODES:
                    # fabricate empty
                    i, z0, mid, z1 = spans[k]
                    rec = {"area_doc":0.0,"cx":0.0,"cy":0.0,"cz":mid,"z0":z0,"z1":z1,"bbox":bb_group}
                if rec is None:
                    continue
                nid = _emit_node(branch_name, k, rec)
                if prev_n and nid:
                    edges.append({"source": prev_n, "target": nid, "type": "vertical", "weight": 1.0})
                if nid: prev_n = nid

        # split edges from last podium node to each tower’s first node
        if BRANCHING_ENABLED and split_idx is not None and split_idx > 0:
            podium_node = node_id.get((bl, split_idx-1))
            if podium_node:
                for bname in branches.keys():
                    if bname == bl: 
                        continue
                    tower_node = node_id.get((bname, split_idx))
                    if tower_node:
                        edges.append({"source": podium_node, "target": tower_node, "type": "split", "weight": 1.0})

    # Optional PLOT node
    pc = _get_plot_center()
    if pc:
        nodes.append({"id":"PLOT", "type":"plot", "center_doc": list(map(float, pc))})
        by_building_branch = defaultdict(list)
        for n in nodes:
            if n.get("type") == "level":
                by_building_branch[n["building_id"]].append(n)
        for bl, lst in by_building_branch.items():
            lst_sorted = sorted(lst, key=lambda n: (n["branch_id"] != bl, n["level_index"]))
            if lst_sorted:
                edges.append({"source": lst_sorted[0]["id"], "target": "PLOT", "type": "plot", "weight": 1.0})

    meta["diagnostics_per_building"] = diagnostics
    return {"nodes": nodes, "links": edges, "meta": meta, "_preview": preview_curves}

# =========================
# SAVE / DIAGNOSTICS / PREVIEW
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
            bl, height, expected, len(lst)
        ))

def save_graph(path=KNOWLEDGE_PATH):
    try:
        tol = sc.doc.ModelAbsoluteTolerance or 0.001
        floor_h_doc = _doc_len_from_meters(FLOOR_HEIGHT_METERS)
        res = build_graph_from_active_doc(floor_h_doc, tol)
        data = {"nodes": res["nodes"], "links": res["links"], "meta": res["meta"]}

        # quick totals
        levels = [n for n in data["nodes"] if n.get("type") == "level"]
        total_area_m2 = sum(float(n.get("area_m2", 0.0)) for n in levels)

        # reference footprint: sum of max level area per building
        by_bld = defaultdict(list)
        for n in levels: by_bld[n.get("building_id","?")].append(n)
        site_footprint_ref_m2 = 0.0
        for bl, lst in by_bld.items():
            if lst:
                site_footprint_ref_m2 += max(float(n.get("area_m2",0.0)) for n in lst)

        _ensure_dir(path)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        Rhino.RhinoApp.WriteLine("[massing_graph] Saved: {0}".format(path))
        Rhino.RhinoApp.WriteLine("[massing_graph] Nodes: {0}, Edges: {1}".format(len(data["nodes"]), len(data["links"])))
        Rhino.RhinoApp.WriteLine("[massing_graph] Level nodes (total): {0}".format(len(levels)))
        Rhino.RhinoApp.WriteLine("[massing_graph] Total GFA: {:.3f} m²".format(total_area_m2))
        Rhino.RhinoApp.WriteLine("[massing_graph] Sum(max level per building): {:.3f} m²".format(site_footprint_ref_m2))

        _diagnose_level_counts(data["nodes"], data["meta"]["floor_height_m"])

        if SHOW_CONTOUR_PREVIEW and res.get("_preview"):
            _preview_on(res.get("_preview") or {})
        else:
            _preview_off()

    except Exception as e:
        Rhino.RhinoApp.WriteLine("[massing_graph] Save error: {0}".format(e))

# =========================
# LISTENER
# =========================
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
    threading.Thread(target=_run_later).start()

def _layer_name_from_event_obj(eobj):
    try: return sc.doc.Layers[eobj.Attributes.LayerIndex].FullPath
    except: return ""

def _layer_matches(path):
    return path.startswith(LAYER_MASSING_ROOT) or path == LAYER_PLOT

def _is_on_watched_layer(ro):
    try:
        path = sc.doc.Layers[ro.Attributes.LayerIndex].FullPath
        return _layer_matches(path)
    except:
        return False

def _on_add(sender, e):
    try:
        if e and e.TheObject and _is_on_watched_layer(e.TheObject):
            _debounce_trigger()
    except: pass

def _on_modify(sender, e):
    try:
        ro = e.RhinoObject
        if ro and _is_on_watched_layer(ro):
            _debounce_trigger()
    except: pass

def _on_replace(sender, e):
    try:
        ro = e.NewRhinoObject
        if ro and _is_on_watched_layer(ro):
            _debounce_trigger()
    except: pass

def _on_delete(sender, e):
    try:
        if e and e.ObjectId:
            _debounce_trigger()
    except: pass

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

# =========================
# ENTRY
# =========================
def main():
    save_graph()
    if ENABLE_LISTENER:
        setup_listener()

if __name__ == "__main__":
    main()
