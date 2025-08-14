# evaluation_preview.py - IronPython 2.7
# Preview: boundary (red), typed nodes inside boundary, edges, and score.

import os
import json
import System
import Rhino
import Rhino.Geometry as rg
import Rhino.Geometry.Intersect as rgi
import scriptcontext as sc

from System.Drawing import Color

# ---- node category colors ----
CAT_COLORS = {
    "Residential": Color.FromArgb(220, 45, 70),
    "Office":      Color.FromArgb(0, 112, 184),
    "Leisure":     Color.FromArgb(0, 170, 70),
    "Cultural":    Color.FromArgb(140, 0, 140),
    "Green":       Color.FromArgb(80, 180, 120),
}

# ---- edge styles ----
EDGE_COLORS = {
    "street": Color.FromArgb(138, 138, 138),
    "access": Color.FromArgb(0, 0, 0),
}
EDGE_THICKNESS = {
    "street": 2,
    "access": 1,
}


def categorize_node(attrs):
    """Heuristic categorization based on OSM-like attributes embedded in node data."""
    try:
        tags = {}
        for k, v in attrs.items():
            try:
                tags[str(k).lower()] = str(v).lower()
            except:
                pass

        b = tags.get("building")

        residential = set([
            "apartments", "house", "residential", "semidetached_house",
            "terrace", "bungalow", "detached", "dormitory"
        ])
        office = set([
            "office", "commercial", "industrial", "retail", "manufacture",
            "warehouse", "service"
        ])
        cultural = set(["college", "school", "kindergarten", "government", "civic", "church", "fire_station", "prison"])
        leisure = set(["hotel", "boathouse", "houseboat", "bridge"])
        green = set(["greenhouse", "allotment_house"])

        if b == "yes":
            return "Residential"
        if b in residential:
            return "Residential"
        if b in office:
            return "Office"
        if b in cultural:
            return "Cultural"
        if b in leisure:
            return "Leisure"
        if b in green:
            return "Green"

        amen = tags.get("amenity", "")
        if ("museum" in amen) or ("theatre" in amen) or ("gallery" in amen):
            return "Cultural"

        leis = tags.get("leisure", "")
        if ("park" in leis) or ("recreation" in leis) or ("garden" in leis):
            return "Leisure"

        if tags.get("landuse") in ["grass", "meadow"] or "green" in tags.get("type", ""):
            return "Green"

        return None
    except:
        return None


class EvaluationConduit(Rhino.Display.DisplayConduit):
    """DisplayConduit that draws:
       - boundary polyline (red)
       - edges (street/access) that are inside/overlap the boundary (or all edges if no boundary)
       - categorized nodes that are inside the boundary (or all nodes if no boundary)
       - evaluation score label (if available)
    """
    def __init__(self, job_dir):
        self.job_dir = job_dir
        self.boundary_pts = []
        self.boundary_curve = None
        self.score = None
        self.points_by_cat = {}               # cat -> [Point3d, ...]
        self.edge_curves = {"street": [], "access": []}  # type -> [Curve, ...]
        self._load_data()

    # -------- geometry tests --------
    def _inside_boundary(self, pt):
        if self.boundary_curve is None:
            return True  # if no boundary, treat as inside for preview convenience
        tol = sc.doc.ModelAbsoluteTolerance
        try:
            c = self.boundary_curve.Contains(pt, tol)
        except:
            c = self.boundary_curve.Contains(pt)
        return (c == rg.PointContainment.Inside) or (c == rg.PointContainment.Coincident)

    def _edge_visible(self, crv, pts=None):
        """Return True if the edge should be drawn: endpoint inside, midpoint inside,
        or intersects the boundary. If no boundary, always True."""
        if self.boundary_curve is None:
            return True

        # Endpoints fast check
        try:
            if pts and len(pts) >= 2:
                p0 = pts[0]
                p1 = pts[-1]
            else:
                p0 = crv.PointAtStart
                p1 = crv.PointAtEnd
        except:
            p0 = crv.PointAtStart
            p1 = crv.PointAtEnd

        if self._inside_boundary(p0) or self._inside_boundary(p1):
            return True

        # Midpoint check
        try:
            t_mid = 0.5 * (crv.Domain.T0 + crv.Domain.T1)
            if self._inside_boundary(crv.PointAt(t_mid)):
                return True
        except:
            pass

        # Intersection test
        tol = sc.doc.ModelAbsoluteTolerance
        try:
            rc, events = rgi.Intersection.CurveCurve(self.boundary_curve, crv, tol, tol)
            if rc and events and events.Count > 0:
                return True
        except:
            pass

        return False

    # -------- data loading --------
    def _load_data(self):
        try:
            bpath = os.path.join(self.job_dir, "boundary.json")
            epath = os.path.join(self.job_dir, "evaluation.json")
            gpath = os.path.join(self.job_dir, "graph.json")

            # boundary
            self.boundary_pts = []
            self.boundary_curve = None
            if os.path.exists(bpath):
                try:
                    xy = json.load(open(bpath, "r"))
                    self.boundary_pts = [rg.Point3d(float(x), float(y), 0.0) for (x, y) in xy]
                    if len(self.boundary_pts) >= 3:
                        self.boundary_curve = rg.Polyline(self.boundary_pts).ToPolylineCurve()
                except:
                    self.boundary_pts = []
                    self.boundary_curve = None

            # score (optional)
            self.score = None
            if os.path.exists(epath):
                try:
                    ev = json.load(open(epath, "r"))
                    self.score = ev.get("score", None)
                except:
                    self.score = None

            # reset accumulators
            self.points_by_cat = {}
            self.edge_curves = {"street": [], "access": []}

            # require graph to plot anything
            if not os.path.exists(gpath):
                Rhino.RhinoApp.WriteLine("[evaluation_preview] graph.json not found at: {0}".format(gpath))
                return

            g = json.load(open(gpath, "r"))
            nodes = g.get("nodes", [])
            edges = g.get("edges", [])

            # nodes
            kept_nodes = 0
            for n in nodes:
                x = n.get("x"); y = n.get("y")
                if x is None or y is None:
                    continue
                p = rg.Point3d(float(x), float(y), 0.0)
                if self._inside_boundary(p):
                    cat = categorize_node(n)
                    if cat in CAT_COLORS:
                        self.points_by_cat.setdefault(cat, []).append(p)
                        kept_nodes += 1

            # edges
            total_edges = 0
            kept_street = 0
            kept_access = 0

            for ed in edges:
                line = ed.get("line")
                if not line or len(line) < 2:
                    continue

                # build points
                pts = []
                ok = True
                for pair in line:
                    try:
                        px = float(pair[0]); py = float(pair[1])
                        pts.append(rg.Point3d(px, py, 0.0))
                    except:
                        ok = False
                        break
                if (not ok) or (len(pts) < 2):
                    continue

                # build curve
                if len(pts) == 2:
                    crv = rg.LineCurve(pts[0], pts[1])
                else:
                    crv = rg.Polyline(pts).ToPolylineCurve()
                if crv is None:
                    continue

                total_edges += 1
                if self._edge_visible(crv, pts):
                    typ = str(ed.get("type") or "street").lower()
                    if typ == "access":
                        self.edge_curves["access"].append(crv)
                        kept_access += 1
                    else:
                        self.edge_curves["street"].append(crv)
                        kept_street += 1

            Rhino.RhinoApp.WriteLine(
                "[evaluation_preview] nodes kept: {0} | edges total: {1} -> street: {2}, access: {3}".format(
                    kept_nodes, total_edges, kept_street, kept_access
                )
            )

        except Exception as e:
            Rhino.RhinoApp.WriteLine("[evaluation_preview] load error: {0}".format(e))

    # -------- drawing --------
    def DrawForeground(self, e):
        # boundary
        if self.boundary_pts and len(self.boundary_pts) >= 2:
            e.Display.DrawPolyline(self.boundary_pts, Color.FromArgb(255, 0, 0), 2)

        # edges (draw before points)
        for typ, curves in self.edge_curves.items():
            col = EDGE_COLORS.get(typ, Color.FromArgb(120, 120, 120))
            thick = EDGE_THICKNESS.get(typ, 1)
            for crv in curves:
                try:
                    e.Display.DrawCurve(crv, col, thick)
                except:
                    pass

        # nodes
        for cat, pts in self.points_by_cat.items():
            col = CAT_COLORS.get(cat, Color.Black)
            for p in pts:
                e.Display.DrawPoint(p, Rhino.Display.PointStyle.RoundSimple, 3, col)

        # score label
        txt = "Evaluation score: (pending)" if (self.score is None) else "Evaluation score: {:.4f}".format(self.score)
        e.Display.Draw2dText(txt, Color.Black, rg.Point2d(20, 40), False, 18)


# ------- module-level control (used by rhino_listener) -------
_conduit = None

def start_evaluation_preview(job_dir):
    global _conduit
    try:
        if _conduit:
            _conduit.Enabled = False
            _conduit = None
    except:
        _conduit = None
    _conduit = EvaluationConduit(job_dir)
    _conduit.Enabled = True
    Rhino.RhinoApp.WriteLine("[evaluation_preview] Enabled for: {0}".format(job_dir))

def stop_evaluation_preview():
    global _conduit
    if _conduit:
        try:
            _conduit.Enabled = False
        except:
            pass
        _conduit = None
        Rhino.RhinoApp.WriteLine("[evaluation_preview] Stopped.")
