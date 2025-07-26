import json
import networkx as nx
from networkx.readwrite import json_graph
import matplotlib.pyplot as plt

# Load graph from file
with open("C:/Users/broue/Documents/IAAC MaCAD/Master_Thesis/July_research&experimentation/graph.json", "r") as f:
    data = json.load(f)

# Convert JSON back to a NetworkX graph
G = json_graph.node_link_graph(data)

# Get node positions (if stored)
pos = nx.get_node_attributes(G, 'pos')
# Convert 3D to 2D for plotting
pos2d = {k: (v[0], v[1]) for k, v in pos.items()}

# Draw graph
nx.draw(G, pos=pos2d, with_labels=True, node_size=300, node_color="lightblue")
plt.show()
