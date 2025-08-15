# -*- coding: utf-8 -*-
# main.py — Launcher compatible with IronPython 2.7 (Rhino)
# Starts the backend (LLM) with an external Python 3 and opens the local UI.

# Rhino button:
# ! _-RunPythonScript "C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis\main.py"
# _-RunPythonScript "C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis\rhino\rhino_listener.py"

import os, sys, subprocess, webbrowser, shutil, re, stat

# Import project variables
try:
    from config import layer_name, copilot_name, python_exe_AB, python_exe_CH
except Exception as _e:
    # Default values if config.py does not exist or variables are missing
    layer_name = "COPILOT"
    copilot_name = "Rhino Copilot"
    python_exe_AB = ""
    python_exe_CH = ""

# === Base paths ===
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
LLM_DIR = os.path.join(CURRENT_DIR, "llm")
RHINO_DIR = os.path.join(CURRENT_DIR, "rhino")
UI_DIR = os.path.join(CURRENT_DIR, "ui")
OSM_DIR = os.path.join(CURRENT_DIR, "knowledge", "osm")  # <-- raíz OSM para limpieza

# Ensure local imports
if LLM_DIR not in sys.path:
    sys.path.append(LLM_DIR)
if RHINO_DIR not in sys.path:
    sys.path.append(RHINO_DIR)

def _safe_print(msg):
    try:
        print(msg)
    except:
        print(str(msg))

# ---------- Delete previous OSM ----------
_UUID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

def _looks_like_uuid(name):
    try:
        return bool(_UUID_RX.match(name))
    except:
        return False

def _delete_folder(p):
    try:
        shutil.rmtree(p, ignore_errors=True)
        return True
    except Exception:
        try:
            trash = p + "_trash"
            os.rename(p, trash)
            shutil.rmtree(trash, ignore_errors=True)
            return True
        except Exception:
            return False

def purge_osm_temp_dirs():
    try:
        if not os.path.isdir(OSM_DIR):
            return
        entries = []
        try:
            entries = os.listdir(OSM_DIR)
        except Exception:
            entries = []
        for name in entries:
            full = os.path.join(OSM_DIR, name)
            if not os.path.isdir(full):
                continue
            if name == "_tmp" or name.startswith("osm_") or _looks_like_uuid(name):
                _safe_print("[CLEAN] Removing OSM workspace: {}".format(full))
                _delete_folder(full)
    except Exception as e:
        _safe_print("[CLEAN] OSM purge error: {}".format(e))
# ------------------------------------------------------

def get_universal_python_path():
    """
    Find a Python 3 executable to launch the backend.
    - If Rhino is running with IronPython, this will be an external executable.
    - Avoid using shutil.which (not available in IronPython 2.7).
    """

    # Known paths (includes those from config.py if set)
    username = os.getenv("USERNAME") or ""
    possible_paths = [
        python_exe_AB,
        python_exe_CH,
        r"C:\Python313\python.exe",
        r"C:\Python312\python.exe",
        r"C:\Python311\python.exe",
        r"C:\Program Files\Python313\python.exe",
        r"C:\Program Files\Python312\python.exe",
        r"C:\Program Files\Python311\python.exe",
        r"C:\Users\{}\AppData\Local\Programs\Python\Python313\python.exe".format(username),
        r"C:\Users\{}\AppData\Local\Programs\Python\Python312\python.exe".format(username),
        r"C:\Users\{}\AppData\Local\Programs\Python\Python311\python.exe".format(username),
        # Official Python launcher on Windows:
        r"C:\Windows\py.exe",
    ]
    for path in possible_paths:
        if path and os.path.exists(path):
            _safe_print("[{}] Found fallback Python: {}".format(copilot_name, path))
            return path
        
    # If sys.executable points to something valid and is NOT IronPython, use it.
    if sys.executable and os.path.exists(sys.executable):
        v = ""
        try:
            v = sys.version
        except:
            v = ""
        if "IronPython" not in v:
            _safe_print("Using current Python: {}".format(sys.executable))
            return sys.executable

    # Search in PATH using distutils.spawn (available in IronPython 2.7)
    try:
        import distutils.spawn
        for candidate in ('python', 'python3', 'py'):
            path = distutils.spawn.find_executable(candidate)
            if path and os.path.exists(path):
                _safe_print("Found Python in PATH: {}".format(path))
                return path
    except Exception as e:
        _safe_print("PATH lookup failed: {}".format(e))

    _safe_print("No valid Python interpreter found.")
    return None

def _run_pip_install(python_exe, requirements_path):
    """
    Try to install requirements.txt using the found Python.
    Supports both 'python.exe -m pip' and 'py -3 -m pip'.
    """
    if not os.path.exists(requirements_path):
        _safe_print("[SETUP] requirements.txt not found: {}".format(requirements_path))
        return

    try:
        if os.path.basename(python_exe).lower() == "py.exe":
            cmd = [python_exe, "-3", "-m", "pip", "install", "-r", requirements_path]
        else:
            cmd = [python_exe, "-m", "pip", "install", "-r", requirements_path]

        _safe_print("[SETUP] Running: {}".format(" ".join(cmd)))
        subprocess.check_call(cmd, cwd=CURRENT_DIR)
        _safe_print("[SETUP] Requirements installed.")
    except Exception as e:
        _safe_print("[SETUP] Failed to install requirements: {}".format(e))

def install_requirements(python_exe):
    req = os.path.join(CURRENT_DIR, "requirements.txt")
    _run_pip_install(python_exe, req)

def start_llm():
    """
    Launch the LLM backend (FastAPI, etc.) using the external Python 3.
    This file does NOT attempt to run FastAPI with IronPython.
    """
    _safe_print("[LLM] Starting backend...")
    llm_script = os.path.join(LLM_DIR, "llm.py")
    python_exe = get_universal_python_path()

    if not python_exe or not os.path.exists(python_exe):
        _safe_print("[LLM] No valid Python 3 found. Aborting.")
        return

    if not os.path.exists(llm_script):
        _safe_print("[LLM] llm.py not found at: {}".format(llm_script))
        return

    try:
        if os.path.basename(python_exe).lower() == "py.exe":
            cmd = [python_exe, "-3", llm_script]
        else:
            cmd = [python_exe, llm_script]

        subprocess.Popen(cmd, cwd=LLM_DIR, creationflags=0)
        # _safe_print("[LLM] Backend launched at http://127.0.0.1:8000")
    except Exception as e:
        _safe_print("[LLM] Failed to launch backend: {}".format(e))

def start_ui():
    _safe_print("[UI] Opening interface...")
    ui_path = os.path.join(UI_DIR, "index.html")
    if os.path.exists(ui_path):
        file_url = "file:///" + ui_path.replace("\\", "/")
        _safe_print("[UI] Opening: {}".format(file_url))
        try:
            webbrowser.open(file_url)
        except Exception as e:
            _safe_print("[UI] Failed to open browser: {}".format(e))
    else:
        _safe_print("[UI] landing.html not found at: {}".format(ui_path))

def clean_history(py):
    script = os.path.join(CURRENT_DIR, "knowledge", "clean_history.py")
    if not os.path.exists(script) or not (py and os.path.exists(py)):
        return
    try:
        if os.path.basename(py).lower() == "py.exe":
            cmd = [py, "-3", script]
        else:
            cmd = [py, script]
        subprocess.check_call(cmd, cwd=CURRENT_DIR)
        print("[{}] Cleaned previous versions.".format(copilot_name))
    except:
        pass

def copilot_start():
    py = get_universal_python_path()
    if py:
        install_requirements(py)
        clean_history(py)
    else:
        _safe_print("[SETUP] Skipping requirements installation: no external Python 3 found.")
    _safe_print("=" * 50)
    _safe_print("[{}] Starting {}".format(copilot_name, copilot_name))
    _safe_print("=" * 50)
    purge_osm_temp_dirs()

    start_llm()

    _safe_print("{} ready. Listening to geometry changes on '{}' layer.".format(copilot_name, layer_name))
    start_ui()
    # _safe_print("Interface is now visible.")

if __name__ == "__main__":
    copilot_start()