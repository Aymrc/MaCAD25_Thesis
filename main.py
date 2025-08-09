# main.py - Rhino-compatible web server script

import http.server
import socketserver
import threading as _th
import webbrowser
import os
import json
import runpy
import Rhino
import requests  # para el proxy


import threading, socket, time, io
from datetime import datetime
from pathlib import Path

try:
    from fastapi import FastAPI, Request, UploadFile, File, Form
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn, requests, PyPDF2
except Exception as e:
    print("[LLM] Falta instalar dependencias FastAPI:", e)
    # Puedes seguir sirviendo la UI aunque falte el LLM
    FastAPI = None

LLM_API_BASE = "http://127.0.0.1:8010"  
LLM_HOST = "127.0.0.1"
LLM_PORT = 8010
LM_STUDIO_URL = "http://127.0.0.1:1234/v1/chat/completions"
COPILOT_NAME = "MASSING"

_llm_server = None
_llm_thread = None
_stored_brief = ""
UPLOAD_FOLDER = Path(os.path.dirname(os.path.abspath(__file__))) / "uploaded_brief"
UPLOAD_FOLDER.mkdir(exist_ok=True)

def _port_in_use(host, port):
    import socket as _s
    s = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()

def start_llm():
    """Arranca FastAPI en segundo plano (si FastAPI está disponible)."""
    global _llm_server, _llm_thread
    if FastAPI is None:
        print("[LLM] FastAPI no disponible. Omitiendo API LLM.")
        return
    if _llm_thread and _llm_thread.is_alive():
        print(f"[LLM] Ya corriendo en http://{LLM_HOST}:{LLM_PORT}")
        return
    if _port_in_use(LLM_HOST, LLM_PORT):
        print(f"[LLM] Puerto ocupado http://{LLM_HOST}:{LLM_PORT}. Asumo que está arriba.")
        return

    # --- FastAPI app ---
    llm_app = FastAPI()
    llm_app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"]
    )

    # Reutiliza TCP/HTTP para menos latencia
    session = requests.Session()

    @llm_app.get("/initial_greeting")
    async def initial_greeting(test: bool = False):
        if test:
            return {"dynamic": True}
        return {"response": f"Hello! I am the copilot {COPILOT_NAME}. What would you like to do?"}

    @llm_app.get("/health")
    async def health():
        """Ping rápido a LM Studio para saber si está listo."""
        try:
            r = session.post(
                LM_STUDIO_URL,
                json={"model": "lmstudio",
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 1, "stream": False, "temperature": 0},
                timeout=3
            )
            return {"ok": r.status_code < 500, "status": r.status_code}
        except Exception:
            return {"ok": False, "status": None}

    @llm_app.post("/chat")
    async def chat(request: Request):
        data = await request.json()
        user_message = data.get("message", "")

        messages = []
        if _stored_brief:
            messages.append({"role": "system",
                             "content": f"Use this project brief as context:\n\n{_stored_brief[:2000]}"})
        messages.append({"role": "user", "content": user_message})

        payload = {
            "messages": messages,
            "stream": False,
            "temperature": 0.7,
            "max_tokens": 512,
            "model": "lmstudio"
        }
        try:
            r = session.post(LM_STUDIO_URL, json=payload, timeout=20)
            r.raise_for_status()
            j = r.json()
            return {"response": j["choices"][0]["message"]["content"]}
        except Exception as e:
            return {"error": str(e), "response": "Failed to reach LM Studio."}

    @llm_app.post("/upload_brief")
    async def upload_brief(file: UploadFile = File(None), text: str = Form(None)):
        global _stored_brief
        if text:
            _stored_brief = text
            return {"status": "ok", "source": "text"}

        if file:
            contents = await file.read()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved = UPLOAD_FOLDER / f"brief_{ts}.pdf"
            with open(saved, "wb") as f:
                f.write(contents)
            try:
                reader = PyPDF2.PdfReader(io.BytesIO(contents))
                brief = "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception:
                brief = ""
            _stored_brief = brief
            return {"status": "ok", "source": "pdf", "filename": str(saved), "length": len(brief)}

        return {"status": "error", "message": "No valid input received."}

    @llm_app.get("/brief")
    async def brief():
        return {"brief": (_stored_brief[:1000] + "...") if _stored_brief else ""}

    # --- Warmup: dispara la 1ª inferencia en segundo plano ---
    import threading as _th
    def _warmup():
        try:
            payload = {
                "model": "lmstudio",
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False, "max_tokens": 5, "temperature": 0
            }
            session.post(LM_STUDIO_URL, json=payload, timeout=8)
            print("[LLM] Warmup done.")
        except Exception as e:
            print("[LLM] Warmup skipped:", e)
    _th.Thread(target=_warmup, daemon=True).start()

    # --- Uvicorn en hilo ---
    config = uvicorn.Config(llm_app, host=LLM_HOST, port=LLM_PORT, log_level="info")
    _llm_server = uvicorn.Server(config)

    def _run():
        print("[LLM] Iniciando FastAPI…")
        _llm_server.run()
        print("[LLM] FastAPI detenido.")

    _llm_thread = threading.Thread(target=_run, daemon=True)
    _llm_thread.start()

    # Espera breve hasta que el puerto responda
    for _ in range(50):
        if _port_in_use(LLM_HOST, LLM_PORT):
            print(f"[LLM] Servidor listo en http://{LLM_HOST}:{LLM_PORT}")
            break
        time.sleep(0.1)

def stop_llm():
    """Detiene FastAPI embebida (si está corriendo)."""
    global _llm_server, _llm_thread
    if not _llm_thread or not _llm_thread.is_alive():
        print("[LLM] No hay LLM server en ejecución.")
        return
    print("[LLM] Deteniendo FastAPI…")
    _llm_server.should_exit = True
    _llm_thread.join(timeout=3.0)
    _llm_server = None
    _llm_thread = None
    print("[LLM] FastAPI detenida.")


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
# --- dentro de Handler ---
    def _proxy_post(self, path, raw_body, content_type="application/json"):
        import requests as _r
        try:
            url = f"http://{LLM_HOST}:{LLM_PORT}{path}"
            r = _r.post(url, data=raw_body, headers={"Content-Type": content_type}, timeout=30)
            self.send_response(r.status_code)
            self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
            self.end_headers()
            self.wfile.write(r.content)
        except Exception as e:
            self._send_json({"error": f"Proxy POST failed: {e}"}, status=502)

    def _proxy_get(self, path):
        import requests as _r
        try:
            url = f"http://{LLM_HOST}:{LLM_PORT}{path}"
            r = _r.get(url, timeout=15)
            self.send_response(r.status_code)
            self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
            self.end_headers()
            self.wfile.write(r.content)
        except Exception as e:
            self._send_json({"error": f"Proxy GET failed: {e}"}, status=502)


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

        # Proxy GET a la API LLM
        if self.path in ("/brief", "/llm/initial_greeting"):
            path = self.path.replace("/llm", "")
            return self._proxy_get(path)

        # servir landing por defecto
        if self.path == "/":
            self.path = "/landing.html"

        return super().do_GET()

    def do_POST(self):
        print(f"[DEBUG] POST: {self.path}")
        content_length = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(content_length) if content_length else b""

        # body completo para lógica; preview solo para log
        body_text = raw_body.decode("utf-8", errors="ignore")
        print(f"[DEBUG] POST body preview: '{body_text[:200]}'")

        # 1) Proxy a LLM
        if self.path in ("/chat", "/upload_brief"):
            return self._proxy_post(
                self.path,
                raw_body,
                self.headers.get("Content-Type", "application/json"),
            )

        # 2) Tarea para Rhino
        if self.path == "/run_context_script":
            return self._handle_context_request(body_text)

        # 3) 404
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"Not found"}')

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
            try:
                webbrowser.open(f"http://127.0.0.1:{PORT}/")
            except:
                pass
            httpd.serve_forever()

    threading.Thread(target=server_thread, daemon=True).start()
    print("[INFO] Web server started in the background.")

# === RHINO ENTRY POINT ===
if __name__ == "__main__" or "RhinoInside" in globals():
    # 1) Arranca FastAPI LLM en :8010 (no bloquea)
    start_llm()
    # 2) Arranca el servidor UI en :8000
    start_web_server()
    # 3) Conecta el procesado de cola al Idle de Rhino
    Rhino.RhinoApp.Idle += process_tasks
    print("[INFO] Copilot listo. UI en http://127.0.0.1:8000 (proxy LLM activo)")