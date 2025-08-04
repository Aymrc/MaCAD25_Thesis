import http.server
import socketserver
import threading
import webbrowser
import os
import json
import runpy
import Rhino

PORT = 8000
DIRECTORY = "ui"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

pending_tasks = []  # queue of tasks for Rhino to run safely

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    # Always add CORS headers
    def end_headers(self):
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
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "dynamic": True,
                "response": "Connected to Rhino server"
            }).encode("utf-8"))
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
            # 1) Respond immediately to the browser
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "message": "Rhino task queued"
            }).encode("utf-8"))

            # 2) Queue the Rhino task to run OSM23dm.py
            script_path = r"C:\Users\CDH\Documents\GitHub\MaCAD25_Thesis\context2graph\OSM23dm.py"
            pending_tasks.append(lambda: runpy.run_path(script_path, run_name="__main__"))
            return

        super().do_POST()

# Stop previous server if re-run
if "httpd" in globals():
    try:
        print("Stopping previous server...")
        httpd.shutdown()
        httpd.server_close()
    except:
        pass
    httpd = None

# Start HTTP server in background
def start_server():
    global httpd
    with ReusableTCPServer(("", PORT), Handler) as httpd:
        print(f"Serving at http://127.0.0.1:{PORT}")
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
        httpd.serve_forever()

threading.Thread(target=start_server, daemon=True).start()
print("Web server started in background. Rhino is still responsive.")

# Rhino Idle event to process queued tasks safely
def process_tasks(sender, e):
    global pending_tasks
    if not pending_tasks:
        return
    tasks = pending_tasks[:]
    pending_tasks.clear()
    for task in tasks:
        try:
            task()  # Execute the queued task on main thread
        except Exception as ex:
            print("[ERROR] Task failed:", ex)

# Attach Idle event to process tasks
Rhino.RhinoApp.Idle += process_tasks
