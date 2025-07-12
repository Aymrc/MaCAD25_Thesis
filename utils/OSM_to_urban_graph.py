import os
import osmnx as ox
import networkx as nx

# Step 1: Load urban data from OpenStreetMap

# Define the area of interest (Superblock in Barcelona)
place_name = "Sagrada Familia, Barcelona, Catalonia, Spain"

# Download the street network (both pedestrian and vehicular)
print("Downloading street network...")
G_streets = ox.graph_from_place(place_name, network_type='all', simplify=True)

# Download building footprints (polygons)
print("Downloading buildings...")
buildings = ox.features_from_place(place_name, tags={'building': True})

# Download public spaces (parks, plazas, etc.)
print("Downloading public spaces...")
parks = ox.features_from_place(place_name, tags={'leisure': 'park'})

# Project geometries to meters (EPSG:3857) for distance calculations
buildings = buildings.to_crs(epsg=3857)
parks = parks.to_crs(epsg=3857)

# Step 2: Build the Urban Graph

# Initialize an empty graph
G_urban = nx.Graph()

# Add nodes for buildings
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

# Add nodes for parks and public spaces
print("Adding public space nodes...")
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

# Add edges: relationships based on proximity
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

# Urban graph is ready
print(f"Urban graph created: {G_urban.number_of_nodes()} nodes and {G_urban.number_of_edges()} edges")

# Get the absolute path to the directory where this script is located
base_dir = os.path.dirname(os.path.abspath(__file__))

# Define the relative path to the 'knowledge' folder
output_dir = os.path.join(base_dir, "..", "knowledge")
os.makedirs(output_dir, exist_ok=True)  # Create the directory if it doesn't exist

# Build the full path to the GraphML file
output_path = os.path.join(output_dir, "urban_graph.graphml")

# Save the graph
nx.write_graphml(G_urban, output_path)
print(f"Graph exported to {output_path}")
