# -*- coding: utf-8 -*-
# rhino/massing_graph.py — robust MASSING scan + Surface handling + tower branching (IronPython-safe)
# + street-compat line network (nodes type:'street', edges type:'street' with u/v + 2D line)

import os, uuid, json, time, math
import Rhino
import Rhino.Geometry as rg
import Rhino.Geometry.Intersect as rgi
import scriptcontext as sc
from System.Drawing import Color
from collections import defaultdict

# ========== CONFIG ==========
LAYER_MASSING_ROOT = "MASSING"
LAYER_PLOT = "PLOT"

FLOOR_HEIGHT_METERS = 3.0
TOL = sc.doc.ModelAbsoluteTolerance or 0.001

# Grouping / branching
# "none" | "by_touching" | "by_sublayer"
GROUPING_MODE = "by_touching"
GROUP_GAP_TOL = (sc.doc.ModelAbsoluteTolerance or 0.001) * 2.0

BRANCHING_ENABLED  = True
BRANCH_MATCH_PAD   = GROUP_GAP_TOL

KEEP_EMPTY_LEVEL_NODES = True
MIN_LEVEL_AREA_M2 = 0.0

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(THIS_DIR)
KNOWLEDGE_PATH = os.path.join(PROJECT_DIR, "knowledge", "massing_graph.json")

SHOW_CONTOUR_PREVIEW = False
PREVIEW_LINE_WIDTH   = 2
PREVIEW_PALETTE = [
    Color.FromArgb(255, 230, 25, 75),
    Color.FromArgb(255, 60, 180, 75),
    Color.FromArgb(255, 0, 130, 200),
    Color.FromArgb(255, 245, 130, 48),
    Color.FromArgb(255, 145, 30, 180),
    Color.FromArgb(255, 70, 240, 240),
    Color.FromArgb(255, 240, 50, 230),
]

USE_CLEAN_NODE_IDS = True
INCLUDE_RANDOM_UID = False
UNION_TOL_MULT = 4.0

# ---- Street / line-network (from main) ----
INCLUDE_LINE_NETWORK = True
DETECT_LINE_INTERSECTIONS = True             # compute curve-curve intersections
MAX_CURVE_PAIRS_FOR_INTERSECTIONS = 3000     # safety cap for O(n^2)
CONNECT_BUILDINGS_TO_NEAREST_LINE = True     # add "access" edges from buildings to nearest line node
ACCESS_SEARCH_RADIUS = 50.0                  # in doc units (0 = unlimited)

STREET_SCHEMA_IDS_PREFIX = "street_v"        # id prefix for street nodes
EXPORT_EDGES_AND_LINKS   = True              # write both 'edges' and 'links' for compatibility

# ========== UNITS ==========
def _uu(src, dst):
    try: return Rhino.RhinoMath.UnitScale(src, dst)
    except: return 1.0

def _doc_len_from_meters(L):       return float(L) * _uu(Rhino.UnitSystem.Meters, sc.doc.ModelUnitSystem)
def _meters_from_doc_len(L):       return float(L) * _uu(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Meters)
def _m2_from_doc_area(a_doc):
    s = _uu(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Meters)
    return float(a_doc) * (s ** 2)

# ========== UTILS ==========
def _ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        try: os.makedirs(d)
        except: Rhino.RhinoApp.WriteLine("[massing_graph] Could not create folder: " + d)

def _to_brep(g):
    # Accept Brep, Extrusion, and Surface (convert to Brep); best-effort SubD
    try:
        if isinstance(g, rg.Brep):
            return g
        if isinstance(g, rg.Extrusion):
            return g.ToBrep(True)
        if isinstance(g, rg.Surface):
            b = rg.Brep.CreateFromSurface(g)
            if b: return b
        if hasattr(rg, "SubD") and isinstance(g, rg.SubD):
            try:
                b = rg.Brep.CreateFromSubD(g)
                if b: return b
            except: pass
    except: pass
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

# ---- MASSING layer helpers (very permissive) ----
def _path_has_segment(fullpath, name):
    try:
        parts = (fullpath or "").split("::")
        n = (name or "").lower()
        for p in parts:
            if (p or "").strip().lower() == n:
                return True
    except:
        pass
    return False

def _iter_massing_breps():
    """Yield (object, Brep) for geometry under MASSING tree."""
    objs = sc.doc.Objects or []
    root = LAYER_MASSING_ROOT.lower()
    for obj in objs:
        try:
            lyr = sc.doc.Layers[obj.Attributes.LayerIndex]
            full = getattr(lyr, "FullPath", lyr.Name) or lyr.Name
            if not _path_has_segment(full, root):
                continue
            b = _to_brep(obj.Geometry)
            if b: yield (obj, b)
        except:
            pass

def _iter_massing_curves():
    """Yield Rhino.Geometry.Curve under MASSING tree (drop near-zero length)."""
    root = LAYER_MASSING_ROOT.lower()
    for obj in sc.doc.Objects or []:
        try:
            lyr = sc.doc.Layers[obj.Attributes.LayerIndex]
            full = getattr(lyr, "FullPath", lyr.Name) or lyr.Name
            if not _path_has_segment(full, root):
                continue
            g = obj.Geometry
            if isinstance(g, rg.Curve) and g.GetLength() > (TOL * 5.0):
                yield g
        except:
            pass

def _get_plot_center():
    try:
        for lyr in sc.doc.Layers:
            if lyr and _path_has_segment(lyr.FullPath or lyr.Name, LAYER_PLOT):
                rhobjs = sc.doc.Objects.FindByLayer(lyr.Index)
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

# ========== GROUPING ==========
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
        try: items.append({"pair": (obj, b), "bb": b.GetBoundingBox(True), "brep": b})
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
            bb_a = items[a]["bb"]
            for j in range(n):
                if visited[j]: continue
                if not _bbox_overlap(bb_a, items[j]["bb"], pad): 
                    continue
                visited[j] = True
                queue.append(j)
                comp.append(items[j]["pair"])
        groups.append(comp)
    return groups

def _make_building_groups(obj_breps):
    if GROUPING_MODE == "by_sublayer":  return _group_breps_by_sublayer(obj_breps)
    if GROUPING_MODE == "by_touching":  return _group_breps_by_touching(obj_breps, GROUP_GAP_TOL)
    return [[p] for p in obj_breps]

# ========== AREA / SECTIONS ==========
def _area_union_centroid_on_plane(curves, plane, tol):
    if not curves: return 0.0, None, []
    projected = []
    for c in curves:
        try: projected.append(rg.Curve.ProjectToPlane(c, plane))
        except: pass
    try:
        joined = rg.Curve.JoinCurves(projected, float(tol) * float(UNION_TOL_MULT)) or []
    except:
        joined = projected
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
    if total > 0.0: return total, (cx/total, cy/total), candidates
    return 0.0, None, candidates

# ========== PREVIEW ==========
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

# ========== MESH FALLBACK ==========
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

# ========== LEVEL SPANS ==========
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

# ========== BRANCHING FROM SECTION CURVES ==========
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
    if area > 0.0: return area, cx/area, cy/area
    return 0.0, 0.0, 0.0

def _build_branches_from_section_components(bl, spans, curves_by_level, pad, bb_group):
    if not spans: return {bl:{}}, None
    N = len(spans)

    compinfo_per_band = []
    for k in range(N):
        curves = curves_by_level.get(k) or []
        if not curves:
            compinfo_per_band.append([])
            continue
        items = [(i, _curve_bbox_xy(curves[i])) for i in range(len(curves))]
        comps = _connected_components_xy(items, pad=pad)
        infos = []
        for comp in comps:
            comp_curves = [curves[i] for i in comp]
            a_doc, cx, cy = _area_centroid_of_curves(comp_curves)
            infos.append({"idxs": comp, "area_doc":a_doc, "cx":cx, "cy":cy})
        compinfo_per_band.append(infos)

    split_idx = None
    for k, infos in enumerate(compinfo_per_band):
        if len(infos) >= 2:
            split_idx = k
            break

    branches = defaultdict(dict)

    def make_rec(k, a_doc, cx, cy):
        idx, z0, mid, z1 = spans[k]
        return {"area_doc": float(a_doc), "cx": float(cx), "cy": float(cy),
                "cz": float(mid), "z0": float(z0), "z1": float(z1), "bbox": bb_group}

    if split_idx is None:
        for k, infos in enumerate(compinfo_per_band):
            if not infos: continue
            a_doc = sum(ci["area_doc"] for ci in infos)
            if a_doc > 0.0:
                cx = sum(ci["cx"]*ci["area_doc"] for ci in infos)/a_doc
                cy = sum(ci["cy"]*ci["area_doc"] for ci in infos)/a_doc
            else:
                cx = cy = 0.0
            branches[bl][k] = make_rec(k, a_doc, cx, cy)
        return branches, None

    for k in range(0, split_idx):
        infos = compinfo_per_band[k]
        if not infos: continue
        a_doc = sum(ci["area_doc"] for ci in infos)
        if a_doc > 0.0:
            cx = sum(ci["cx"]*ci["area_doc"] for ci in infos)/a_doc
            cy = sum(ci["cy"]*ci["area_doc"] for ci in infos)/a_doc
        else:
            cx = cy = 0.0
        branches[bl][k] = make_rec(k, a_doc, cx, cy)

    split_infos = compinfo_per_band[split_idx]
    towers_sorted = sorted(split_infos, key=lambda ci: (ci["cx"], ci["cy"]))
    ref_centroids = [(ci["cx"], ci["cy"]) for ci in towers_sorted]
    tower_names = ["{}{}".format(bl, i+1) for i in range(len(ref_centroids))]

    def nearest_ref(cx, cy):
        best_i = 0; best_d2 = 1e99
        for i, (rx, ry) in enumerate(ref_centroids):
            d2 = (cx-rx)*(cx-rx) + (cy-ry)*(cy-ry)
            if d2 < best_d2:
                best_d2 = d2; best_i = i
        return best_i

    for k in range(split_idx, N):
        infos = compinfo_per_band[k]
        if not infos: continue
        agg = [ {"a":0.0,"cx":0.0,"cy":0.0} for _ in ref_centroids ]
        for ci in infos:
            i = nearest_ref(ci["cx"], ci["cy"])
            a = ci["area_doc"]
            agg[i]["a"]  += a
            agg[i]["cx"] += ci["cx"] * a
            agg[i]["cy"] += ci["cy"] * a
        for i, name in enumerate(tower_names):
            a = agg[i]["a"]
            if a > 0.0:
                cx = agg[i]["cx"]/a; cy = agg[i]["cy"]/a
                branches[name][k] = make_rec(k, a, cx, cy)

    return branches, split_idx

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

# ========== CORE GRAPH ==========
def build_graph_from_active_doc(floor_h_doc, tol):
    obj_breps = list(_iter_massing_breps()) or []
    Rhino.RhinoApp.WriteLine("[massing_graph] MASSING objects found: " + str(len(obj_breps)))

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
        return {"nodes": [], "links": [], "edges": [], "meta": meta, "_preview": None}

    H = float(floor_h_doc)
    tol_z = float(tol)

    groups = _make_building_groups(obj_breps)
    Rhino.RhinoApp.WriteLine("[massing_graph] Building groups: " + str(len(groups)))
    building_ids = [ _excel_label(i) for i in range(len(groups)) ]

    preview_curves = {}
    diagnostics = {}

    # Keep lowest level node per building-branch for access edges
    ground_node_per_branch = {}

    for gid, group in enumerate(groups):
        bl = building_ids[gid]
        breps = [b for (_, b) in group]
        if not breps: 
            continue

        zmin_g = +1e20; zmax_g = -1e20; bb_group = None
        for b in breps:
            zmin_b, zmax_b, bb = _bbox_z(b)
            zmin_g = min(zmin_g, zmin_b)
            zmax_g = max(zmax_g, zmax_b)
            bb_group = _bbox_union(bb_group, bb)
        if zmax_g <= zmin_g + tol_z:  # flat
            continue

        spans = _level_spans_from_bbox(zmin_g, zmax_g, H, tol_z)
        Rhino.RhinoApp.WriteLine("  [" + bl + "] spans: " + str(len(spans)))

        mesh_cache = [ _mesh_from_brep(b) for b in breps ]
        building_accum = {}

        mesh_fallback_hits = 0
        empty_kept = 0

        for (obj, b), mesh in zip(group, mesh_cache):
            zmin_b, zmax_b, _ = _bbox_z(b)
            for idx, z0, mid, z1 in spans:
                if z1 < zmin_b - tol_z or z0 > zmax_b + tol_z:
                    continue
                plane = rg.Plane(rg.Point3d(0,0,mid), rg.Vector3d.ZAxis)

                crvs = []
                try:
                    rc, crv_list, pts = rgi.Intersection.BrepPlane(b, plane, float(tol))
                    if rc and crv_list: crvs = list(crv_list)
                except: pass

                a_doc, xy_c, joined = _area_union_centroid_on_plane(crvs, plane, float(tol))

                if a_doc <= 0.0 and mesh is not None:
                    try: pls = rgi.Intersection.MeshPlane(mesh, plane)
                    except: pls = None
                    if pls and len(pls) > 0:
                        crvs2 = _polylines_to_nurbs_curves(pls)
                        a2, c2, j2 = _area_union_centroid_on_plane(crvs2, plane, float(tol))
                        if a2 > 0.0:
                            a_doc, xy_c, joined = a2, c2, j2
                            mesh_fallback_hits += 1

                if a_doc > 0.0:
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

        curves_by_level = { idx: (preview_curves.get((bl, idx)) or []) for (idx,_,_,_) in spans }
        if BRANCHING_ENABLED:
            branches, split_idx = _build_branches_from_section_components(bl, spans, curves_by_level, BRANCH_MATCH_PAD, bb_group)
        else:
            branches, split_idx = defaultdict(dict), None
            for idx, z0, mid, z1 in spans:
                rec = building_accum.get(idx)
                if rec: branches[bl][idx] = rec

        node_id = {}  # (branch, level_idx) -> node_id

        def _emit_node(branch_name, lvl_idx, rec):
            area_doc = float(rec.get("area_doc", 0.0))
            area_m2  = float(_m2_from_doc_area(area_doc)) if area_doc > 0.0 else 0.0
            if MIN_LEVEL_AREA_M2 > 0.0 and area_m2 < MIN_LEVEL_AREA_M2: return None
            w = area_doc if area_doc != 0.0 else 1.0
            cx = rec.get("cx",0.0)/w; cy = rec.get("cy",0.0)/w; cz = rec.get("cz",0.0)
            z0 = rec.get("z0", cz - 0.5*H); z1 = rec.get("z1", cz + 0.5*H)
            bb = rec.get("bbox") or rg.BoundingBox(rg.Point3d(cx,cy,z0), rg.Point3d(cx,cy,z1))

            clean_id = branch_name + "-L" + str(int(lvl_idx)).zfill(2)
            nid = clean_id if USE_CLEAN_NODE_IDS else (branch_name + "|L" + str(int(lvl_idx)).zfill(2) + "|" + uuid.uuid4().hex[:6])
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
            # keep lowest for “access” connections later
            if (branch_name, "ground") not in ground_node_per_branch or lvl_idx < ground_node_per_branch[(branch_name, "ground")][0]:
                ground_node_per_branch[(branch_name, "ground")] = (lvl_idx, nid, node_obj)
            return nid

        # vertical edges per branch
        for branch_name, lvlmap in branches.items():
            idxs = sorted(lvlmap.keys())
            prev_n = None
            for k in idxs:
                rec = lvlmap.get(k)
                if rec is None and KEEP_EMPTY_LEVEL_NODES:
                    i, z0, mid, z1 = spans[k]
                    rec = {"area_doc":0.0,"cx":0.0,"cy":0.0,"cz":mid,"z0":z0,"z1":z1,"bbox":bb_group}
                if rec is None: continue
                nid = _emit_node(branch_name, k, rec)
                if prev_n and nid:
                    edges.append({"u": prev_n, "v": nid, "type": "vertical", "weight": 1.0})
                if nid: prev_n = nid

        # split edges
        if BRANCHING_ENABLED and split_idx is not None and split_idx > 0:
            podium_node = node_id.get((bl, split_idx-1))
            if podium_node:
                for bname in list(branches.keys()):
                    if bname == bl: continue
                    tower_node = node_id.get((bname, split_idx))
                    if tower_node:
                        edges.append({"u": podium_node, "v": tower_node, "type": "split", "weight": 1.0})

    # ---- optional PLOT hub (kept; light) ----
    pc = _get_plot_center()
    if pc:
        nodes.append({"id":"PLOT", "type":"plot", "center_doc": [float(pc[0]), float(pc[1]), float(pc[2])]})
        # connect the first node of each building's main branch (original bl)
        by_building_branch = defaultdict(list)
        for n in nodes:
            if n.get("type") == "level":
                by_building_branch[n["building_id"]].append(n)
        for bl, lst in by_building_branch.items():
            lst_sorted = sorted(lst, key=lambda n: (n.get("branch_id") != bl, n.get("level_index", 1e9)))
            if lst_sorted:
                edges.append({"u": lst_sorted[0]["id"], "v": "PLOT", "type": "plot", "weight": 1.0})

    # ---------------------------------------------------------------------
    # 2) Curves → street line network (from main; street schema compatible)
    # ---------------------------------------------------------------------
    line_node_ids = {}   # quantized point key -> node_id
    line_nodes_pts = {}  # node_id -> Point3d
    line_node_seq = [0]  # mutable counter for IronPython

    def _ensure_line_node(pt):
        k = _pt_key(pt, tol)
        nid = line_node_ids.get(k)
        if nid:
            return nid
        line_node_seq[0] += 1
        seq = line_node_seq[0]
        nid = "%s%d" % (STREET_SCHEMA_IDS_PREFIX, seq)
        nodes.append({
            "id": nid,
            "type": "street",
            "x": float(pt.X),
            "y": float(pt.Y)
        })
        line_node_ids[k] = nid
        line_nodes_pts[nid] = rg.Point3d(pt.X, pt.Y, pt.Z)
        return nid

    if INCLUDE_LINE_NETWORK:
        curves = list(_iter_massing_curves())
        simple_curves = []
        for c in curves:
            simple_curves.extend(_curve_segments(c))

        # collect intersections per curve (normalized)
        curve_to_t_hits = {}
        if DETECT_LINE_INTERSECTIONS and len(simple_curves) > 1:
            n = len(simple_curves)
            max_pairs = min(MAX_CURVE_PAIRS_FOR_INTERSECTIONS, n*(n-1)//2)
            cnt = 0
            for i in range(n):
                ci = simple_curves[i]; di = ci.Domain
                for j in range(i+1, n):
                    if cnt >= max_pairs:
                        break
                    cj = simple_curves[j]; dj = cj.Domain
                    events = rgi.Intersection.CurveCurve(ci, cj, tol, tol)
                    cnt += 1
                    if events and events.Count > 0:
                        for ev in events:
                            try:
                                ti = (ev.ParameterA - di.T0) / (di.T1 - di.T0) if (di.T1 - di.T0) != 0 else 0.0
                                tj = (ev.ParameterB - dj.T0) / (dj.T1 - dj.T0) if (dj.T1 - dj.T0) != 0 else 0.0
                            except:
                                continue
                            ti = max(0.0, min(1.0, float(ti)))
                            tj = max(0.0, min(1.0, float(tj)))
                            curve_to_t_hits.setdefault(ci, set()).add(ti)
                            curve_to_t_hits.setdefault(cj, set()).add(tj)

        # split by endpoints + intersections → street edges
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
                    "u": na,
                    "v": nb,
                    "type": "street",
                    "distance": float(math.hypot(pb.X - pa.X, pb.Y - pa.Y)),
                    "line": [
                        [float(pa.X), float(pa.Y)],
                        [float(pb.X), float(pb.Y)]
                    ]
                })

    # 3) Connect building ground nodes to nearest street node
    if INCLUDE_LINE_NETWORK and CONNECT_BUILDINGS_TO_NEAREST_LINE and line_nodes_pts:
        ln_items = list(line_nodes_pts.items())
        for (branch_name, _), (lvl_idx, nid, node_obj) in ground_node_per_branch.items():
            bp = rg.Point3d(node_obj["centroid_doc"][0], node_obj["centroid_doc"][1], node_obj["centroid_doc"][2])
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
                    "u": nid,
                    "v": best,
                    "type": "access",
                    "distance": float(math.sqrt(best_d2))
                })

    meta["diagnostics_per_building"] = diagnostics
    out = {"nodes": nodes, "edges": edges, "meta": meta}
    # Mirror to links for consumers that expect 'links'
    out["links"] = edges
    return out

# ========== SAVE ==========
def _diagnose_level_counts(nodes, H_m):
    from collections import defaultdict
    by_bld = defaultdict(list)
    for n in nodes:
        if n.get("type") == "level":
            by_bld[n["building_id"]].append(n)
    Rhino.RhinoApp.WriteLine("— Level diagnostics —  floor_h_m=" + str(round(H_m, 3)))
    for bl, lst in sorted(by_bld.items()):
        if not lst:
            Rhino.RhinoApp.WriteLine(" {} : no levels".format(bl))
            continue
        zmins = [n["z_span_m"][0] for n in lst]
        zmaxs = [n["z_span_m"][1] for n in lst]
        zmin = min(zmins); zmax = max(zmaxs); height = max(0.0, zmax - zmin)
        try:
            expected = int(math.floor(height / H_m)) + (1 if (height % H_m) > 1e-6 else 0)
        except:
            expected = 0
        Rhino.RhinoApp.WriteLine(" {} : bboxHeight={} m | floors_expected={} | nodes={}".format(
            bl, round(height,3), expected, len(lst)
        ))

def save_graph(path=KNOWLEDGE_PATH):
    try:
        tol = sc.doc.ModelAbsoluteTolerance or 0.001
        floor_h_doc = _doc_len_from_meters(FLOOR_HEIGHT_METERS)
        res = build_graph_from_active_doc(floor_h_doc, tol)

        # ensure both 'edges' and 'links' are present
        data = {"nodes": res["nodes"], "edges": res.get("edges") or res.get("links") or [], "meta": res["meta"]}
        data["links"] = data["edges"]

        levels = [n for n in data["nodes"] if n.get("type") == "level"]
        total_area_m2 = sum(float(n.get("area_m2", 0.0)) for n in levels)

        from collections import defaultdict
        by_bld = defaultdict(list)
        for n in levels: by_bld[n.get("building_id","?")].append(n)
        site_footprint_ref_m2 = 0.0
        for bl, lst in by_bld.items():
            if lst:
                site_footprint_ref_m2 += max(float(n.get("area_m2",0.0)) for n in lst)

        _ensure_dir(path)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        Rhino.RhinoApp.WriteLine("[massing_graph] Saved: " + path)
        Rhino.RhinoApp.WriteLine("[massing_graph] Nodes: " + str(len(data["nodes"])) + ", Edges: " + str(len(data["links"])))
        Rhino.RhinoApp.WriteLine("[massing_graph] Level nodes (total): " + str(len(levels)))
        Rhino.RhinoApp.WriteLine("[massing_graph] Total GFA: " + str(round(total_area_m2, 3)) + " m^2")
        Rhino.RhinoApp.WriteLine("[massing_graph] Sum(max level per building): " + str(round(site_footprint_ref_m2, 3)) + " m^2")
        try:
            Rhino.RhinoApp.WriteLine("[massing_graph] Masterplan total sqm: {0:.2f} m^2".format(total_area_m2))
        except:
            Rhino.RhinoApp.WriteLine("[massing_graph] Masterplan total sqm: " + str(round(total_area_m2, 2)) + " m^2")

        try:
            _diagnose_level_counts(data["nodes"], data["meta"]["floor_height_m"])
        except:
            Rhino.RhinoApp.WriteLine("[massing_graph] (diagnostics skipped)")

        if SHOW_CONTOUR_PREVIEW and res.get("_preview"): _preview_on(res.get("_preview") or {})
        else: _preview_off()

    except Exception as e:
        Rhino.RhinoApp.WriteLine("[massing_graph] Save error: " + str(e))

# ========== ENTRY ==========
def main():
    save_graph()

if __name__ == "__main__":
    main()
