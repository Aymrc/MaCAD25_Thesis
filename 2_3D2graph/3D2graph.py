#! python

# This script transforms geometry from Rhino into a graph
# Note:
# Python component needs list access for "ego_list"

import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
from collections import defaultdict

debug_messages = []

# === Input check & cleanup
geo_objects = []
for g in geo_list:
    geo = rs.coercegeometry(g) if not isinstance(g, rg.GeometryBase) else g
    if isinstance(geo, rg.Extrusion): geo = geo.ToBrep()
    if isinstance(geo, rg.Brep): geo_objects.append(geo)

n = len(geo_objects)
debug_messages.append(f"Input Breps: {n}")

# === Build connection map using BooleanUnion
adj = defaultdict(set)
for i in range(n):
    for j in range(i + 1, n):
        try:
            result = rg.Brep.CreateBooleanUnion([geo_objects[i], geo_objects[j]], tolerance)
            if result and len(result) == 1:
                adj[i].add(j)
                adj[j].add(i)
                debug_messages.append(f"Connected: {i} <-> {j}")
        except:
            debug_messages.append(f"Union failed: {i} <-> {j}")

# === Group connected breps
visited = set()
groups = []

def dfs(v, group):
    visited.add(v)
    group.add(v)
    for nb in adj[v]:
        if nb not in visited:
            dfs(nb, group)

for i in range(n):
    if i not in visited:
        group = set()
        dfs(i, group)
        groups.append(group)

debug_messages.append(f"Groups found: {len(groups)}")

# === Centroids by group
group_centroids = []
group_volumes = [] # data volume 
index_to_group = {}

for group_id, group in enumerate(groups):
    for idx in group:
        index_to_group[idx] = group_id
    breps = [geo_objects[i] for i in group]
    joined = rg.Brep.JoinBreps(breps, tolerance)
    brep = joined[0] if joined else breps[0]

    vmp = rg.VolumeMassProperties.Compute(brep) #this part deals with volume data in the node
    volume = vmp.Volume if vmp else 0.0
    c = vmp.Centroid if vmp else brep.GetBoundingBox(True).Center

    group_volumes.append(volume)
    group_centroids.append(rg.Point3d(c.X, c.Y, 0))

debug_messages.append(f"Group centroids created: {len(group_centroids)}")

# === Unique edges between groups
group_edges = set()
for i, neighbors in adj.items():
    for j in neighbors:
        gi, gj = index_to_group[i], index_to_group[j]
        if gi != gj:
            edge = tuple(sorted((gi, gj)))
            group_edges.add(edge)

debug_messages.append(f"Group-to-group edges: {len(group_edges)}\n")

# === Plot connections
plot_crv = plot.ToNurbsCurve() if hasattr(plot, 'ToNurbsCurve') else plot
plot_center = None
plot_edges = []

if plot_crv and plot_crv.IsClosed:
    amp = rg.AreaMassProperties.Compute(plot_crv)
    if amp:
        plot_center = amp.Centroid
        debug_messages.append(f"Plot center found at: ({plot_center.X:.1f}, {plot_center.Y:.1f})")
    else:
        debug_messages.append("Plot center not found")

    for group_id, group in enumerate(groups):
        for idx in group:
            bbox = geo_objects[idx].GetBoundingBox(True)
            base_pts = [
                rg.Point3d(x, y, 0)
                for x in [bbox.Min.X, bbox.Max.X]
                for y in [bbox.Min.Y, bbox.Max.Y]
            ]
            if any(plot_crv.Contains(pt) != rg.PointContainment.Outside for pt in base_pts):
                plot_edges.append(rg.Line(plot_center, group_centroids[group_id]))
                debug_messages.append(f"Plot connection made to group {group_id}")
                break

debug_messages.append(f"Total plot connections: {len(plot_edges)}\n")

# === GH Outputs
merged_points_out = group_centroids + ([plot_center] if plot_center else [])
merged_edges_out = [rg.Line(group_centroids[u], group_centroids[v]).ToNurbsCurve() for u, v in group_edges] + \
                   [line.ToNurbsCurve() for line in plot_edges]

debug_out = "\n".join(debug_messages)





# =============
# === GRAPH ===
# =============

import networkx as nx
import json
from networkx.readwrite import json_graph

G = nx.Graph()

# Add nodes
# Add group nodes with volume
for i, pt in enumerate(group_centroids):
    volume = group_volumes[i]
    G.add_node(i, pos=(pt.X, pt.Y, pt.Z), volume=volume)
    debug_messages.append(f"Group {i}: volume = {volume:.0f} m³")


# Add edges group-to-group
for u, v in group_edges:
    G.add_edge(u, v)

# Add plot node if available
plot_node_id = None
if plot_center:
    plot_node_id = len(group_centroids)
    G.add_node(plot_node_id, pos=(plot_center.X, plot_center.Y, plot_center.Z), volume=0)
    for i, line in enumerate(plot_edges):
        end_pt = line.To
        for j, pt in enumerate(group_centroids):
            if end_pt.DistanceTo(pt) < 1e-6:
                G.add_edge(plot_node_id, j)
                break


debug_messages.append(f"\nGraph built: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")


data = json_graph.node_link_data(G)
json_path = path + "massing2graph.json"
with open(json_path, "w") as f:
    json.dump(data, f, indent=2)
debug_messages.append(f"Graph exported to: {json_path}")


graph_out = G
debug_out = "\n".join(debug_messages)
