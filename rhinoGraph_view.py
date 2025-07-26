# import json
# import networkx as nx
# from networkx.readwrite import json_graph
# import matplotlib.pyplot as plt

# # Load graph from file
# with open("C:/Users/broue/Documents/IAAC MaCAD/Master_Thesis/July_research&experimentation/graph.json", "r") as f:
#     data = json.load(f)

# # Convert JSON back to a NetworkX graph
# G = json_graph.node_link_graph(data)

# # Get node positions (if stored)
# pos = nx.get_node_attributes(G, 'pos')
# # Convert 3D to 2D for plotting
# pos2d = {k: (v[0], v[1]) for k, v in pos.items()}

# # Draw graph
# nx.draw(G, pos=pos2d, with_labels=True, node_size=300, node_color="lightblue")
# plt.show()


import json
import networkx as nx
from networkx.readwrite import json_graph
import plotly.graph_objects as go
import plotly.io as pio
pio.renderers.default = 'browser'


# === Load Host Graph ===
with open("C:/Users/broue/Documents/IAAC MaCAD/Master_Thesis/July_research&experimentation/graph.json", "r") as f:
    host_data = json.load(f)

# Fix future warning in networkx
host_graph = json_graph.node_link_graph(host_data, edges="links")

# === Define Brief Nodes ===
brief_nodes = [
    {"id": "Jernbanebyen", "label": "Jernbanebyen", "footprint": 550000, "typology": "urban_design_brief"},
    {"id": "development_area", "label": "Development Area", "footprint": 550000, "typology": "site"},
    {"id": "new_apartments", "label": "New Apartments", "footprint": 200000, "typology": "residential"},
    {"id": "workplaces", "label": "Workplaces", "footprint": 150000, "typology": "commercial"},
    {"id": "parks_and_green_spaces", "label": "Parks and Green Spaces", "footprint": 300000, "typology": "recreational"},
    {"id": "heritage_buildings", "label": "Heritage Buildings", "footprint": 100000, "typology": "cultural"},
    {"id": "pedestrian_streets", "label": "Pedestrian Streets", "footprint": 50000, "typology": "public_space"},
    {"id": "cycling_paths", "label": "Cycling Paths", "footprint": 100000, "typology": "transportation"}
]

# === Color map for typologies ===
typology_colors = {
    "residential": "orange",
    "commercial": "blue",
    "recreational": "green",
    "cultural": "purple",
    "public_space": "pink",
    "transportation": "cyan",
    "urban_design_brief": "black",
    "site": "gray"
}

# === STEP 1: Annotate host nodes ===
for node in host_graph.nodes.values():
    node['available_area'] = 100000  # Placeholder area (replace with real later)
    node['program'] = None
    node['assigned_programs'] = []

# === STEP 2: Assign programs ===
for b_node in brief_nodes:
    if b_node['id'] == "Jernbanebyen" or b_node['typology'] == "site":
        continue  # skip plot and site-level nodes

    program_id = b_node['id']
    typology = b_node['typology']
    footprint_needed = b_node['footprint']

    sorted_hosts = sorted(
        host_graph.nodes(data=True),
        key=lambda x: x[1]['available_area'],
        reverse=True
    )

    assigned = []
    remaining = footprint_needed

    for node_id, node_data in sorted_hosts:
        if node_data['available_area'] <= 0:
            continue

        use_area = min(node_data['available_area'], remaining)
        node_data['available_area'] -= use_area
        node_data['assigned_programs'].append((program_id, typology, use_area))
        assigned.append(node_id)

        remaining -= use_area
        if remaining <= 0:
            break

    if remaining > 0:
        print(f"⚠️ Program '{program_id}' only partially placed. Remaining: {remaining}")
    else:
        print(f"✅ Program '{program_id}' placed in nodes: {assigned}")

# === Plotly Interactive Graph ===
# Get 2D node positions
pos = nx.get_node_attributes(host_graph, 'pos')
pos2d = {k: (v[0], v[1]) for k, v in pos.items()}

# Edge traces
edge_x = []
edge_y = []
for u, v in host_graph.edges():
    x0, y0 = pos2d[u]
    x1, y1 = pos2d[v]
    edge_x += [x0, x1, None]
    edge_y += [y0, y1, None]

edge_trace = go.Scatter(
    x=edge_x, y=edge_y,
    line=dict(width=0.5, color='#888'),
    hoverinfo='none',
    mode='lines'
)

# Node traces
node_x = []
node_y = []
node_text = []
node_labels = []
node_color = []

for node_id, data in host_graph.nodes(data=True):
    x, y = pos2d[node_id]
    node_x.append(x)
    node_y.append(y)

    if data['assigned_programs']:
        programs = [f"{p[0]} ({p[1]}): {p[2]:,.0f} m²" for p in data['assigned_programs']]
        hover = "<br>".join(programs)
        label = data['assigned_programs'][0][0]
        typology = data['assigned_programs'][0][1]
        color = typology_colors.get(typology, "lightgray")
    else:
        hover = "Empty node"
        label = ""
        color = "lightgray"

    node_text.append(hover)
    node_labels.append(label)
    node_color.append(color)

node_trace = go.Scatter(
    x=node_x, y=node_y,
    mode='markers+text',
    text=node_labels,
    textposition='top center',
    hoverinfo='text',
    hovertext=node_text,
    marker=dict(
        color=node_color,
        size=18,
        line=dict(width=1, color='black')
    )
)

fig = go.Figure(data=[edge_trace, node_trace],
         layout=go.Layout(
            title=dict(text="📦 Embedded Brief in Host Graph", font=dict(size=20)),
            showlegend=False,
            hovermode='closest',
            margin=dict(b=20,l=20,r=20,t=40),
            xaxis=dict(showgrid=False, zeroline=False),
            yaxis=dict(showgrid=False, zeroline=False),
            height=800
        )
)

fig.show()
