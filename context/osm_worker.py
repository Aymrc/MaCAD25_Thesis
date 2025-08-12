# osm_worker.py - Python 3 OSM downloader for Rhino pipeline
# Inputs via env: LAT, LON, RADIUS_KM, OUT_DIR
# Outputs: streets.geojson, buildings.geojson, greens.geojson, DONE.txt/FAILED.txt

import os
import sys
import json
import time
import traceback
import subprocess  # moved to top-level to avoid local shadowing

# Third-party libs (install via requirements.txt): osmnx, geopandas
import osmnx as ox
from osmnx.projection import project_gdf
import geopandas as gpd

def getenv_float(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return float(default)

def main():
    lat = getenv_float("LAT", 41.3874)
    lon = getenv_float("LON", 2.1686)
    radius_km = getenv_float("RADIUS_KM", 0.5)
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    knowledge_dir = os.path.join(project_root, "knowledge")
    default_runtime = os.path.abspath(os.path.join(knowledge_dir, "osm", "_tmp"))

    out_dir = os.environ.get("OUT_DIR", default_runtime)
    os.makedirs(out_dir, exist_ok=True)

    # Configure OSMnx cache to speed up repeated queries
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    cache_dir = os.path.join(project_root, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = cache_dir
    ox.settings.log_console = False
    ox.settings.overpass_endpoint = "https://overpass-api.de/api"
    ox.settings.timeout = 300

    location_point = (lat, lon)
    dist_m = max(1.0, radius_km * 1000.0)

    tags_streets = {"highway": True}
    tags_buildings = {"building": True}
    tags_greens = {
        "leisure": ["park", "garden"],
        "landuse": ["grass", "recreation_ground", "cemetery"],
    }

    print("OSM worker starting...", flush=True)
    print("Lat: {0}, Lon: {1}, Radius_km: {2}".format(lat, lon, radius_km), flush=True)
    print("Output dir: {0}".format(out_dir), flush=True)

    try:
        # Retry helper for transient Overpass issues
        def fetch_with_retries(fn, *args, **kwargs):
            attempts = 3
            delay = 5
            last_exc = None
            for i in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    print("Fetch attempt {0}/{1} failed: {2}".format(i + 1, attempts, e), flush=True)
                    time.sleep(delay)
            raise last_exc

        print("Downloading streets...", flush=True)
        gdf_streets = fetch_with_retries(
            ox.features_from_point, location_point, tags=tags_streets, dist=dist_m
        )
        print("Downloading buildings...", flush=True)
        gdf_buildings = fetch_with_retries(
            ox.features_from_point, location_point, tags=tags_buildings, dist=dist_m
        )
        print("Downloading green areas...", flush=True)
        gdf_greens = fetch_with_retries(
            ox.features_from_point, location_point, tags=tags_greens, dist=dist_m
        )

        # Project to a metric CRS for consistent geometry operations
        gdf_streets = project_gdf(gdf_streets)
        gdf_buildings = project_gdf(gdf_buildings)
        gdf_greens = project_gdf(gdf_greens)

        # Recentering around streets centroid if available; fallback to combined centroid
        def calc_centroid():
            try:
                if len(gdf_streets) > 0:
                    return gdf_streets.unary_union.centroid.coords[0]
            except Exception:
                pass
            merged = []
            for gdf in (gdf_streets, gdf_buildings, gdf_greens):
                try:
                    if len(gdf) > 0:
                        merged.append(gdf.unary_union)
                except Exception:
                    pass
            if merged:
                import shapely
                from shapely.geometry import GeometryCollection
                geom = GeometryCollection(merged)
                return geom.centroid.coords[0]
            return (0.0, 0.0)

        cx, cy = calc_centroid()
        print("Recentering to origin using centroid: ({0}, {1})".format(cx, cy), flush=True)

        def recenter_gdf(gdf, cx, cy):
            if len(gdf) == 0:
                return gdf
            gdf = gdf.copy()
            # Shapely translate on each geometry
            gdf["geometry"] = gdf["geometry"].translate(-cx, -cy)
            return gdf

        gdf_streets = recenter_gdf(gdf_streets, cx, cy)
        gdf_buildings = recenter_gdf(gdf_buildings, cx, cy)
        gdf_greens = recenter_gdf(gdf_greens, cx, cy)

        streets_path = os.path.join(out_dir, "streets.geojson")
        buildings_path = os.path.join(out_dir, "buildings.geojson")
        greens_path = os.path.join(out_dir, "greens.geojson")

        print("Writing GeoJSON files...", flush=True)
        gdf_streets.to_file(streets_path, driver="GeoJSON")
        gdf_buildings.to_file(buildings_path, driver="GeoJSON")
        gdf_greens.to_file(greens_path, driver="GeoJSON")

        try:
            # Build graph alongside the OSM exports
            env = os.environ.copy()
            env["OUT_DIR"] = out_dir
            graph_script = os.path.join(os.path.dirname(__file__), "graph_builder.py")

            log_out = os.path.join(out_dir, "graph_builder_stdout.log")
            log_err = os.path.join(out_dir, "graph_builder_stderr.log")

            with open(log_out, "w") as fout, open(log_err, "w") as ferr:
                proc = subprocess.Popen(
                    [sys.executable, "-u", graph_script],
                    cwd=os.path.dirname(__file__),
                    env=env,
                    stdout=fout,
                    stderr=ferr
                )
                ret = proc.wait()

            if ret == 0 and os.path.exists(os.path.join(out_dir, "graph.json")):
                print("Graph built successfully.", flush=True)
            else:
                print("Graph build failed. See logs:", log_out, log_err, flush=True)

        except Exception as _e:
            print("Graph build failed (exception): {0}".format(_e), flush=True)

        with open(os.path.join(out_dir, "DONE.txt"), "w") as f:
            f.write("ok")

        print("OSM worker finished successfully.", flush=True)

    except Exception as e:
        err_path = os.path.join(out_dir, "FAILED.txt")
        with open(err_path, "w") as f:
            f.write("{0}\n\n{1}".format(str(e), traceback.format_exc()))
        print("OSM worker failed. See FAILED.txt for details.", flush=True)
        # raise  # optional re-raise

if __name__ == "__main__":
    main()