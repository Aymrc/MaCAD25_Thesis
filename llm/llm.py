from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import requests
import io
import PyPDF2
import os
import uvicorn
import sys
import uuid
import subprocess
import json
from datetime import datetime
from pathlib import Path
from typing import Dict

# Project name
copilot_name = "MASSING"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
stored_brief = ""

# ----------------------------
# Paths for runtime artifacts
# ----------------------------
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
CONTEXT_DIR = PROJECT_DIR / "1_context"
RUNTIME_DIR = CONTEXT_DIR / "runtime"
OSM_DIR = RUNTIME_DIR / "osm"
UPLOAD_FOLDER = BASE_DIR / "uploaded_brief"
for d in (RUNTIME_DIR, OSM_DIR, UPLOAD_FOLDER):
    os.makedirs(d, exist_ok=True)

# Serve runtime files at /files/*
app.mount("/files", StaticFiles(directory=str(RUNTIME_DIR)), name="files")

# In-memory job registry (simple)
JOBS: Dict[str, Dict] = {}

def _python_exe():
    # Use the same interpreter that runs FastAPI
    return sys.executable

def _job_dir(job_id):
    return OSM_DIR / job_id

def _write_json(path, payload):
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)

def _read_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None

# ============================
# GREETING endpoint
# ============================
@app.get("/initial_greeting")
async def initial_greeting(test: bool = False):
    if test:
        return {"dynamic": True}

    prompt_messages = [
        {
            "role": "system",
            "content": (
                "You are a friendly, professional urban design project copilot. "
                f"Your name is {copilot_name}. "
                "Greet the user naturally and warmly, in one short sentence. "
                "Make the greeting vary each time, avoid repeating the exact same words, "
                "and keep it concise."
            )
        }
    ]

    try:
        res = requests.post(
            LM_STUDIO_URL,
            json={
                "model": "lmstudio",
                "messages": prompt_messages,
                "temperature": 0.9,
                "max_tokens": 30
            },
            timeout=10
        )
        res.raise_for_status()
        greeting = res.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        greeting = f"Hello! I’m {copilot_name}. Ready to start?"

    return {"response": greeting}

# ============================
# CHAT endpoint
# ============================
@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    user_message = data.get("message", "")

    messages = []
    if stored_brief:
        messages.append({"role": "system", "content": "Use this project brief as context:\n\n{}".format(stored_brief[:2000])})
    messages.append({"role": "user", "content": user_message})

    try:
        lmstudio_payload = {
            "messages": messages,
            "stream": False,
            "temperature": 0.7,
            "max_tokens": 512,
            "model": "lmstudio"
        }
        res = requests.post(LM_STUDIO_URL, json=lmstudio_payload)
        lmstudio_response = res.json()
        assistant_reply = lmstudio_response["choices"][0]["message"]["content"]
        return {"response": assistant_reply}
    except Exception as e:
        return {"error": str(e), "response": "Failed to reach LM Studio."}

# ============================
# BRIEF upload endpoint
# ============================
@app.post("/upload_brief")
async def upload_brief(file: UploadFile = File(None), text: str = Form(None)):
    global stored_brief

    if text:
        stored_brief = text
        return {"status": "ok", "source": "text"}

    elif file and file.content_type == "application/pdf":
        contents = await file.read()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_filename = str(UPLOAD_FOLDER / "brief_{}.pdf".format(timestamp))
        with open(saved_filename, "wb") as f:
            f.write(contents)

        reader = PyPDF2.PdfReader(io.BytesIO(contents))
        brief_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        stored_brief = brief_text

        return {
            "status": "ok",
            "source": "pdf",
            "filename": saved_filename,
            "length": len(brief_text)
        }

    return {"status": "error", "message": "No valid input received."}

@app.get("/brief")
async def get_brief():
    return {"brief": stored_brief[:1000] + "..."}

# ============================
# OSM endpoints (silent responses)
# ============================
@app.post("/osm/run")
async def osm_run(payload: dict):
    """
    Launch OSM download worker (Python 3) as a background subprocess.
    Expects: { "lat": float, "lon": float, "radius_km": float }
    Returns: { ok, job_id }
    """
    try:
        lat = float(payload.get("lat"))
        lon = float(payload.get("lon"))
        radius_km = float(payload.get("radius_km"))
    except Exception:
        return {"ok": False, "error": "Invalid lat/lon/radius_km"}

    job_id = str(uuid.uuid4())
    out_dir = OSM_DIR / job_id
    os.makedirs(out_dir, exist_ok=True)

    env = os.environ.copy()
    env["LAT"] = str(lat)
    env["LON"] = str(lon)
    env["RADIUS_KM"] = str(radius_km)
    env["OUT_DIR"] = str(out_dir)

    worker = PROJECT_DIR / "1_context" / "osm_worker.py"
    if not worker.exists():
        return {"ok": False, "error": "Worker not found: {}".format(worker)}

    try:
        subprocess.Popen([_python_exe(), str(worker)], cwd=str(PROJECT_DIR), env=env)
        # No mensajes “narrativos” para la UI: solo status
        return {"ok": True, "job_id": job_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/osm/status/{job_id}")
async def osm_status(job_id: str):
    """
    Minimal status: running/finished/failed and the output folder.
    Works after restarts using filesystem flags.
    """
    info = JOBS.get(job_id)
    out_dir = str(_job_dir(job_id))
    if info is None:
        # Try to recover from filesystem
        job_json = _read_json(Path(out_dir) / "job.json")
        if job_json is None:
            return {"ok": False, "error": "unknown job"}
        info = {"status": "running", "out_dir": out_dir}
        JOBS[job_id] = info

    done_flag = os.path.join(out_dir, "DONE.txt")
    failed_flag = os.path.join(out_dir, "FAILED.txt")

    status = info.get("status", "running")
    if os.path.exists(failed_flag):
        status = "failed"
    elif os.path.exists(done_flag):
        status = "finished"

    info["status"] = status
    return {"ok": True, "status": status, "out_dir": out_dir}

# ============================
# Server entry point
# ============================
def run_llm(reload=False):
    print("[LLM] Starting the server for LLM access ...")
    uvicorn.run("llm:app", host="127.0.0.1", port=8000, reload=reload)

if __name__ == "__main__":
    try:
        run_llm(reload=True)
    except Exception as e:
        print("LLM crashed:", e)
        raw_input = input  # ensure name exists in case of IronPython call
        raw_input("Press Enter to close...")