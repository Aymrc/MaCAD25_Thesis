import os
import osmnx as ox
import networkx as nx
from shapely.geometry import Point
from scipy.spatial import cKDTree

# ----------------------------------------
# Step 1: Define area of interest
# ----------------------------------------
place_name = "Jernbanebyen, Copenhagen, Denmark"
buffer_dist = 500  # meters (to include surrounding context)
neighbor_radius = 300  # max walking distance to connect nodes (meters)
max_neighbors = 3  # max number of neighbors per node

# ----------------------------------------
# Step 2: Download street network
# ----------------------------------------
print("Downloading street network...")
G_streets = ox.graph_from_address(place_name, dist=buffer_dist, network_type="walk", simplify=True)

# ----------------------------------------
# Step 3: Download buildings and public spaces
# ----------------------------------------
print("Downloading buildings...")
buildings = ox.features_from_address(place_name, dist=buffer_dist, tags={"building": True})
if not buildings.empty:
    buildings = buildings.to_crs(epsg=3857)  # Project for metric units
else:
    buildings = None

print("Downloading parks and plazas...")
public_spaces = ox.features_from_address(place_name, dist=buffer_dist, tags={"leisure": ["park", "garden"], "place": "square"})
if not public_spaces.empty:
    public_spaces = public_spaces.to_crs(epsg=3857)
else:
    public_spaces = None

# ----------------------------------------
# Step 4: Build urban graph
# ----------------------------------------
G_urban = nx.Graph()

# Add building nodes
if buildings is not None:
    print("Adding building nodes...")
    for idx, row in buildings.iterrows():
        centroid = row.geometry.centroid
        G_urban.add_node(
            f"building_{idx}",
            type="building",
            use=row.get("building", "unknown"),
            area=row.geometry.area,
            x=centroid.x,
            y=centroid.y,
            geometry_wkt=row.geometry.wkt
        )

# Add public space nodes
if public_spaces is not None:
    print("Adding public space nodes...")
    for idx, row in public_spaces.iterrows():
        centroid = row.geometry.centroid
        G_urban.add_node(
            f"public_space_{idx}",
            type=row.get("leisure", row.get("place", "unknown")),
            use="public_space",
            area=row.geometry.area,
            x=centroid.x,
            y=centroid.y
        )

# ----------------------------------------
# Step 5: Connect nodes by walking distance
# ----------------------------------------
if G_urban.number_of_nodes() > 0:
    print("Connecting nodes by accessibility...")
    # Build KDTree for efficient nearest-neighbor search
    coords = [(data["x"], data["y"]) for node, data in G_urban.nodes(data=True)]
    tree = cKDTree(coords)

    node_list = list(G_urban.nodes)
    for i, node in enumerate(node_list):
        # Find nearest neighbors within radius
        idxs = tree.query_ball_point(coords[i], neighbor_radius)
        idxs = [j for j in idxs if j != i]  # exclude self
        # Limit to k neighbors
        idxs = sorted(idxs, key=lambda j: ((coords[i][0] - coords[j][0])**2 + (coords[i][1] - coords[j][1])**2)**0.5)[:max_neighbors]
        for j in idxs:
            u, v = node, node_list[j]
            # Compute walking distance on street network
            u_point = ox.distance.nearest_nodes(G_streets, coords[i][0], coords[i][1])
            v_point = ox.distance.nearest_nodes(G_streets, coords[j][0], coords[j][1])
            try:
                walk_dist = nx.shortest_path_length(G_streets, u_point, v_point, weight="length")
                if walk_dist <= neighbor_radius:
                    G_urban.add_edge(u, v, type="accessibility", distance=walk_dist)
            except nx.NetworkXNoPath:
                pass  # skip if no path exists

# ----------------------------------------
# Step 6: Export GraphML
# ----------------------------------------
output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "knowledge")
os.makedirs(output_dir, exist_ok=True)
output_path = os.path.join(output_dir, "urban_graph.graphml")

print(f"Urban graph created: {G_urban.number_of_nodes()} nodes and {G_urban.number_of_edges()} edges")
nx.write_graphml(G_urban, output_path)
print(f"Graph exported to {output_path}")