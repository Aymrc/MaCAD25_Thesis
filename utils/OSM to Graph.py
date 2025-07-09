import osmnx as ox
import networkx as nx
import matplotlib.pyplot as plt
from shapely.geometry import Polygon
from itertools import combinations

# 1. Universal function to calculate distances
def calculate_distance(point1, point2):
    """Calculate distance between two points (lon, lat) in meters"""
    try:
        # For OSMnx >= 1.3.0
        return ox.distance.great_circle(
            lat1=point1[1], lon1=point1[0],
            lat2=point2[1], lon2=point2[0]
        )
    except (AttributeError, TypeError):
        try:
            # For OSMnx < 1.3.0
            return ox.distance.great_circle_vec(
                lat1=point1[1], lng1=point1[0],
                lat2=point2[1], lng2=point2[0]
            )
        except AttributeError:
            # Manual fallback (haversine formula)
            return ox.distance.great_circle(point1, point2)

# 2. Get buildings
coordinates = [
    (2.1685, 41.3872),  # NW
    (2.1690, 41.3872),  # NE
    (2.1690, 41.3868),  # SE
    (2.1685, 41.3868)   # SW
]
polygon = Polygon(coordinates)
buildings = ox.features_from_polygon(polygon, tags={'building': True})

# 3. Create and connect graph
G = nx.Graph()
distance_threshold = 50  # meters

for idx, row in buildings.iterrows():
    if hasattr(row.geometry, 'centroid'):
        centroid = row.geometry.centroid
        G.add_node(idx, pos=(centroid.x, centroid.y))

for (n1, d1), (n2, d2) in combinations(G.nodes(data=True), 2):
    distance = calculate_distance(d1['pos'], d2['pos'])
    if distance < distance_threshold:
        G.add_edge(n1, n2, weight=distance)

# 4. Visualization
fig, ax = ox.plot_footprints(buildings, show=False, close=False, bgcolor='white')
pos = {node: data['pos'] for node, data in G.nodes(data=True)}
nx.draw(G, pos, ax=ax, node_size=50, node_color='red', 
        edge_color='gray', width=1.5, alpha=0.6)

plt.title(f"Relationship graph between {len(G.nodes)} buildings")
plt.show()