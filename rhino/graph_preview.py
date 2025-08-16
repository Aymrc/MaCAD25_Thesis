# 2_rhino/graph_preview.py
# Live preview of the urban graph (edges + nodes) using DisplayConduit (no bake).
# Updated: color-code nodes using the same categories/colors as evaluation_preview.py

import os
import json
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
import rhinoscriptsyntax as rs
import System.Drawing as sd

# --- Try to import the categorization + colors from evaluation_preview.py ---
try:
    from evaluation_preview import categorize_node, CAT_COLORS
    _HAS_EVAL_COLORS = True
except Exception:
    # Fallback: minimal local replica so the script still runs if import fails.
    from System.Drawing import Color

    def categorize_node(attrs):
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

    CAT_COLORS = {
        "Residential": Color.FromArgb(220, 45, 70),
        "Office":      Color.FromArgb(0, 112, 184),
        "Leisure":     Color.FromArgb(0, 170, 70),
        "Cultural":    Color.FromArgb(140, 0, 140),
        "Green":       Color.FromArgb(80, 180, 120),
    }
    _HAS_EVAL_COLORS = False


class GraphPreviewConduit(Rhino.Display.DisplayConduit):
    def __init__(self, graph_json_path):
        super(GraphPreviewConduit, self).__init__()
        self.path = graph_json_path
        self.nodes = []
        self.edges = []
        self._load_graph()

        # --- Edge styles (kept consistent with evaluation_preview) ---
        from System.Drawing import Color
        self.street_color = Color.FromArgb(138, 138, 138)
        self.access_color = Color.FromArgb(0, 0, 0)
        self.street_thickness = 2
        self.access_thickness = 1

        # Node drawing style
        self.node_point_style = Rhino.Display.PointStyle.RoundSimple
        self.node_pixel_size = 3  # matches evaluation_preview

    def _load_graph(self):
        if not os.path.exists(self.path):
            Rhino.RhinoApp.WriteLine("[graph_preview] graph.json not found: {0}".format(self.path))
            self.nodes = []
            self.edges = []
            return
        with open(self.path, "r") as f:
            data = json.load(f)
        self.nodes = data.get("nodes", [])
        self.edges = data.get("edges", [])

    def _node_color(self, node):
        """Return System.Drawing.Color for a node using evaluation categories/colors.
        Falls back to legacy types if categorization yields None.
        """
        cat = None
        try:
            cat = categorize_node(node)
        except Exception:
            cat = None

        if cat and (cat in CAT_COLORS):
            return CAT_COLORS[cat]

        # Fallbacks to keep backwards compatibility with existing graphs
        t = str(node.get("type", "")).lower()
        from System.Drawing import Color
        if t == "green":
            return Color.FromArgb(80, 180, 120)  # same as CAT_COLORS["Green"]
        if t == "building":
            return Color.FromArgb(220, 140, 40)
        # default street-ish gray
        return Color.FromArgb(140, 140, 140)

    def DrawForeground(self, e):
        # Draw edges
        for ed in self.edges:
            line = ed.get("line")
            if not line or len(line) != 2:
                continue
            (x1, y1), (x2, y2) = line
            p1 = rg.Point3d(float(x1), float(y1), 0.0)
            p2 = rg.Point3d(float(x2), float(y2), 0.0)
            seg = rg.Line(p1, p2)

            if str(ed.get("type") or "street").lower() == "access":
                e.Display.DrawLine(seg, self.access_color, self.access_thickness)
            else:
                e.Display.DrawLine(seg, self.street_color, self.street_thickness)

        # Draw nodes as small discs using evaluation categories/colors
        for n in self.nodes:
            x, y = n.get("x"), n.get("y")
            if x is None or y is None:
                continue
            p = rg.Point3d(float(x), float(y), 0.0)
            col = self._node_color(n)
            e.Display.DrawPoint(p, self.node_point_style, self.node_pixel_size, col)


def start_preview(job_dir):
    """Start or replace a sticky conduit for the given job_dir/graph.json."""
    graph_path = os.path.join(job_dir, "graph.json")
    # If an existing conduit is running, disable it first
    key = "graph_preview_conduit"
    prev = sc.sticky.get(key)
    if prev:
        try:
            prev.Enabled = False
        except:
            pass
        sc.sticky[key] = None

    conduit = GraphPreviewConduit(graph_path)
    conduit.Enabled = True
    sc.sticky[key] = conduit
    rs.Redraw()


def stop_preview():
    key = "graph_preview_conduit"
    prev = sc.sticky.get(key)
    if prev:
        try:
            prev.Enabled = False
        except:
            pass
        sc.sticky[key] = None
        rs.Redraw()
