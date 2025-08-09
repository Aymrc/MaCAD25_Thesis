# -*- coding: utf-8 -*-
# rhino_listener.py
# Listen to the opened Rhino window and auto-import finished OSM jobs (UI-thread safe)

import os
import sys
import threading
import time

import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs

import System
from System import Action

from create_layers import create_layers_from_json
from config import layer_name  # project variables from config.py

TARGET_LAYER_NAME = layer_name
listener_active = True
is_running = False
debounce_timer = None

STICKY_KEY = "macad_listener_active"
STICKY_IMPORTED = "macad_imported_jobs"
WATCHER_STARTED_AT = None  # epoch seconds to ignore old DONE.txt

# ---- Paths (project structure aware) ----
THIS_DIR = os.path.dirname(__file__)
PROJECT_DIR = os.path.dirname(THIS_DIR)  # parent of 2_rhino -> MaCAD25_Thesis
OSM_DIR = os.path.join(PROJECT_DIR, "runtime", "osm")
IMPORTER_DIR = os.path.join(PROJECT_DIR, "1_context")

# Ensure importer is importable
if IMPORTER_DIR not in sys.path:
    sys.path.append(IMPORTER_DIR)

try:
    import osm_importer  # from 1_context/osm_importer.py
    Rhino.RhinoApp.WriteLine("[rhino_listener] osm_importer loaded from: {0}".format(IMPORTER_DIR))
except Exception as _e:
    osm_importer = None
    Rhino.RhinoApp.WriteLine("[rhino_listener] Warning: could not import osm_importer: {0}".format(_e))

# ---- Layer change handling ----
def is_on_target_layer(rh_obj):
    try:
        layer_index = rh_obj.Attributes.LayerIndex
        if layer_index < 0:
            return False
        layer = sc.doc.Layers[layer_index]
        return (layer is not None) and (layer.Name == TARGET_LAYER_NAME)
    except:
        return False

def handle_layer_change():
    global is_running
    if is_running:
        return
    is_running = True
    try:
        Rhino.RhinoApp.WriteLine("[rhino_listener] Geometry on '{0}' layer changed.".format(TARGET_LAYER_NAME))
        # TODO: Insert your actual logic here
    finally:
        is_running = False

def debounce_trigger():
    global debounce_timer
    if debounce_timer and debounce_timer.is_alive():
        return

    def delayed():
        time.sleep(1.0)
        if not listener_active:
            return
        handle_layer_change()

    debounce_timer = threading.Thread(target=delayed)
    try:
        debounce_timer.setDaemon(True)
    except:
        pass
    debounce_timer.start()

def on_add(sender, e):
    if listener_active and is_on_target_layer(e.Object):
        debounce_trigger()

def on_modify(sender, e):
    if listener_active and is_on_target_layer(e.Object):
        debounce_trigger()

def on_replace(sender, e):
    if listener_active and is_on_target_layer(e.NewObject):
        debounce_trigger()

def on_delete(sender, e):
    # Cannot reliably check layer on delete -> may trigger for any delete
    debounce_trigger()

# ---- Helpers for OSM watcher ----
def _list_job_dirs(osm_root):
    try:
        items = [os.path.join(osm_root, d) for d in os.listdir(osm_root)]
        return [d for d in items if os.path.isdir(d)]
    except:
        return []

def _ensure_imported_registry():
    reg = sc.sticky.get(STICKY_IMPORTED)
    if reg is None:
        reg = set()
        sc.sticky[STICKY_IMPORTED] = reg
    return reg

def _job_id_from_path(p):
    try:
        return os.path.basename(p.rstrip("\\/"))
    except:
        return p

def _import_job_on_ui(job_dir, job_id, imported_registry):
    """Run the GeoJSON import on Rhino's UI thread to avoid crashes."""
    def _do_import():
        try:
            Rhino.RhinoApp.WriteLine("[rhino_listener] Importing job {0} on UI thread...".format(job_id))
            try:
                rs.EnableRedraw(False)
            except:
                pass
            total = osm_importer.import_osm_folder(job_dir)
            Rhino.RhinoApp.WriteLine("[rhino_listener] OSM import complete ({0} elements).".format(total))
        except Exception as e:
            Rhino.RhinoApp.WriteLine("[rhino_listener] Import error for job {0}: {1}".format(job_id, e))
        finally:
            try:
                rs.EnableRedraw(True)
            except:
                pass
            # Persist and mark as processed
            try:
                open(os.path.join(job_dir, "IMPORTED.txt"), "w").write("ok")
            except:
                pass
            imported_registry.add(job_id)

    try:
        Rhino.RhinoApp.InvokeOnUiThread(Action(_do_import))
    except Exception as e:
        Rhino.RhinoApp.WriteLine("[rhino_listener] InvokeOnUiThread failed ({0}); running inline.".format(e))
        _do_import()

def _try_import_finished_job():
    if not osm_importer:
        return
    if not os.path.exists(OSM_DIR):
        return

    job_dirs = _list_job_dirs(OSM_DIR)
    if not job_dirs:
        return
    # Newest first
    job_dirs.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    imported = _ensure_imported_registry()

    for job_dir in job_dirs:
        job_id = _job_id_from_path(job_dir)
        if job_id in imported:
            continue

        done_flag = os.path.join(job_dir, "DONE.txt")
        fail_flag = os.path.join(job_dir, "FAILED.txt")
        imported_flag = os.path.join(job_dir, "IMPORTED.txt")

        # Skip if already imported in a previous session
        if os.path.exists(imported_flag):
            imported.add(job_id)
            continue

        if os.path.exists(fail_flag):
            Rhino.RhinoApp.WriteLine("[rhino_listener] OSM job {0} failed. See FAILED.txt".format(job_id))
            imported.add(job_id)
            continue

        if not os.path.exists(done_flag):
            continue  # not finished yet

        # Ignore DONE older than watcher start
        try:
            done_mtime = os.path.getmtime(done_flag)
        except:
            done_mtime = None

        if (WATCHER_STARTED_AT is not None) and (done_mtime is not None):
            if done_mtime < WATCHER_STARTED_AT:
                imported.add(job_id)
                Rhino.RhinoApp.WriteLine("[rhino_listener] Skipping old OSM job {0} (finished before listener start).".format(job_id))
                continue

        Rhino.RhinoApp.WriteLine("[rhino_listener] OSM job {0} finished. Queuing import...".format(job_id))
        _import_job_on_ui(job_dir, job_id, imported)
        # Handle one job per tick
        break

def _watcher_loop():
    Rhino.RhinoApp.WriteLine("[rhino_listener] OSM watcher started. Folder: {0}".format(OSM_DIR))
    while listener_active:
        try:
            _try_import_finished_job()
        except Exception as e:
            Rhino.RhinoApp.WriteLine("[rhino_listener] Watcher error: {0}".format(e))
        time.sleep(3.0)
    Rhino.RhinoApp.WriteLine("[rhino_listener] OSM watcher stopped.")

def _start_watcher_thread():
    t = threading.Thread(target=_watcher_loop)
    try:
        t.setDaemon(True)
    except:
        pass
    t.start()
    return t

# ---- Setup / teardown ----
def setup_layer_listener():
    global WATCHER_STARTED_AT
    if sc.sticky.get(STICKY_KEY):
        Rhino.RhinoApp.WriteLine("[rhino_listener] Already active on '{0}'.".format(TARGET_LAYER_NAME))
        return

    json_path = os.path.join(os.path.dirname(__file__), "layers.json")
    Rhino.RhinoApp.WriteLine("[rhino_listener] Creating layers from: {0}".format(json_path))
    try:
        create_layers_from_json(json_path)
        Rhino.RhinoApp.WriteLine("[rhino_listener] Layers created.")
    except Exception as e:
        Rhino.RhinoApp.WriteLine("[rhino_listener] Failed to create layers: {0}".format(e))

    Rhino.RhinoDoc.AddRhinoObject += on_add
    Rhino.RhinoDoc.ModifyObjectAttributes += on_modify
    Rhino.RhinoDoc.ReplaceRhinoObject += on_replace
    Rhino.RhinoDoc.DeleteRhinoObject += on_delete

    sc.sticky[STICKY_KEY] = True
    Rhino.RhinoApp.WriteLine("[rhino_listener] Layer-specific listener active on '{0}'.".format(TARGET_LAYER_NAME))

    # Mark watcher start time to ignore old DONE flags
    try:
        WATCHER_STARTED_AT = time.time()
    except:
        WATCHER_STARTED_AT = None

    _start_watcher_thread()

def remove_layer_listener():
    try: Rhino.RhinoDoc.AddRhinoObject -= on_add
    except: pass
    try: Rhino.RhinoDoc.ModifyObjectAttributes -= on_modify
    except: pass
    try: Rhino.RhinoDoc.ReplaceRhinoObject -= on_replace
    except: pass
    try: Rhino.RhinoDoc.DeleteRhinoObject -= on_delete
    except: pass
    sc.sticky[STICKY_KEY] = False
    Rhino.RhinoApp.WriteLine("[rhino_listener] Layer listener removed.")

def shutdown_listener():
    global listener_active
    listener_active = False
    remove_layer_listener()
    Rhino.RhinoApp.WriteLine("[rhino_listener] Listener shut down.")

# Run once from Rhino:
# _-RunPythonScript "C:\\...\\2_rhino\\rhino_listener.py"
if __name__ == "__main__":
    setup_layer_listener()
