# This is the main function of the copilot
# It starts the copilot back and front-end

# Rhino button:
# ! _-RunPythonScript "C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis\main.py"
# _-RunPythonScript "C:\Users\broue\Documents\IAAC MaCAD\Master_Thesis\MaCAD25_Thesis\rhino\rhino_listener.py"

# r"C:\Users\broue\AppData\Local\Programs\Python\Python310\python.exe" # Adjust path
# r"C:\Program Files\Rhino 8\System\Rhino.exe" # Adjust path 

import os
import sys
import subprocess
import rhino_listener
import distutils.spawn

from config import layer_name, copilot_name, python_exe_AB, python_exe_CH # project variables

# === Setup paths ===
current_dir = os.path.dirname(os.path.abspath(__file__))
llm_dir = os.path.join(current_dir, "llm")
rhino_dir = os.path.join(current_dir, "rhino")

sys.path.append(llm_dir)
sys.path.append(rhino_dir)


def get_universal_python_path():
    if sys.executable and os.path.exists(sys.executable):
        print("Using current Python: {}".format(sys.executable))
        return sys.executable

    # Fallback for IronPython (no shutil.which)
    try:
        import distutils.spawn
        for candidate in ['python', 'python3']:
            path = distutils.spawn.find_executable(candidate)
            if path and os.path.exists(path):
                print("Found Python in PATH: {}".format(path))
                return path
    except Exception as e:
        print("Fallback path lookup failed:", e)

    possible_paths = [
        python_exe_AB,
        python_exe_CH,
        r"C:\Python312\python.exe",
        r"C:\Users\{}\AppData\Local\Programs\Python\Python312\python.exe".format(os.getenv("USERNAME")),
        r"C:\Program Files\Python312\python.exe",
    ]
    for path in possible_paths:
        if os.path.exists(path):
            print("Found fallback Python: {}".format(path))
            return path

    print("No valid Python interpreter found.")
    return None

def install_requirements(python_exe):
    try:
        subprocess.check_call([python_exe, "-m", "pip", "install", "-r", "requirements.txt"])
        print("[SETUP] Requirements installed.")
    except Exception as e:
        print("[SETUP] Failed to install requirements:", e)

def start_llm():
    print("Starting LLM FastAPI backend...")
    llm_script = os.path.join(llm_dir, "llm.py")
    python_exe = get_universal_python_path()

    # print("LLM path: {}".format(llm_script))
    # print("Python path: {}".format(python_exe))

    if not os.path.exists(python_exe):
        print("Python executable not found. Aborting.")
        return

    try:
        subprocess.Popen(
            [python_exe, llm_script],
            cwd=llm_dir,
            creationflags=0 # hidden window: subprocess.CREATE_NO_WINDOW
        )
        print("LLM backend launched at http://127.0.0.1:8000")
    except Exception as e:
        print("Failed to launch LLM backend:", e)

def start_rhino_listener():
    print("Starting layer-based Rhino listener...")
    rhino_listener.setup_layer_listener()

def copilot_start():
    install_requirements(get_universal_python_path())
    print("=" * 50)
    print("Starting Rhino Copilot '{}'".format(copilot_name))
    print("=" * 50)
    start_llm()
    start_rhino_listener()
    print("Copilot ready. Listening to geometry changes on {} layer.".format(layer_name))

copilot_start()
