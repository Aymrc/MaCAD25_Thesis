# requirements: osmnx

import os
import rhinoscriptsyntax as rs
import Rhino.Geometry as rg
import osmnx as ox
from shapely.geometry import Polygon, LineString
from osmnx.projection import project_gdf
import pandas as pd

# =======================
# HARD-CODED PARAMETERS
# =======================
location_point = (41.3874, 2.1686)  # Barcelona city center (lat, lon)
buffer_distance = 500  # meters
cache_folder = r"C:\Users\CDH\Documents\GitHub\MaCAD25_Thesis\cache"

tags_streets = {"highway": True}
tags_buildings = {"building": True}
tags_greens = {
    "leisure": ["park", "garden"],
    "landuse": ["grass", "recreation_ground", "cemetery"]
}

# =======================
# OSMNX CONFIG
# =======================
ox.settings.use_cache = True
ox.settings.cache_folder = cache_folder
os.makedirs(ox.settings.cache_folder, exist_ok=True)

# =======================
# DOWNLOAD OSM DATA
# =======================
print("Downloading OSM data...")
gdf_streets = project_gdf(
    ox.features_from_point(location_point, tags=tags_streets, dist=buffer_distance)
)
gdf_buildings = project_gdf(
    ox.features_from_point(location_point, tags=tags_buildings, dist=buffer_distance)
)
gdf_greens = project_gdf(
    ox.features_from_point(location_point, tags=tags_greens, dist=buffer_distance)
)

# =======================
# CONVERSION FUNCTIONS
# =======================
def shapely_to_rhino(geometry):
    """Converts Shapely geometry to Rhino.Geometry"""
    if geometry is None:
        return None
    if isinstance(geometry, LineString):
        pts = [rg.Point3d(x, y, 0) for x, y in geometry.coords]
        return rg.Polyline(pts)
    elif isinstance(geometry, Polygon):
        pts = [rg.Point3d(x, y, 0) for x, y in geometry.exterior.coords]
        return rg.Polyline(pts)
    return None

def add_to_rhino(gdf, layer_name):
    """Adds geometry from a GeoDataFrame to a specific Rhino layer"""
    if not rs.IsLayer(layer_name):
        rs.AddLayer(layer_name)
    rs.CurrentLayer(layer_name)
    
    count = 0
    for _, row in gdf.iterrows():
        rhino_geom = shapely_to_rhino(row.geometry)
        if rhino_geom:
            rs.AddPolyline([p for p in rhino_geom])
            count += 1
    print("{} elements added to {}".format(count, layer_name))

# =======================
# ADD TO RHINO
# =======================
add_to_rhino(gdf_streets, "OSM_Streets")
add_to_rhino(gdf_buildings, "OSM_Buildings")
add_to_rhino(gdf_greens, "OSM_Greens")

print("Data imported to Rhino.")
