# -*- coding: utf-8 -*-
# main.py — Launcher compatible with IronPython 2.7 (Rhino)
# Starts the backend (LLM) with an external Python 3 and opens the local UI.

# Rhino button:
# ! _-RunPythonScript "C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis\main.py"
# _-RunPythonScript "C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis\rhino\rhino_listener.py"

import os, sys, subprocess, webbrowser

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

# Ensure local imports
if LLM_DIR not in sys.path:
    sys.path.append(LLM_DIR)
if RHINO_DIR not in sys.path:
    sys.path.append(RHINO_DIR)

def _safe_print(msg):
    try:
        print(msg)
    except:
        # IronPython in some environments may fail with unicode: force str
        print(str(msg))

def get_universal_python_path():
    """
    Find a Python 3 executable to launch the backend.
    - If Rhino is running with IronPython, this will be an external executable.
    - Avoid using shutil.which (not available in IronPython 2.7).
    """

    # 1) Known paths (includes those from config.py if set)
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
            _safe_print("Found fallback Python: {}".format(path))
            return path
        
    # 2) If sys.executable points to something valid and is NOT IronPython, use it.
    if sys.executable and os.path.exists(sys.executable):
        v = ""
        try:
            v = sys.version
        except:
            v = ""
        if "IronPython" not in v:
            _safe_print("Using current Python: {}".format(sys.executable))
            return sys.executable

    # 3) Search in PATH using distutils.spawn (available in IronPython 2.7)
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
        _safe_print("[LLM] Backend launched at http://127.0.0.1:8000")
    except Exception as e:
        _safe_print("[LLM] Failed to launch backend: {}".format(e))

def start_ui():
    _safe_print("Opening interface...")
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

def copilot_start():
    py = get_universal_python_path()
    if py:
        install_requirements(py)
    else:
        _safe_print("[SETUP] Skipping requirements installation: no external Python 3 found.")

    _safe_print("=" * 50)
    _safe_print("Starting '{}'".format(copilot_name))
    _safe_print("=" * 50)

    start_llm()

    _safe_print("Copilot ready. Listening to geometry changes on '{}' layer.".format(layer_name))
    start_ui()
    _safe_print("Interface is now visible.")

if __name__ == "__main__":
    copilot_start()