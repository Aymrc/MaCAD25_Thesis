# osm_importer.py - IronPython 2.7 inside Rhino
# Import GeoJSON files (streets, buildings, greens) into Rhino layers

import os
import json
import rhinoscriptsyntax as rs
import Rhino
import scriptcontext as sc
import Rhino.Geometry as rg
import System.Drawing as sd

def _ensure_sublayer(parent_name, child_name):
    
    if not rs.IsLayer(parent_name):
        rs.AddLayer(parent_name)
    full_name = parent_name + "::" + child_name
    if not rs.IsLayer(full_name):
        # Colors
        color = None
        if child_name.lower() == "streets":
            color = sd.Color.FromArgb(128, 128, 128)
        elif child_name.lower() == "greens":
            color = sd.Color.FromArgb(0, 128, 0)

        if color:
            rs.AddLayer(child_name, color, parent=parent_name)
        else:
            rs.AddLayer(child_name, parent=parent_name)

    rs.CurrentLayer(full_name)
    return full_name

def _add_polyline(coords):
    """Add a polyline from a list of [x, y] pairs."""
    pts = [rg.Point3d(float(x), float(y), 0.0) for x, y in coords]
    if len(pts) >= 2:
        rs.AddPolyline(pts)

def _add_polygon(rings):
    """
    Add polygon exterior as polyline. Ignores holes.
    rings[0] is exterior, rest are holes.
    """
    if not rings:
        return
    _add_polyline(rings[0])

def _import_geojson(path, layer, expect):
    """
    Import a GeoJSON file into a specific Rhino layer.
    expect: "line", "poly" or "any"
    """
    if not os.path.exists(path):
        Rhino.RhinoApp.WriteLine("[osm_importer] Not found: {0}".format(path))
        return 0
    #_ensure_layer(layer)
    full_layer_name = _ensure_sublayer("OSM", layer)
    
    count = 0
    with open(path, "r") as f:
        data = json.load(f)
    feats = data.get("features", [])
    for feat in feats:
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        if gtype == "LineString" and (expect in ("line", "any")):
            _add_polyline(coords)
            count += 1
        elif gtype == "MultiLineString" and (expect in ("line", "any")):
            for part in coords:
                _add_polyline(part)
                count += 1
        elif gtype == "Polygon" and (expect in ("poly", "any")):
            _add_polygon(coords)
            count += 1
        elif gtype == "MultiPolygon" and (expect in ("poly", "any")):
            for poly in coords:
                _add_polygon(poly)
                count += 1
    Rhino.RhinoApp.WriteLine("[osm_importer] {0} elements added to {1}".format(count, layer))
    return count

def import_osm_folder(folder):
    """Import all OSM layers from a job folder."""
    streets = os.path.join(folder, "streets.geojson")
    buildings = os.path.join(folder, "buildings.geojson")
    greens = os.path.join(folder, "greens.geojson")
    total = 0
    total += _import_geojson(streets,  "Streets",  "line")  # OSM::Streets
    total += _import_geojson(buildings,"Buildings","poly")  # OSM::Buildings
    total += _import_geojson(greens,   "Greens",   "poly")  # OSM::Greens
    Rhino.RhinoApp.WriteLine("[osm_importer] Total elements imported: {0}".format(total))
    return total

if __name__ == "__main__":
    # Ask user to select a folder containing GeoJSON files
    folder = rs.BrowseForFolder(message="Select OSM job folder")
    if folder:
        import_osm_folder(folder)
