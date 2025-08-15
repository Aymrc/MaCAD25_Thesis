# -*- coding: utf-8 -*-
# main.py — Rhino-friendly launcher (IronPython 2.7 compatible)
# Starts the backend (LLM) with an external Python 3 and opens the local UI.

import os, sys, subprocess, webbrowser

try:
    from config import layer_name, copilot_name, python_exe_AB, python_exe_CH
except Exception:
    layer_name = "COPILOT"
    copilot_name = "Rhino Copilot"
    python_exe_AB = ""
    python_exe_CH = ""

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
LLM_DIR = os.path.join(CURRENT_DIR, "llm")
RHINO_DIR = os.path.join(CURRENT_DIR, "rhino")
UI_DIR = os.path.join(CURRENT_DIR, "ui")

if LLM_DIR not in sys.path:
    sys.path.append(LLM_DIR)
if RHINO_DIR not in sys.path:
    sys.path.append(RHINO_DIR)

def _safe_print(msg):
    try:
        print(msg)
    except:
        print(str(msg))

def get_universal_python_path():
    username = os.getenv("USERNAME") or ""
    candidates = [
        python_exe_AB,
        python_exe_CH,
        r"C:\Python313\python.exe",
        r"C:\Python312\python.exe",
        r"C:\Python311\python.exe",
        r"C:\Program Files\Python313\python.exe",
        r"C:\Program Files\Python312\python.exe",
        r"C:\Program Files\Python311\python.exe",
        r"C:\Users\%s\AppData\Local\Programs\Python\Python313\python.exe" % username,
        r"C:\Users\%s\AppData\Local\Programs\Python\Python312\python.exe" % username,
        r"C:\Users\%s\AppData\Local\Programs\Python\Python311\python.exe" % username,
        r"C:\Windows\py.exe",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            _safe_print("[%s] Found fallback Python: %s" % (copilot_name, p))
            return p

    if sys.executable and os.path.exists(sys.executable):
        try:
            v = sys.version
        except:
            v = ""
        if "IronPython" not in v:
            _safe_print("Using current Python: %s" % sys.executable)
            return sys.executable

    try:
        import distutils.spawn
        for cand in ("python", "python3", "py"):
            p = distutils.spawn.find_executable(cand)
            if p and os.path.exists(p):
                _safe_print("Found Python in PATH: %s" % p)
                return p
    except Exception as e:
        _safe_print("PATH lookup failed: %s" % e)

    _safe_print("No valid Python interpreter found.")
    return None

def _run_pip_install(python_exe, requirements_path):
    if not os.path.exists(requirements_path):
        _safe_print("[SETUP] requirements.txt not found: %s" % requirements_path)
        return
    try:
        if os.path.basename(python_exe).lower() == "py.exe":
            cmd = [python_exe, "-3", "-m", "pip", "install", "-r", requirements_path]
        else:
            cmd = [python_exe, "-m", "pip", "install", "-r", requirements_path]
        _safe_print("[SETUP] Running: %s" % " ".join(cmd))
        subprocess.check_call(cmd, cwd=CURRENT_DIR)
        _safe_print("[SETUP] Requirements installed.")
    except Exception as e:
        _safe_print("[SETUP] Failed to install requirements: %s" % e)

def install_requirements(python_exe):
    req = os.path.join(CURRENT_DIR, "requirements.txt")
    _run_pip_install(python_exe, req)

def start_llm():
    _safe_print("[LLM] Starting backend...")
    llm_script = os.path.join(LLM_DIR, "llm.py")
    python_exe = get_universal_python_path()
    if not python_exe or not os.path.exists(python_exe):
        _safe_print("[LLM] No valid Python 3 found. Aborting.")
        return
    if not os.path.exists(llm_script):
        _safe_print("[LLM] llm.py not found at: %s" % llm_script)
        return
    try:
        if os.path.basename(python_exe).lower() == "py.exe":
            cmd = [python_exe, "-3", llm_script]
        else:
            cmd = [python_exe, llm_script]
        subprocess.Popen(cmd, cwd=LLM_DIR, creationflags=0)
    except Exception as e:
        _safe_print("[LLM] Failed to launch backend: %s" % e)

def start_ui():
    _safe_print("[UI] Opening interface...")
    ui_path = os.path.join(UI_DIR, "index.html")
    if os.path.exists(ui_path):
        file_url = "file:///" + ui_path.replace("\\", "/")
        _safe_print("[UI] Opening: %s" % file_url)
        try:
            webbrowser.open(file_url)
        except Exception as e:
            _safe_print("[UI] Failed to open browser: %s" % e)
    else:
        _safe_print("[UI] index.html not found at: %s" % ui_path)

def clean_history(py):
    """Run clean_history.py (Python 3) to clear history and OSM temp dirs."""
    script = os.path.join(CURRENT_DIR, "knowledge", "clean_history.py")
    if not os.path.exists(script) or not (py and os.path.exists(py)):
        return
    try:
        if os.path.basename(py).lower() == "py.exe":
            cmd = [py, "-3", script]
        else:
            cmd = [py, script]
        subprocess.check_call(cmd, cwd=CURRENT_DIR)
        print("[%s] Cleaned history and OSM temp dirs." % copilot_name)
    except:
        pass

def copilot_start():
    py = get_universal_python_path()
    if py:
        install_requirements(py)
        clean_history(py)  # <-- cleanup now happens in clean_history.py
    else:
        _safe_print("[SETUP] Skipping requirements installation: no external Python 3 found.")

    _safe_print("=" * 50)
    _safe_print("[%s] Starting %s" % (copilot_name, copilot_name))
    _safe_print("=" * 50)

    start_llm()
    _safe_print("%s ready. Listening to geometry changes on '%s' layer." % (copilot_name, layer_name))
    start_ui()

if __name__ == "__main__":
    copilot_start()
