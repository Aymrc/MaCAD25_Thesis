# evaluation_preview.py - IronPython 2.7
# Preview: boundary (red), typed nodes inside boundary colored by category, and score.

import os, json, System
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc

# ---- category colors (match your COPILOT layer colors) ----
from System.Drawing import Color
CAT_COLORS = {
    "Residential": Color.FromArgb(220, 45, 70),
    "Office":      Color.FromArgb(0, 112, 184),
    "Leisure":     Color.FromArgb(0, 170, 70),
    "Cultural":    Color.FromArgb(140, 0, 140),
    "Green":       Color.FromArgb(80, 180, 120),
}

def categorize_node(attrs):
    """Lightweight categorization based on OSM-like tags embedded in node data."""
    try:
        tags = {}
        for k, v in attrs.items():
            try:
                k2 = str(k).lower()
                v2 = str(v).lower()
                tags[k2] = v2
            except:
                pass
        building = tags.get("building")

        residential_buildings = {
            "apartments","house","residential","semidetached_house","terrace",
            "bungalow","detached","dormitory"
        }
        office_buildings = {
            "office","commercial","industrial","retail","manufacture","warehouse","service"
        }
        cultural_buildings = {"college","school","kindergarten","government","civic","church","fire_station","prison"}
        leisure_buildings  = {"hotel","boathouse","houseboat","bridge"}
        green_buildings    = {"greenhouse","allotment_house"}

        if building == "yes":
            return "Residential"
        if building in residential_buildings:
            return "Residential"
        if building in office_buildings:
            return "Office"
        if building in cultural_buildings:
            return "Cultural"
        if building in leisure_buildings:
            return "Leisure"
        if building in green_buildings:
            return "Green"

        if any(kw in tags.get("amenity","") for kw in ["museum","theatre","gallery"]):
            return "Cultural"
        if any(kw in tags.get("leisure","") for kw in ["park","recreation","garden"]):
            return "Leisure"
        if tags.get("landuse") in ["grass","meadow"] or "green" in tags.get("type",""):
            return "Green"

        return None
    except:
        return None

class EvaluationConduit(Rhino.Display.DisplayConduit):
    def __init__(self, job_dir):
        self.job_dir = job_dir
        self.boundary_pts = []
        self.boundary_curve = None
        self.score = None
        self.points_by_cat = {}  # cat -> [Point3d,...]
        self._load_data()

    def _load_data(self):
        try:
            bpath = os.path.join(self.job_dir, "boundary.json")
            epath = os.path.join(self.job_dir, "evaluation.json")
            gpath = os.path.join(self.job_dir, "graph.json")

            # boundary
            if os.path.exists(bpath):
                xy = json.load(open(bpath, "r"))
                self.boundary_pts = [rg.Point3d(float(x), float(y), 0.0) for (x, y) in xy]
                if len(self.boundary_pts) >= 3:
                    pl = rg.Polyline(self.boundary_pts)
                    self.boundary_curve = pl.ToNurbsCurve()

            # score
            if os.path.exists(epath):
                ev = json.load(open(epath, "r"))
                self.score = ev.get("score", None)

            # nodes: filter inside boundary and categorize
            self.points_by_cat = {}
            if self.boundary_curve is not None and os.path.exists(gpath):
                g = json.load(open(gpath, "r"))
                nodes = g.get("nodes", [])
                tol = sc.doc.ModelAbsoluteTolerance
                for n in nodes:
                    x = n.get("x"); y = n.get("y")
                    if x is None or y is None:
                        continue
                    p = rg.Point3d(float(x), float(y), 0.0)
                    # only draw nodes INSIDE boundary
                    try:
                        contains = self.boundary_curve.Contains(p, tol)
                    except:
                        contains = self.boundary_curve.Contains(p)
                    if contains != rg.PointContainment.Inside:
                        continue

                    cat = categorize_node(n)
                    if cat in CAT_COLORS:
                        self.points_by_cat.setdefault(cat, []).append(p)

        except Exception as e:
            Rhino.RhinoApp.WriteLine("[evaluation_preview] load error: {0}".format(e))

    def DrawForeground(self, e):
        # boundary
        if self.boundary_pts and len(self.boundary_pts) >= 2:
            e.Display.DrawPolyline(self.boundary_pts, Color.FromArgb(255, 0, 0), 2)

        # typed nodes by category (inside only)
        for cat, pts in self.points_by_cat.items():
            col = CAT_COLORS.get(cat, Color.Black)
            for p in pts:
                e.Display.DrawPoint(p, Rhino.Display.PointStyle.RoundSimple, 3, col)

        # score label
        if self.score is not None:
            txt = "Evaluation score: {:.4f}".format(self.score)
        else:
            txt = "Evaluation score: (pending)"
        e.Display.Draw2dText(txt, Color.Black, rg.Point2d(20, 40), False, 18)

_conduit = None

def start_evaluation_preview(job_dir):
    global _conduit
    if _conduit:
        _conduit.Enabled = False
        _conduit = None
    _conduit = EvaluationConduit(job_dir)
    _conduit.Enabled = True
    Rhino.RhinoApp.WriteLine("[evaluation_preview] Enabled for: {0}".format(job_dir))

def stop_evaluation_preview():
    global _conduit
    if _conduit:
        _conduit.Enabled = False
        _conduit = None
        Rhino.RhinoApp.WriteLine("[evaluation_preview] Stopped.")