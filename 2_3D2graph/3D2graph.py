#! python

# import rhinoscriptsyntax as rs
import Rhino.Geometry as rg
import networkx as nx

# Build graph
G = nx.Graph()
for i, pt in enumerate(nodes):
    G.add_node(i, pos=(pt.X, pt.Y, pt.Z))

index_edges = []
for edge in edges:
    try:
        pt1 = edge.PointAtStart
        pt2 = edge.PointAtEnd
        i1 = nodes.index(pt1)
        i2 = nodes.index(pt2)
        index_edges.append((i1, i2))
    except:
        pass

G.add_edges_from(index_edges)

# Prepare output
graph_lines = []
graph_nodes = []

for i in G.nodes:
    x, y, z = G.nodes[i]['pos']
    graph_nodes.append(rg.Point3d(x, y, z))

for u, v in G.edges:
    p1 = G.nodes[u]['pos']
    p2 = G.nodes[v]['pos']
    graph_lines.append(rg.Line(rg.Point3d(*p1), rg.Point3d(*p2)))

# Outputs
node_points = graph_nodes
edge_lines = graph_lines
graph = G



# ==================================== 


import json
from networkx.readwrite import json_graph

# JSON
data = json_graph.node_link_data(G)


path = path + "graph.json"
print(path)

# Write to file
with open(path, "w") as f:
    json.dump(data, f)


out = """3D to Graph has been saved in the location: {}\n""".format(path)
