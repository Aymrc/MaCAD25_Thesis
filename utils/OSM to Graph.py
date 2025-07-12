import osmnx as ox
import networkx as nx
import matplotlib.pyplot as plt
import numpy as np
from shapely.geometry import Polygon #Method 1 Get buildinggs by polygon coordinates
from shapely.geometry import Point #Method 2 Get buildinggs by 1point
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

# # 2.Method 1 Get buildings by polygon coordinates
# coordinates = [
#     (2.1685, 41.3872),  # NW
#     (2.1710, 41.3872),  # NE
#     (2.1710, 41.3888),  # SE
#     (2.1685, 41.3888)   # SW
# ]
# polygon = Polygon(coordinates)
# buildings = ox.features_from_polygon(polygon, tags={'building': True})


# 2. Method 2 Get buildings by location
def safe_buffer(point, radius_meters):
    """Create a valid buffer polygon"""
    try:
        # Convert meters to approximate degrees (1° ≈ 111,320m at equator)
        radius_degrees = radius_meters / 111320
        buffer = point.buffer(radius_degrees)
        
        # Validate the geometry
        if not buffer.is_valid or np.isnan(buffer.area):
            # Fallback: create a simple bbox if buffer fails
            minx, miny = point.x - radius_degrees, point.y - radius_degrees
            maxx, maxy = point.x + radius_degrees, point.y + radius_degrees
            buffer = box(minx, miny, maxx, maxy)
            
        return buffer
    except Exception as e:
        print(f"Buffer creation failed: {e}")
        return None

# Define center point (longitude, latitude)
center_point = Point(-73.9855, 40.7580)  # Example: Plaza Catalunya
radius = 50  # meters

# Create validated buffer
buffer = safe_buffer(center_point, radius)
if buffer is None:
    raise ValueError("Could not create valid search area")

# Get buildings with error handling
try:
    buildings = ox.features_from_polygon(buffer, tags={'building': True})
    print(f"Found {len(buildings)} buildings")
    
    # Basic validation
    if len(buildings) == 0:
        print("Warning: No buildings found. Try increasing radius.")
        
except Exception as e:
    print(f"Error fetching buildings: {e}")
    buildings = ox.features_from_point(
        (center_point.y, center_point.x),  # (lat, lon)
        dist=radius,
        tags={'building': True}
    )


# print(buildings.columns)
# print(buildings.notnull().sum())
print(buildings['building'])


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

nx.draw_networkx_labels(G, pos, font_size=8, ax=ax)

edge_labels = {(u, v): f"{round(d['weight'], 1)} m" for u, v, d in G.edges(data=True)}
nx.draw_networkx_edge_labels(
    G, pos, edge_labels=edge_labels,
    font_size=5, font_color="darkgreen", ax=ax, label_pos=0.5, rotate=True
)

plt.title(f"Relationship graph between {len(G.nodes)} buildings")
plt.show()