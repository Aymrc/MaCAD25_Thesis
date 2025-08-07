# main.py - Rhino-compatible web server script

import http.server
import socketserver
import threading
import webbrowser
import os
import json
import runpy
import Rhino

# === CONFIGURATION ===
PORT = 8000
DIRECTORY = "ui"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

pending_tasks = []  # Task queue for Rhino-safe execution

# === SERVER CLASSES ===

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # Add CORS headers
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        print(f"[DEBUG] GET: {self.path}")
        if self.path.startswith("/initial_greeting"):
            self._send_json({
                "dynamic": True,
                "response": "Connected to Rhino server"
            })
            return

        if self.path == "/":
            self.path = "/landing.html"

        return super().do_GET()

    def do_POST(self):
        print(f"[DEBUG] POST: {self.path}")
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length else ""
        print(f"[DEBUG] POST body: '{body}'")

        if self.path == "/run_context_script":
            self._handle_context_request(body)
            return

        super().do_POST()

    def _send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _handle_context_request(self, body):
        try:
            data = json.loads(body)
            lat = float(data.get("lat", 41.3874))
            lon = float(data.get("long", 2.1686))
            radius = float(data.get("radius", 0.5))
        except Exception as e:
            print("[ERROR] Failed to parse JSON:", e)
            lat, lon, radius = 41.3874, 2.1686, 0.5

        # Respond immediately to the frontend
        self._send_json({
            "status": "ok",
            "message": f"Rhino task queued for {lat},{lon} with radius {radius} km"
        })

        # Queue Rhino task for main-thread execution
        script_path = r"C:\Users\CDH\Documents\GitHub\MaCAD25_Thesis\context2graph\OSM23dm.py"

        def run_task():
            os.environ["LAT"] = str(lat)
            os.environ["LON"] = str(lon)
            os.environ["RADIUS_KM"] = str(radius)
            runpy.run_path(script_path, run_name="__main__")

        pending_tasks.append(run_task)

# === RHINO TASK EXECUTION ===

def process_tasks(sender, e):
    global pending_tasks
    if not pending_tasks:
        return

    tasks = pending_tasks[:]
    pending_tasks.clear()

    for task in tasks:
        try:
            task()
        except Exception as ex:
            print("[ERROR] Task execution failed:", ex)

# === START WEB SERVER ===

def start_web_server():
    global httpd

    if "httpd" in globals() and httpd:
        try:
            print("[INFO] Stopping previous server...")
            httpd.shutdown()
            httpd.server_close()
        except:
            pass
        httpd = None

    def server_thread():
        global httpd
        with ReusableTCPServer(("", PORT), Handler) as httpd:
            print(f"[INFO] Serving at http://127.0.0.1:{PORT}")
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
            httpd.serve_forever()

    threading.Thread(target=server_thread, daemon=True).start()
    print("[INFO] Web server started in the background.")

# === RHINO ENTRY POINT ===

if __name__ == "__main__" or "RhinoInside" in globals():
    start_web_server()
    Rhino.RhinoApp.Idle += process_tasks
    print("[INFO] Copilot ready. Waiting for tasks from the web interface.")
