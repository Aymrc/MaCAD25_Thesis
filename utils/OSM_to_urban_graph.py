import os
import osmnx as ox
import networkx as nx

# Step 1: Load urban data from OpenStreetMap
place_name = "Plaça Reial, Barcelona, Spain"

# "Sagrada Familia, Barcelona, Catalonia, Spain"
# "Plaça de Catalunya, Barcelona, Spain"
# "Plaça Reial, Barcelona, Spain"

# -------------------------------
# Download street network with explicit buffer
# -------------------------------
print("Downloading street network...")
try:
    G_streets = ox.graph_from_address(place_name, dist=300, network_type='all', simplify=True)
    if len(G_streets.nodes) == 0:
        print("No street nodes found in this area.")
        G_streets = None
except Exception as e:
    print(f"Error downloading street network: {e}")
    G_streets = None

# -------------------------------
# Download building footprints
# -------------------------------
print("Downloading buildings...")
try:
    buildings = ox.features_from_address(place_name, dist=300, tags={'building': True})
    if buildings.empty:
        print("No building footprints found.")
        buildings = None
    else:
        buildings = buildings.to_crs(epsg=3857)
except Exception as e:
    print(f"Error downloading buildings: {e}")
    buildings = None

# -------------------------------
# Download public spaces (parks, plazas, etc.)
# -------------------------------
print("Downloading public spaces...")
try:
    parks = ox.features_from_address(place_name, dist=300, tags={'leisure': 'park'})
    if parks.empty:
        print("No parks found in this area.")
        parks = None
    else:
        parks = parks.to_crs(epsg=3857)
except ox._errors.InsufficientResponseError:
    print("No matching public spaces found.")
    parks = None
except Exception as e:
    print(f"Error downloading public spaces: {e}")
    parks = None

# -------------------------------
# Build the Urban Graph
# -------------------------------
G_urban = nx.Graph()

# Add nodes for buildings
if buildings is not None:
    print("Adding building nodes...")
    for idx, row in buildings.iterrows():
        centroid = row.geometry.centroid
        G_urban.add_node(
            f"building_{idx}",
            type="building",
            use=row.get('building', 'unknown'),
            area=row.geometry.area,
            x=centroid.x,
            y=centroid.y
        )
else:
    print("No building nodes to add.")

# Add nodes for parks
if parks is not None:
    print("Adding park nodes...")
    for idx, row in parks.iterrows():
        centroid = row.geometry.centroid
        G_urban.add_node(
            f"park_{idx}",
            type="park",
            use="public_space",
            area=row.geometry.area,
            x=centroid.x,
            y=centroid.y
        )
else:
    print("No park nodes to add.")

# Add proximity edges
if G_urban.number_of_nodes() > 0:
    print("Creating proximity edges...")
    for u, u_data in G_urban.nodes(data=True):
        for v, v_data in G_urban.nodes(data=True):
            if u != v:
                point_u = (u_data['x'], u_data['y'])
                point_v = (v_data['x'], v_data['y'])
                # Calculate Euclidean distance
                distance = ((point_u[0] - point_v[0])**2 + (point_u[1] - point_v[1])**2)**0.5
                # Connect nodes if they are within 50 meters
                if distance < 50:
                    G_urban.add_edge(u, v, type="proximity", distance=distance)
else:
    print("No nodes found to create proximity edges.")

# -------------------------------
# Export the Urban Graph
# -------------------------------
print(f"Urban graph created: {G_urban.number_of_nodes()} nodes and {G_urban.number_of_edges()} edges")

base_dir = os.path.dirname(os.path.abspath(__file__))
output_dir = os.path.join(base_dir, "..", "knowledge")
os.makedirs(output_dir, exist_ok=True)
output_path = os.path.join(output_dir, "urban_graph.graphml")

if G_urban.number_of_nodes() > 0:
    nx.write_graphml(G_urban, output_path)
    print(f"Graph exported to {output_path}")
    print("GraphML saved at:", os.path.abspath(output_path))
else:
    print("Graph not exported because it has no nodes.")