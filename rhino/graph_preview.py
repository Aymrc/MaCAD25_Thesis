# 2_rhino/graph_preview.py
# Live preview of the urban graph (edges + nodes) using DisplayConduit (no bake).

import os
import json
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
import rhinoscriptsyntax as rs
import System.Drawing as sd

class GraphPreviewConduit(Rhino.Display.DisplayConduit):
    def __init__(self, graph_json_path):
        super(GraphPreviewConduit, self).__init__()
        self.path = graph_json_path
        self.nodes = []
        self.edges = []
        self._load_graph()

        # Styles
        self.street_color = sd.Color.FromArgb(140, 140, 140)
        self.access_color = sd.Color.FromArgb(180, 180, 180)
        self.building_color = sd.Color.FromArgb(220, 140, 40)
        self.green_color = sd.Color.FromArgb(0, 160, 0)

        self.street_thickness = 2
        self.access_thickness = 1
        self.node_radius = 0.6

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

            if ed.get("type") == "street":
                e.Display.DrawLine(seg, self.street_color, self.street_thickness)
            else:
                e.Display.DrawLine(seg, self.access_color, self.access_thickness)

        # Draw nodes as small discs
        for n in self.nodes:
            x, y = n.get("x"), n.get("y")
            if x is None or y is None: 
                continue
            p = rg.Point3d(float(x), float(y), 0.0)
            if n.get("type") == "green":
                col = self.green_color
            elif n.get("type") == "building":
                col = self.building_color
            else:
                col = self.street_color
            e.Display.DrawPoint(p, Rhino.Display.PointStyle.RoundSimple, 3, col)

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