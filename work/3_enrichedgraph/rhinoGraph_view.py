import json, os
import networkx as nx
from networkx.readwrite import json_graph
import plotly.graph_objects as go
import plotly.io as pio

pio.renderers.default = 'browser'

USE_SPRING_LAYOUT = False #True #False  # <<<< Change to False to use Rhino 3D layout

# === File loading ===
base_dir = os.path.dirname(__file__)
json_path = os.path.abspath(os.path.join(base_dir, "..", "2_3D2graph", "massing2graph.json"))

with open(json_path, "r") as f:
    host_data = json.load(f)

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
    node['available_area'] = 100000
    node['program'] = None
    node['assigned_programs'] = []

# === STEP 2: Assign programs ===
for b_node in brief_nodes:
    if b_node['id'] == "Jernbanebyen" or b_node['typology'] == "site":
        continue

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
        print(f"Program '{program_id}' only partially placed. Remaining: {remaining}")
    else:
        print(f"Program '{program_id}' placed in nodes: {assigned}")

# === TOGGLE 3D POSITION LAYOUT ===
original_pos = nx.get_node_attributes(host_graph, 'pos')
if USE_SPRING_LAYOUT:
    pos3d = nx.spring_layout(host_graph, dim=3, seed=42)
    print("Using 3D spring layout for node positions.")
else:
    if all(len(v) == 3 for v in original_pos.values()):
        pos3d = original_pos
        print("Using original 3D positions from Rhino.")
    else:
        print("Original positions missing. Falling back to spring layout.")
        pos3d = nx.spring_layout(host_graph, dim=3, seed=42)

# === Create 3D Edge Trace ===
edge_x, edge_y, edge_z = [], [], []
for u, v in host_graph.edges():
    x0, y0, z0 = pos3d[u]
    x1, y1, z1 = pos3d[v]
    edge_x += [x0, x1, None]
    edge_y += [y0, y1, None]
    edge_z += [z0, z1, None]

edge_trace = go.Scatter3d(
    x=edge_x, y=edge_y, z=edge_z,
    mode='lines',
    line=dict(color='#888', width=2),
    hoverinfo='none'
)

# === Create 3D Node Trace ===
node_x, node_y, node_z = [], [], []
node_text, node_labels, node_color = [], [], []

for node_id, data in host_graph.nodes(data=True):
    x, y, z = pos3d[node_id]
    node_x.append(x)
    node_y.append(y)
    node_z.append(z)

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

node_trace = go.Scatter3d(
    x=node_x, y=node_y, z=node_z,
    mode='markers+text',
    text=node_labels,
    textposition='top center',
    hovertext=node_text,
    hoverinfo='text',
    marker=dict(
        color=node_color,
        size=10,
        line=dict(width=1, color='black')
    )
)

# === Render Plotly 3D Graph ===
fig = go.Figure(data=[edge_trace, node_trace],
    layout=go.Layout(
        title=dict(text="3D Host Graph with Assigned Programs", font=dict(size=20)),
        showlegend=False,
        margin=dict(l=20, r=20, b=20, t=40),
        scene=dict(
            xaxis=dict(title='X'),
            yaxis=dict(title='Y'),
            zaxis=dict(title='Z')
        )
    )
)

fig.show()

# === Optional: Save Enriched Graph ===
enriched_data = json_graph.node_link_data(host_graph)

for node in enriched_data["nodes"]:
    if "pos" in node:
        node["pos"] = [round(coord, 2) for coord in node["pos"]]
    if "assigned_programs" in node and not node["assigned_programs"]:
        node.pop("assigned_programs", None)

with open("enriched_graph.json", "w") as f:
    json.dump(enriched_data, f, indent=2)
