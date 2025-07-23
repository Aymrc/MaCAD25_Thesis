import os
import osmnx as ox
import networkx as nx
from shapely.geometry import Point
from scipy.spatial import cKDTree

# ----------------------------------------
# Step 1: Define area of interest
# ----------------------------------------
place_name = "Jernbanebyen, Copenhagen, Denmark"
buffer_dist = 1000  # meters
neighbor_radius = 700  # max walking distance
max_neighbors = 3  # max number of neighbors per node

# ----------------------------------------
# Step 2: Download street network
# ----------------------------------------
print("Downloading street network...")
G_streets = ox.graph_from_address(place_name, dist=buffer_dist, network_type="walk", simplify=True)
G_streets = ox.project_graph(G_streets)  # Project to meters (EPSG:3857)


# # Ver primer nodo
# for node_id, data in G_streets.nodes(data=True):
#     print(f"Node {node_id}: {data}")
#     break

# Ver primer edge
names = set()
for _, _, data in G_streets.edges(data=True):
    name = data.get("name")
    if name:
        if isinstance(name, list):
            names.update(name)
        else:
            names.add(name)

print("Unique street names found:", names)



# # ----------------------------------------
# # Step 3: Download buildings and public spaces
# # ----------------------------------------
# print("Downloading buildings...")
# buildings = ox.features_from_address(place_name, dist=buffer_dist, tags={"building": True})
# if not buildings.empty:
#     buildings = buildings.to_crs(G_streets.graph['crs'])  # Match CRS to street network
# else:
#     buildings = None

# print("Downloading parks and plazas...")
# public_spaces = ox.features_from_address(place_name, dist=buffer_dist, tags={"leisure": ["park", "garden"], "place": "square"})
# if not public_spaces.empty:
#     public_spaces = public_spaces.to_crs(G_streets.graph['crs'])
# else:
#     public_spaces = None

# # ----------------------------------------
# # Step 4: Build urban graph
# # ----------------------------------------
# G_urban = nx.Graph()

# # Add building nodes
# if buildings is not None:
#     print("Adding building nodes...")
#     for idx, row in buildings.iterrows():
#         centroid = row.geometry.centroid
#         G_urban.add_node(
#             f"building_{idx}",
#             type="building",
#             use=row.get("building", "unknown"),
#             area=row.geometry.area,
#             x=centroid.x,
#             y=centroid.y,
#             geometry_wkt=row.geometry.wkt  # Store polygon WKT
#         )

# # Add public space nodes
# if public_spaces is not None:
#     print("Adding public space nodes...")
#     for idx, row in public_spaces.iterrows():
#         centroid = row.geometry.centroid
#         G_urban.add_node(
#             f"public_space_{idx}",
#             type=row.get("leisure", row.get("place", "unknown")),
#             use="public_space",
#             area=row.geometry.area,
#             x=centroid.x,
#             y=centroid.y
#         )

# # ----------------------------------------
# # Step 4.5: Connect buildings and public spaces to nearest street node
# # ----------------------------------------
# print("Connecting buildings and public spaces to the street network...")

# # Build list of street node coordinates
# street_node_positions = {
#     node: (data["x"], data["y"])
#     for node, data in G_streets.nodes(data=True)
#     if "x" in data and "y" in data
# }
# street_kdtree = cKDTree(list(street_node_positions.values()))
# street_nodes_list = list(street_node_positions.keys())
# street_coords = list(street_node_positions.values())

# urban_nodes_data = list(G_urban.nodes(data=True))
# for node_id, data in urban_nodes_data:
#     x, y = data["x"], data["y"]

#     # Find nearest street node
#     dist, idx = street_kdtree.query([x, y], k=1)
#     nearest_street_node = street_nodes_list[idx]

#     try:
#         walk_dist = nx.shortest_path_length(
#             G_streets,
#             source=nearest_street_node,
#             target=nearest_street_node,
#             weight="length"
#         )
#     except nx.NetworkXNoPath:
#         walk_dist = dist  # fallback to Euclidean

#     # Before adding the edge, make sure the street node exists in G_urban with attributes
#     if not G_urban.has_node(nearest_street_node):
#         street_data = G_streets.nodes[nearest_street_node]
#         G_urban.add_node(
#             nearest_street_node,
#             type="street_node",
#             x=street_data["x"],
#             y=street_data["y"]
#         )

#     # Now safely add the edge
#     G_urban.add_edge(node_id, nearest_street_node, type="building_to_street", distance=dist)



# # ----------------------------------------
# # Step 5: Connect nodes by walking distance
# # ----------------------------------------
# if G_urban.number_of_nodes() > 0:
#     print("Connecting nodes by accessibility...")
#     coords = [(data["x"], data["y"]) for node, data in G_urban.nodes(data=True)]
#     tree = cKDTree(coords)

#     node_list = list(G_urban.nodes)
#     for i, node in enumerate(node_list):
#         idxs = tree.query_ball_point(coords[i], neighbor_radius)
#         idxs = [j for j in idxs if j != i]
#         idxs = sorted(idxs, key=lambda j: ((coords[i][0] - coords[j][0])**2 + (coords[i][1] - coords[j][1])**2)**0.5)[:max_neighbors]
#         for j in idxs:
#             u, v = node, node_list[j]
#             u_point = ox.distance.nearest_nodes(G_streets, coords[i][0], coords[i][1])
#             v_point = ox.distance.nearest_nodes(G_streets, coords[j][0], coords[j][1])
#             try:
#                 walk_dist = nx.shortest_path_length(G_streets, u_point, v_point, weight="length")
#                 if walk_dist <= neighbor_radius:
#                     G_urban.add_edge(u, v, type="accessibility", distance=walk_dist)
#             except nx.NetworkXNoPath:
#                 pass  # Skip if no path exists

# # ----------------------------------------
# # Step 6: Export GraphML
# # ----------------------------------------
# output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "knowledge")
# os.makedirs(output_dir, exist_ok=True)

# urban_graph_path = os.path.join(output_dir, "urban_graph.graphml")
# streets_graph_path = os.path.join(output_dir, "streets_graph.graphml")

# # Fix: remove CRS from G_streets
# if "crs" in G_streets.graph:
#     del G_streets.graph["crs"]

# # Fix: convert LineString geometries in edges to WKT
# from shapely.geometry import LineString
# for u, v, data in G_streets.edges(data=True):
#     geom = data.get("geometry")
#     if isinstance(geom, LineString):
#         data["geometry_wkt"] = geom.wkt
#         del data["geometry"]

# # Fix: convert all list attributes to comma-separated strings
# for u, v, data in G_streets.edges(data=True):
#     for k, v in data.items():
#         if isinstance(v, list):
#             data[k] = ",".join(str(item) for item in v)

# for node, data in G_streets.nodes(data=True):
#     for k, v in data.items():
#         if isinstance(v, list):
#             data[k] = ",".join(str(item) for item in v)

# print(f"Urban graph: {G_urban.number_of_nodes()} nodes, {G_urban.number_of_edges()} edges")
# nx.write_graphml(G_urban, urban_graph_path)
# nx.write_graphml(G_streets, streets_graph_path)

# print(f"Urban graph exported to: {urban_graph_path}")
# print(f"Streets graph exported to: {streets_graph_path}")

