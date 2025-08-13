# -*- coding: utf-8 -*-
# rhino_listener.py
# Listen to the opened Rhino window, auto-import finished OSM jobs, export a boundary
# from the PLOT layer, trigger evaluation, and enable evaluation preview (UI-thread safe).

import os
import sys
import threading
import time
import json  # for HTTP POST payloads
import re

import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs

import System
from System import Action

from create_layers import create_layers_from_json
from config import layer_name  # project variables from config.py

# ===========================
# Settings / Globals
# ===========================
TARGET_LAYER_NAME = layer_name           # e.g. "MASSING"
PLOT_LAYER_NAME = "PLOT"                 # boundary layer to watch
DEBOUNCE_SECONDS = 1.5

listener_active = True
is_running = False
debounce_timer = None

STICKY_KEY = "macad_listener_active"
STICKY_IMPORTED = "macad_imported_jobs"
STICKY_ACTIVE_JOB = "active_job_dir"
STICKY_PLOT_DIRTY = "plot_dirty"
STICKY_PLOT_LAST = "plot_last_candidate"
STICKY_EVAL_PREVIEW_MTIME = "eval_preview_mtime"  # track last previewed evaluation.json mtime
STICKY_UI_STATE_MTIME = "ui_state_mtime"

WATCHER_STARTED_AT = None  # epoch seconds to ignore old DONE.txt

# ---- Paths (project structure aware) ----
THIS_DIR = os.path.dirname(__file__)
PROJECT_DIR = os.path.dirname(THIS_DIR)

# knowledge directory (all live state goes here)
KNOWLEDGE_DIR = os.path.join(PROJECT_DIR, "knowledge")
OSM_DIR = os.path.join(KNOWLEDGE_DIR, "osm")  # OSM jobs root

# Ensure destination exists
try:
    if not os.path.exists(OSM_DIR):
        os.makedirs(OSM_DIR)
except Exception as _e:
    try:
        Rhino.RhinoApp.WriteLine("[rhino_listener] Could not create OSM_DIR: {0}".format(_e))
    except Exception:
        pass

# UI state JSON lives inside knowledge/osm
UI_STATE_PATH = os.path.join(OSM_DIR, "ui_state.json")

# Keep importer dir (module lives in /context)
CONTEXT_DIR = os.path.join(PROJECT_DIR, "context")
IMPORTER_DIR = os.path.join(PROJECT_DIR, "context")

# Ensure importer is importable
if IMPORTER_DIR not in sys.path:
    sys.path.append(IMPORTER_DIR)

# Graph preview (optional)
start_preview = None
stop_preview = None
try:
    if THIS_DIR not in sys.path:
        sys.path.append(THIS_DIR)
    from graph_preview import start_preview, stop_preview
except Exception:
    start_preview = None
    stop_preview = None

# Evaluation preview (optional)
EP = None
try:
    if THIS_DIR not in sys.path:
        sys.path.append(THIS_DIR)
    import evaluation_preview as EP
except Exception:
    EP = None

try:
    import osm_importer  # from context/osm_importer.py
    Rhino.RhinoApp.WriteLine("[rhino_listener] osm_importer loaded from: {0}".format(IMPORTER_DIR))
except Exception as _e:
    osm_importer = None
    Rhino.RhinoApp.WriteLine("[rhino_listener] Warning: could not import osm_importer: {0}".format(_e))

# ===========================
# Layer helpers (robust)
# ===========================
def _layer_name_from_event_obj(ev_obj):
    """Return layer name from a RhinoDoc event object (RhinoObject)."""
    try:
        attrs = getattr(ev_obj, "Attributes", None) or getattr(ev_obj, "ObjectAttributes", None)
        if not attrs:
            return None
        idx = attrs.LayerIndex
        if idx is None or idx < 0:
            return None
        layer = sc.doc.Layers[idx]
        return layer.Name if layer else None
    except:
        return None


def _layer_matches(lname, target_name):
    """Match exact name or parent::child suffix."""
    if not lname:
        return False
    return (lname == target_name) or lname.endswith("::" + target_name)


def _is_object_on_layer(ev_obj, target_name):
    return _layer_matches(_layer_name_from_event_obj(ev_obj), target_name)


def is_on_target_layer(rh_obj):
    return _layer_matches(_layer_name_from_event_obj(rh_obj), TARGET_LAYER_NAME)


# === current-layer protection ===
def _save_current_layer_index():
    """Remember the user's current layer index."""
    try:
        return sc.doc.Layers.CurrentLayerIndex
    except:
        return None


def _restore_current_layer_index(idx):
    """Restore the user's current layer index if still valid."""
    try:
        if idx is None:
            return
        if 0 <= idx < sc.doc.Layers.Count:
            sc.doc.Layers.SetCurrentLayerIndex(idx, True)
    except:
        pass


# ===========================
# Debug helpers
# ===========================
def _debug_event_layer(ev_obj, tag):
    """Print the layer name of the event object for debugging."""
    try:
        attrs = getattr(ev_obj, "Attributes", None) or getattr(ev_obj, "ObjectAttributes", None)
        idx = attrs.LayerIndex if attrs else -1
        lname = sc.doc.Layers[idx].Name if (idx is not None and idx >= 0) else "<none>"
        Rhino.RhinoApp.WriteLine("[rhino_listener] {0}: layer={1}".format(tag, lname))
    except:
        Rhino.RhinoApp.WriteLine("[rhino_listener] {0}: layer=?".format(tag))


# ===========================
# Active job helpers
# ===========================
def _get_active_job_dir():
    return sc.sticky.get(STICKY_ACTIVE_JOB)


def _set_active_job_dir(job_dir):
    sc.sticky[STICKY_ACTIVE_JOB] = job_dir


def _list_job_dirs(osm_root):
    try:
        items = [os.path.join(osm_root, d) for d in os.listdir(osm_root)]
        return [d for d in items if os.path.isdir(d)]
    except:
        return []


def _seed_active_job_dir_from_latest_done():
    """Pick latest DONE job as active if none present (useful when listener starts after a job finished)."""
    try:
        if _get_active_job_dir():
            return
        if not os.path.exists(OSM_DIR):
            return
        dirs = _list_job_dirs(OSM_DIR)
        cand = [d for d in dirs if os.path.exists(os.path.join(d, "DONE.txt"))]
        if not cand:
            return
        cand.sort(key=lambda p: os.path.getmtime(os.path.join(p, "DONE.txt")), reverse=True)
        _set_active_job_dir(cand[0])
        Rhino.RhinoApp.WriteLine("[rhino_listener] Active job set to latest DONE: {0}".format(cand[0]))
    except Exception as e:
        Rhino.RhinoApp.WriteLine("[rhino_listener] seed active job error: {0}".format(e))


# ===========================
# UI preview state helpers
# ===========================
def _read_ui_state():
    try:
        if os.path.exists(UI_STATE_PATH):
            return json.load(open(UI_STATE_PATH, "r"))
    except:
        pass
    return {"context_preview": False, "plot_preview": False}


def _apply_ui_preview_state():
    """Enable/disable conduits according to ui_state.json without changing the current layer."""
    try:
        st = _read_ui_state()
        job_dir = _get_active_job_dir()
        if not job_dir or not os.path.isdir(job_dir):
            Rhino.RhinoApp.WriteLine("[rhino_listener] No active job_dir to apply UI state.")
            return

        # Context Graph
        try:
            if st.get("context_preview"):
                if start_preview:
                    start_preview(job_dir)
            else:
                if stop_preview:
                    stop_preview()
        except:
            pass

        # Plot Graph
        try:
            if st.get("plot_preview") and EP:
                EP.start_evaluation_preview(job_dir)
                Rhino.RhinoApp.WriteLine("[rhino_listener] Plot preview: ENABLED")
            else:
                if EP:
                    EP.stop_evaluation_preview()
                    Rhino.RhinoApp.WriteLine("[rhino_listener] Plot preview: DISABLED")
        except:
            pass

        # Log context toggle status
        try:
            Rhino.RhinoApp.WriteLine("[rhino_listener] Context preview: {0}".format(
                "ENABLED" if st.get("context_preview") else "DISABLED"))
        except:
            pass

    except Exception as e:
        Rhino.RhinoApp.WriteLine("[rhino_listener] apply_ui_preview_state error: {0}".format(e))


def _apply_ui_preview_state_if_changed():
    """Only re-apply when ui_state.json mtime changed."""
    try:
        if not os.path.exists(UI_STATE_PATH):
            return
        m = os.path.getmtime(UI_STATE_PATH)
        last = sc.sticky.get(STICKY_UI_STATE_MTIME)
        if (last is None) or (float(m) > float(last)):
            _apply_ui_preview_state()
            sc.sticky[STICKY_UI_STATE_MTIME] = m
    except:
        pass


# ===========================
# PLOT boundary helpers
# ===========================
def _guid_is_closed_planar(guid):
    try:
        return rs.IsCurveClosed(guid) and rs.IsCurvePlanar(guid)
    except:
        return False


def _curve_to_xy_list(curve, max_points=400, target_seg_len=1.0):
    """Sample a closed planar curve to polyline and return [[x,y], ...]."""
    length = curve.GetLength()
    if length <= 0:
        return None
    # sample count based on target segment length, clamped
    count = int(max(8, min(max_points, max(8, length / max(0.1, target_seg_len)))))
    t0, t1 = curve.Domain.T0, curve.Domain.T1
    ts = [t0 + (t1 - t0) * (i / float(count)) for i in range(count + 1)]
    pts = [curve.PointAt(t) for t in ts]
    xy = [[float(p.X), float(p.Y)] for p in pts]
    if xy and (xy[0] != xy[-1]):
        xy.append(xy[0])
    return xy


def _collect_plot_guids_in_doc():
    """Find objects that are on PLOT (also supports parent::PLOT)."""
    guids = []
    all_objs = rs.AllObjects() or []
    for g in all_objs:
        try:
            layer_name = rs.ObjectLayer(g)  # returns full path "Parent::Child" or plain name
            if _layer_matches(layer_name, PLOT_LAYER_NAME):
                guids.append(g)
        except:
            pass
    return guids


def _export_plot_boundary_to_job(job_dir, candidate_id=None):
    """Export boundary.json from PLOT layer. Prefer candidate_id; fallback to largest-area closed planar curve."""
    try:
        import Rhino.Geometry as rg

        if not job_dir or not os.path.isdir(job_dir):
            Rhino.RhinoApp.WriteLine("[rhino_listener] No active job dir for boundary export.")
            return False

        guids = _collect_plot_guids_in_doc()
        if not guids:
            Rhino.RhinoApp.WriteLine("[rhino_listener] No objects on PLOT (including nested).")
            return False

        chosen_curve = None

        # 1) Try candidate first (last event object)
        if candidate_id and candidate_id in guids and _guid_is_closed_planar(candidate_id):
            chosen_curve = rs.coercecurve(candidate_id)

        # 2) Fallback: largest-area among valid closed planar curves
        if chosen_curve is None:
            best_area = -1.0
            for g in guids:
                if not _guid_is_closed_planar(g):
                    continue
                crv = rs.coercecurve(g)
                if crv is None:
                    continue
                try:
                    amp = rg.AreaMassProperties.Compute(crv)
                    area = amp.Area if amp else 0.0
                except:
                    bb = crv.GetBoundingBox(True)
                    area = abs((bb.Max.X - bb.Min.X) * (bb.Max.Y - bb.Min.Y))
                if area > best_area:
                    best_area = area
                    chosen_curve = crv

        if chosen_curve is None:
            Rhino.RhinoApp.WriteLine("[rhino_listener] No valid closed planar curve found on PLOT.")
            return False

        xy = _curve_to_xy_list(chosen_curve)
        if not xy:
            Rhino.RhinoApp.WriteLine("[rhino_listener] Failed to sample PLOT curve.")
            return False

        out_path = os.path.join(job_dir, "boundary.json")
        with open(out_path, "w") as f:
            json.dump(xy, f, indent=2)

        Rhino.RhinoApp.WriteLine("[rhino_listener] boundary.json written to: {0}".format(out_path))

        # Trigger evaluation AFTER boundary.json is written successfully
        try:
            _trigger_evaluation(job_dir)
        except:
            pass

        return True

    except Exception as e:
        Rhino.RhinoApp.WriteLine("[rhino_listener] Boundary export error: {0}".format(e))
        return False


def _mark_plot_dirty(guid=None):
    sc.sticky[STICKY_PLOT_DIRTY] = True
    if guid is not None:
        sc.sticky[STICKY_PLOT_LAST] = guid


# ===========================
# HTTP helpers / evaluation trigger
# ===========================
def _http_post_json(url, payload):
    try:
        from System.Net import WebClient
        from System.Text import UTF8Encoding
        wc = WebClient()
        wc.Headers.Add("Content-Type", "application/json")
        data = UTF8Encoding(False).GetBytes(json.dumps(payload))
        resp_bytes = wc.UploadData(url, "POST", data)
        return UTF8Encoding(False).GetString(resp_bytes)
    except Exception as e:
        Rhino.RhinoApp.WriteLine("[rhino_listener] HTTP POST error: {0}".format(e))
        return None


def _trigger_evaluation(job_dir):
    try:
        url = "http://127.0.0.1:8000/evaluate/run"
        payload = {"job_dir": job_dir}
        _http_post_json(url, payload)
        Rhino.RhinoApp.WriteLine("[rhino_listener] Evaluation triggered for: {0}".format(job_dir))
    except Exception as e:
        Rhino.RhinoApp.WriteLine("[rhino_listener] Eval trigger error: {0}".format(e))


# ===========================
# Evaluation preview (enable when evaluation.json appears/updates)
# ===========================
def _try_evaluation_preview(job_dir):
    try:
        if not EP:
            return False
        ejson = os.path.join(job_dir, "evaluation.json")
        if not os.path.exists(ejson):
            return False
        mtime = os.path.getmtime(ejson)
        last_mtime = sc.sticky.get(STICKY_EVAL_PREVIEW_MTIME)
        if (last_mtime is None) or (mtime > float(last_mtime)):
            EP.start_evaluation_preview(job_dir)
            sc.sticky[STICKY_EVAL_PREVIEW_MTIME] = mtime
            Rhino.RhinoApp.WriteLine("[rhino_listener] Evaluation preview enabled.")
            return True
    except Exception as e:
        Rhino.RhinoApp.WriteLine("[rhino_listener] Eval preview error: {0}".format(e))
    return False


# ===========================
# Job folder renaming (timestamped)
# ===========================
def _is_timestamped_job_name(name):
    """Return True if name matches osm_YYYYMMDD_HHMMSS or osm_YYYYMMDD_HHMMSS_N."""
    try:
        return bool(re.match(r"^osm_\d{8}_\d{6}(?:_\d+)?$", name or ""))
    except:
        return False


def _format_ts_from_epoch(ts):
    try:
        return time.strftime("%Y%m%d_%H%M%S", time.localtime(float(ts)))
    except:
        return time.strftime("%Y%m%d_%H%M%S")


def _rename_job_dir_to_timestamp(job_dir):
    """Rename a finished job folder to osm_YYYYMMDD_HHMMSS[_N] based on DONE.txt mtime."""
    try:
        base = os.path.basename(job_dir.rstrip("\\/"))
        if _is_timestamped_job_name(base):
            return job_dir  # already formatted

        done_flag = os.path.join(job_dir, "DONE.txt")
        if os.path.exists(done_flag):
            try:
                ts = os.path.getmtime(done_flag)
            except:
                ts = time.time()
        else:
            ts = time.time()

        new_base = "osm_" + _format_ts_from_epoch(ts)
        parent = os.path.dirname(job_dir)
        candidate = os.path.join(parent, new_base)

        if os.path.exists(candidate):
            i = 2
            while True:
                alt = os.path.join(parent, "{}_{}".format(new_base, i))
                if not os.path.exists(alt):
                    candidate = alt
                    break
                i += 1

        try:
            os.rename(job_dir, candidate)
            Rhino.RhinoApp.WriteLine("[rhino_listener] Renamed job:\n  {0}\n-> {1}".format(job_dir, candidate))
            return candidate
        except Exception as e:
            Rhino.RhinoApp.WriteLine("[rhino_listener] Rename failed ({0}); keeping original.".format(e))
            return job_dir
    except Exception as e:
        Rhino.RhinoApp.WriteLine("[rhino_listener] _rename_job_dir_to_timestamp error: {0}".format(e))
        return job_dir


# ===========================
# Main change handler + debounce
# ===========================
def handle_layer_change():
    global is_running
    if is_running:
        return
    is_running = True
    try:
        wrote = False

        # 1) If PLOT is marked dirty, export with candidate preference
        if sc.sticky.get(STICKY_PLOT_DIRTY):
            sc.sticky[STICKY_PLOT_DIRTY] = False
            job_dir = sc.sticky.get(STICKY_ACTIVE_JOB)
            candidate = sc.sticky.get(STICKY_PLOT_LAST)
            wrote = _export_plot_boundary_to_job(job_dir, candidate_id=candidate) or False
            if not wrote:
                Rhino.RhinoApp.WriteLine("[rhino_listener] PLOT changed but boundary export failed.")

        # 2) Passive fallback: try exporting any valid PLOT boundary if nothing written
        if not wrote:
            job_dir = sc.sticky.get(STICKY_ACTIVE_JOB)
            if job_dir and os.path.isdir(job_dir):
                wrote = _export_plot_boundary_to_job(job_dir, candidate_id=None) or False

        # 3) Try to enable evaluation preview if results are ready/updated
        job_dir = sc.sticky.get(STICKY_ACTIVE_JOB)
        if job_dir:
            _try_evaluation_preview(job_dir)

        # 4) Apply UI preview toggles
        _apply_ui_preview_state_if_changed()

        Rhino.RhinoApp.WriteLine("[rhino_listener] Debounced change processed.")
    finally:
        is_running = False


def debounce_trigger():
    global debounce_timer
    if debounce_timer and debounce_timer.is_alive():
        return

    def delayed():
        time.sleep(DEBOUNCE_SECONDS)
        if not listener_active:
            return
        handle_layer_change()

    debounce_timer = threading.Thread(target=delayed)
    try:
        debounce_timer.setDaemon(True)
    except:
        pass
    debounce_timer.start()


# ===========================
# Event handlers
# ===========================
def on_add(sender, e):
    _debug_event_layer(e.Object, "on_add")

    # MASSING (target layer)
    if listener_active and is_on_target_layer(e.Object):
        debounce_trigger()

    # PLOT
    try:
        if listener_active and _is_object_on_layer(e.Object, PLOT_LAYER_NAME):
            _mark_plot_dirty(e.Object.Id)
            Rhino.RhinoApp.WriteLine("[rhino_listener] PLOT on_add: guid={0}".format(e.Object.Id))
            debounce_trigger()
    except:
        pass


def on_modify(sender, e):
    _debug_event_layer(e.Object, "on_modify")

    if listener_active and is_on_target_layer(e.Object):
        debounce_trigger()
    try:
        if listener_active and _is_object_on_layer(e.Object, PLOT_LAYER_NAME):
            _mark_plot_dirty(e.Object.Id)
            Rhino.RhinoApp.WriteLine("[rhino_listener] PLOT on_modify: guid={0}".format(e.Object.Id))
            debounce_trigger()
    except:
        pass


def on_replace(sender, e):
    _debug_event_layer(e.NewObject, "on_replace")

    if listener_active and is_on_target_layer(e.NewObject):
        debounce_trigger()
    try:
        if listener_active and _is_object_on_layer(e.NewObject, PLOT_LAYER_NAME):
            _mark_plot_dirty(e.NewObject.Id)
            Rhino.RhinoApp.WriteLine("[rhino_listener] PLOT on_replace: guid={0}".format(e.NewObject.Id))
            debounce_trigger()
    except:
        pass


def on_delete(sender, e):
    try:
        _debug_event_layer(e.Object, "on_delete")
    except:
        pass
    # Cannot reliably check layer on delete -> may trigger for any delete
    debounce_trigger()


# ===========================
# Import finished OSM jobs
# ===========================
def _job_id_from_path(p):
    try:
        return os.path.basename(p.rstrip("\\/"))
    except:
        return p


def _import_job_on_ui(job_dir, job_id, imported_registry):
    def _do_import():
        # Save current layer before any operation that might change it
        saved_layer_idx = _save_current_layer_index()
        try:
            Rhino.RhinoApp.WriteLine("[rhino_listener] Importing job {0} on UI thread...".format(job_id))
            try:
                rs.EnableRedraw(False)
            except:
                pass

            # Import/bake OSM content; this may change the current layer internally
            total = osm_importer.import_osm_folder(job_dir)
            Rhino.RhinoApp.WriteLine("[rhino_listener] OSM import complete ({0} elements).".format(total))

            # Mark active job for boundary export and preview tracking
            _set_active_job_dir(job_dir)
            sc.sticky[STICKY_EVAL_PREVIEW_MTIME] = None

            # Apply current UI preview state for this graph (does not touch current layer)
            _apply_ui_preview_state_if_changed()

            # Graph preview info
            try:
                gjson = os.path.join(job_dir, "graph.json")
                if start_preview and os.path.exists(gjson):
                    # start/stop handled by _apply_ui_preview_state_if_changed()
                    pass
                else:
                    Rhino.RhinoApp.WriteLine("[rhino_listener] graph.json not found; no preview.")
            except Exception as pe:
                Rhino.RhinoApp.WriteLine("[rhino_listener] Graph preview error: {0}".format(pe))

        except Exception as e:
            Rhino.RhinoApp.WriteLine("[rhino_listener] Import error for job {0}: {1}".format(job_id, e))
        finally:
            # Always restore user's current layer
            _restore_current_layer_index(saved_layer_idx)
            try:
                rs.EnableRedraw(True)
            except:
                pass
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


def _ensure_imported_registry():
    reg = sc.sticky.get(STICKY_IMPORTED)
    if reg is None:
        reg = set()
        sc.sticky[STICKY_IMPORTED] = reg
    return reg


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

        # Rename job folder to timestamp format before importing
        renamed_dir = _rename_job_dir_to_timestamp(job_dir)
        if renamed_dir != job_dir:
            job_dir = renamed_dir
            job_id = os.path.basename(job_dir.rstrip("\\/"))

        _import_job_on_ui(job_dir, job_id, imported)
        # Handle one job per tick
        break


def _watcher_loop():
    Rhino.RhinoApp.WriteLine("[rhino_listener] OSM watcher started. Folder: {0}".format(OSM_DIR))
    while listener_active:
        try:
            _try_import_finished_job()
            # also poll for evaluation results to start preview automatically
            job_dir = _get_active_job_dir()
            if job_dir:
                _try_evaluation_preview(job_dir)
            # and reflect UI toggles if they changed (does not change current layer)
            _apply_ui_preview_state_if_changed()
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


# ===========================
# Setup / teardown
# ===========================
def setup_layer_listener():
    global WATCHER_STARTED_AT
    if sc.sticky.get(STICKY_KEY):
        Rhino.RhinoApp.WriteLine("[rhino_listener] Already active on '{0}'.".format(TARGET_LAYER_NAME))
        return

    json_path = os.path.join(os.path.dirname(__file__), "layers.json")
    Rhino.RhinoApp.WriteLine("[rhino_listener] Creating layers from: {0}".format(json_path))

    # Protect user's current layer during layer creation
    saved_layer_idx = _save_current_layer_index()
    try:
        create_layers_from_json(json_path)
        Rhino.RhinoApp.WriteLine("[rhino_listener] Layers created.")
    except Exception as e:
        Rhino.RhinoApp.WriteLine("[rhino_listener] Failed to create layers: {0}".format(e))
    finally:
        _restore_current_layer_index(saved_layer_idx)

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

    # If there is no active job yet (e.g., listener started after a job finished), seed it.
    _seed_active_job_dir_from_latest_done()

    # Apply current UI toggles on boot (does not change current layer)
    _apply_ui_preview_state_if_changed()


def remove_layer_listener():
    try:
        Rhino.RhinoDoc.AddRhinoObject -= on_add
    except:
        pass
    try:
        Rhino.RhinoDoc.ModifyObjectAttributes -= on_modify
    except:
        pass
    try:
        Rhino.RhinoDoc.ReplaceRhinoObject -= on_replace
    except:
        pass
    try:
        Rhino.RhinoDoc.DeleteRhinoObject -= on_delete
    except:
        pass
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
