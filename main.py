# -*- coding: utf-8 -*-
# MASSING → Graph → JSON → Live Preview (conduit, macro‑safe via Idle)
# - No baking. Draws nodes+edges with DisplayConduit.
# - Auto-zoom and auto-stop after 20 s using RhinoApp.Idle (works in macros).

import os, uuid, json, time
import Rhino
import Rhino.Geometry as rg
import Rhino.Display as rd
import scriptcontext as sc
import rhinoscriptsyntax as rs
import System.Drawing as sd
from collections import defaultdict
import string

# ---------- CONFIG ----------
LAYER_MASSING_ROOT = "MASSING"
LAYER_PLOT         = "PLOT"
FLOOR_HEIGHT       = 30.0
TOL                = sc.doc.ModelAbsoluteTolerance or 0.001
MIN_PIECE_AREA     = 0.0

REPO_ROOT      = r"C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis"
KNOWLEDGE_PATH = os.path.join(REPO_ROOT, "knowledge", "massing_graph.json")

# preview (conduit)
POINT_SIZE     = 8
EDGE_THICK_V   = 3
EDGE_THICK_P   = 3
EDGE_THICK_DEF = 2
LABELS         = True
AUTO_STOP_SEC  = 20.0
AUTO_ZOOM      = True
ZOOM_PADDING   = 10.0

STICKY_CONDUIT = "massing_graph_conduit"
STICKY_IDLE    = "massing_graph_idle_handler"
STICKY_DEADLINE= "massing_graph_stop_time"
# ----------------------------

# -------- utils --------
def ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.exists(d): os.makedirs(d)

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
    idxs = layer_indices_under(LAYER_MASSING_ROOT)
    if not idxs: return
    for obj in sc.doc.Objects:
        if obj.Attributes.LayerIndex in idxs:
            b = to_brep(obj.Geometry)
            if b: yield b

def get_plot_center():
    idxs = layer_indices_under(LAYER_PLOT)
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

def area_of_brep(b):
    amp = rg.AreaMassProperties.Compute(b)
    return amp.Area if amp else 0.0

def centroid_of_brep(b):
    amp = rg.AreaMassProperties.Compute(b)
    return amp.Centroid if amp else b.GetBoundingBox(True).Center

# -------- graph builder (one node per building+level) --------
def build_graph_from_active_doc(floor_h, tol):
    breps = list(iter_massing_breps())
    nodes, edges = [], []
    if not breps:
        return {"nodes": [], "links": [], "meta": {"source":"ActiveDoc"}}

    # one letter per solid
    letters = iter(string.ascii_uppercase)
    b_letter = {}
    for b in breps:
        try: b_letter[id(b)] = next(letters)
        except StopIteration: b_letter[id(b)] = "X"

    # slicers
    zmins, zmaxs = [], []
    for b in breps:
        z0,z1,_ = bbox_z(b); zmins.append(z0); zmaxs.append(z1)
    z_min, z_max = min(zmins), max(zmaxs)
    _, splitters = make_splitters(z_min, z_max, float(floor_h))

    # merge per building+level
    per_building_levels = {}
    for b in breps:
        bl = b_letter[id(b)]
        parts = rg.Brep.CreateBooleanSplit([b], splitters, tol) if splitters else [b]
        parts = list(parts) if parts else [b]
        accum = {}
        for p in parts:
            a = area_of_brep(p)
            if MIN_PIECE_AREA > 0.0 and a < MIN_PIECE_AREA: continue
            z0,z1,bb = bbox_z(p)
            zc = 0.5*(z0+z1)
            lvl = int((zc - z_min)//float(floor_h))
            c = centroid_of_brep(p)
            rec = accum.get(lvl)
            if rec is None:
                rec = {"area":0.0, "cx":0.0, "cy":0.0, "cz":0.0, "bbox":None, "z0":z0, "z1":z1}
            rec["area"] += a
            rec["cx"] += c.X * a; rec["cy"] += c.Y * a; rec["cz"] += c.Z * a
            rec["bbox"] = bbox_union(rec["bbox"], bb)
            if z0 < rec["z0"]: rec["z0"] = z0
            if z1 > rec["z1"]: rec["z1"] = z1
            accum[lvl] = rec
        per_building_levels[bl] = accum

    # nodes
    node_id = {}
    for bl, levels in per_building_levels.items():
        for lvl, rec in levels.items():
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
    for bl, levels in per_building_levels.items():
        lvls = sorted([int(k) for k in levels.keys()])
        for i in range(len(lvls)-1):
            l0, l1 = lvls[i], lvls[i+1]
            if (bl,l0) in node_id and (bl,l1) in node_id:
                edges.append({"source": node_id[(bl,l0)], "target": node_id[(bl,l1)], "type": "vertical", "weight": 1.0})

    # PLOT hub
    pc = get_plot_center()
    if pc:
        nodes.append({"id":"PLOT","type":"plot","centroid":[float(pc.X),float(pc.Y),float(pc.Z)]})
        for bl, levels in per_building_levels.items():
            nid = node_id.get((bl,0))
            if nid is None:
                avail = sorted([int(k) for k in levels.keys()])
                if avail: nid = node_id.get((bl, avail[0]))
            if nid:
                edges.append({"source": nid, "target": "PLOT", "type": "plot", "weight": 1.0})

    return {"nodes": nodes, "links": edges,
            "meta": {"floor_height": float(floor_h), "tolerance": float(tol), "source": "ActiveDoc"}}

# -------- conduit (no timers; macro‑safe) --------
def _color_from_letter(ch):
    palette = [
        (230,57,70),(29,53,87),(69,123,157),(168,218,220),
        (244,162,97),(231,111,81),(42,157,143),(38,70,83),
        (94,96,206),(255,183,3),(20,120,180),(140,140,140)
    ]
    try: i = ord(str(ch).upper()[0]) - ord('A')
    except: i = 0
    r,g,b = palette[i % len(palette)]
    return sd.Color.FromArgb(r,g,b)

def _bbox_from_nodes(nodes):
    pts = []
    for n in nodes:
        c = n.get("centroid")
        if c: pts.append(rg.Point3d(float(c[0]), float(c[1]), float(c[2]) if len(c)>2 else 0.0))
    if not pts: return None
    bb = rg.BoundingBox(pts)
    if not bb.IsValid: return None
    minpt = rg.Point3d(bb.Min.X - ZOOM_PADDING, bb.Min.Y - ZOOM_PADDING, bb.Min.Z - ZOOM_PADDING)
    maxpt = rg.Point3d(bb.Max.X + ZOOM_PADDING, bb.Max.Y + ZOOM_PADDING, bb.Max.Z + ZOOM_PADDING)
    return rg.BoundingBox(minpt, maxpt)

class MassingConduit(rd.DisplayConduit):
    def __init__(self, nodes, links):
        rd.DisplayConduit.__init__(self)
        self.nodes = nodes[:]
        self.links = links[:]
        self.by_id = dict((n.get("id"), n) for n in self.nodes if n.get("id"))
        self.c_plot = sd.Color.White
        self.c_edge_plot = sd.Color.FromArgb(230,230,230)
        self.c_edge_vert = sd.Color.FromArgb(200,200,200)
        self.c_edge_def  = sd.Color.FromArgb(160,160,160)

    def _draw(self, e):
        # edges
        for ed in self.links:
            sn = self.by_id.get(ed.get("source"))
            tn = self.by_id.get(ed.get("target"))
            if not sn or not tn: continue
            ps = sn.get("centroid"); pt = tn.get("centroid")
            if not ps or not pt: continue
            p1 = rg.Point3d(ps[0], ps[1], ps[2] if len(ps)>2 else 0.0)
            p2 = rg.Point3d(pt[0], pt[1], pt[2] if len(pt)>2 else 0.0)
            t = ed.get("type") or ""
            if t == "vertical":
                e.Display.DrawLine(rg.Line(p1,p2), self.c_edge_vert, EDGE_THICK_V)
            elif t == "plot":
                e.Display.DrawLine(rg.Line(p1,p2), self.c_edge_plot, EDGE_THICK_P)
            else:
                e.Display.DrawLine(rg.Line(p1,p2), self.c_edge_def, EDGE_THICK_DEF)
        # nodes
        for n in self.nodes:
            c = n.get("centroid")
            if not c: continue
            pt = rg.Point3d(c[0], c[1], c[2] if len(c)>2 else 0.0)
            if n.get("type") == "plot":
                e.Display.DrawDot(pt, "PLOT", self.c_plot, sd.Color.Black)
            else:
                bid = n.get("building_id","?")
                col = _color_from_letter(bid[:1] if bid else "A")
                e.Display.DrawPoint(pt, rd.PointStyle.RoundSimple, POINT_SIZE, col)
                if LABELS:
                    lvl = n.get("level",0)
                    e.Display.DrawDot(pt, "{} L{:02d}".format(bid, int(lvl)), col, sd.Color.Black)

    def DrawForeground(self, e): self._draw(e)
    def DrawOverlay(self, e):    self._draw(e)  # extra safety across display modes

def _idle_stop_sender(sender, args):
    """Idle handler: auto-stop after deadline."""
    try:
        deadline = sc.sticky.get(STICKY_DEADLINE)
        conduit  = sc.sticky.get(STICKY_CONDUIT)
        if not conduit or deadline is None:
            Rhino.RhinoApp.Idle -= _idle_stop_sender
            sc.sticky[STICKY_IDLE] = None
            return
        if time.time() >= deadline:
            conduit.Enabled = False
            sc.sticky[STICKY_CONDUIT] = None
            Rhino.RhinoApp.Idle -= _idle_stop_sender
            sc.sticky[STICKY_IDLE] = None
            rs.Redraw()
            Rhino.RhinoApp.WriteLine("[massing_preview] auto-stopped after {}s".format(int(AUTO_STOP_SEC)))
    except:
        # best effort cleanup
        try: Rhino.RhinoApp.Idle -= _idle_stop_sender
        except: pass
        sc.sticky[STICKY_IDLE] = None

def start_preview(nodes, links):
    # kill previous
    prev = sc.sticky.get(STICKY_CONDUIT)
    if prev:
        try: prev.Enabled = False
        except: pass
        sc.sticky[STICKY_CONDUIT] = None
    # conduit
    c = MassingConduit(nodes, links)
    c.Enabled = True
    sc.sticky[STICKY_CONDUIT] = c
    # idle auto-stop
    try:
        if sc.sticky.get(STICKY_IDLE):
            Rhino.RhinoApp.Idle -= _idle_stop_sender
    except: pass
    sc.sticky[STICKY_DEADLINE] = time.time() + AUTO_STOP_SEC
    Rhino.RhinoApp.Idle += _idle_stop_sender
    sc.sticky[STICKY_IDLE] = True
    # zoom
    if AUTO_ZOOM:
        bb = _bbox_from_nodes(nodes)
        if bb and bb.IsValid:
            v = sc.doc.Views.ActiveView
            if v:
                v.ActiveViewport.ZoomBoundingBox(bb)
                v.Redraw()
    rs.Redraw()
    Rhino.RhinoApp.WriteLine("[massing_preview] live preview ON ({}s)".format(int(AUTO_STOP_SEC)))

def stop_preview():
    prev = sc.sticky.get(STICKY_CONDUIT)
    if prev:
        try: prev.Enabled = False
        except: pass
        sc.sticky[STICKY_CONDUIT] = None
    try:
        Rhino.RhinoApp.Idle -= _idle_stop_sender
    except: pass
    sc.sticky[STICKY_IDLE] = None
    sc.sticky[STICKY_DEADLINE] = None
    rs.Redraw()
    Rhino.RhinoApp.WriteLine("[massing_preview] stopped.")

# -------- main (for macro) --------
def main():
    data = build_graph_from_active_doc(FLOOR_HEIGHT, TOL)
    ensure_dir(KNOWLEDGE_PATH)
    with open(KNOWLEDGE_PATH, "w") as f:
        json.dump(data, f, indent=2)
    Rhino.RhinoApp.WriteLine("Saved graph: {}".format(KNOWLEDGE_PATH))
    Rhino.RhinoApp.WriteLine("Nodes: {}, Edges: {}".format(len(data["nodes"]), len(data["links"])))
    start_preview(data["nodes"], data["links"])

if __name__ == "__main__":
    main()
