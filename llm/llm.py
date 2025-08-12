import requests, io, PyPDF2, os, uvicorn, sys, re, glob, json, shutil, csv, uuid, subprocess
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


from datetime import datetime
from pathlib import Path
from typing import Dict

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import copilot_name

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
CONTEXT_DIR = PROJECT_DIR / "context"
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

# In‑memory context for brief
stored_brief = ""

# Files path
BASE_DIR = Path(__file__).resolve().parent # .../llm sub-folder
ROOT_DIR = BASE_DIR.parent # project root
KNOWLEDGE_DIR = ROOT_DIR / "knowledge" # ../knowledge sub-folder
UPLOAD_FOLDER = KNOWLEDGE_DIR / "briefs" # ../knowledge/brief_upload sub-folder
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

# === START SERVER ===
def run_llm(reload=False):
    print("[LLM] Starting the server for LLM access ...\n")
    uvicorn.run(
        "llm:app",
        host="127.0.0.1",
        port=8000,
        reload=reload,
        reload_dirs=[str(BASE_DIR)],
        reload_includes=["*.py"],
    )

# === GREETING endpoint ===
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

# === CHAT endpoint ===
@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    user_message = data.get("message", "")

    messages = [
        {"role": "system", "content": """
        You are Graph Copilot for an urban design project.\n"SCOPE & ROLE\n
        - Support across phases: set CITY context; read PROJECT BRIEF; build SEMANTIC graph;
        build TOPOLOGICAL graph from 3D massing; MERGE the two; INSERT into the GLOBAL CITY graph; EVALUATE and advise.\n
        - Stay on project. If asked off‑topic, say it’s out of scope.\n\n
        INTERACTION STYLE\n
        - Default to short, human‑friendly answers (1–5 bullets or a short paragraph).\n
        - Only produce structured JSON or code when the user asks for it.\n
        - Don’t restate the full brief; surface only what’s needed now.\n
        - If something is missing, ask ONE precise question and stop. Don’t invent data or IDs.\n\n
        GUARDRAILS\n
        - Do not reveal internal chain‑of‑thought. Provide final reasoning only.
        When helpful, format replies in Markdown (bold, lists, short headings).
        """}
    ]

    if stored_brief:
        messages.append({
            "role": "system",
            "content": f"PROJECT BRIEF (context):\n{stored_brief[:4000]}"
        })

    messages.append({"role": "user", "content": user_message})

    try:
        lmstudio_payload = {
            "model": "lmstudio",
            "messages": messages,
            "stream": False,
            "temperature": 0.3, # less blabla
            "max_tokens": 500 # 150 = concise
        }

        res = requests.post(LM_STUDIO_URL, json=lmstudio_payload, timeout=30)
        res.raise_for_status()
        lmstudio_response = res.json()
        assistant_reply = lmstudio_response["choices"][0]["message"]["content"].strip()

        return {"response": assistant_reply}

    except Exception as e:
        return {"error": str(e), "response": "Failed to reach LM Studio."}

# === BRIEF upload endpoint ===
@app.post("/upload_brief")
async def upload_brief(file: UploadFile = File(None), text: str = Form(None)):
    global stored_brief

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = UPLOAD_FOLDER / f"brief_{timestamp}"

    # Clean previous briefs (folders + stray PDFs)
    for p in UPLOAD_FOLDER.glob("brief_*"):
        try:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.suffix.lower() == ".pdf":
                p.unlink()
        except Exception:
            pass

    # Brief to text (from text OR PDF)
    if text:
        stored_brief = text
        source_label = "text"
        original_name = "pasted_text.txt"

    elif file and file.content_type == "application/pdf":
        contents = await file.read()
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = out_dir / "brief.pdf"
        with open(pdf_path, "wb") as f:
            f.write(contents)
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(contents))
            stored_brief = "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception:
            stored_brief = ""
        source_label = "pdf"
        original_name = file.filename

    else:
        return {"status": "error", "message": "No valid input received."}

    # Run brief to graph
    try:
        graph = llm_extract_graph_from_brief(stored_brief)
    except Exception as e:
        return {
            "status": "ok",
            "source": source_label,
            "chat_notice": f"Brief received ({source_label}: {original_name}). Graph extraction failed: {e}",
            "graph": None
        }

    # Save JSON in PDF folder
    out_dir.mkdir(parents=True, exist_ok=True)
    graph_json_path = out_dir / "brief_graph.json"
    with open(graph_json_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)

    # Response
    n = len(graph.get("nodes", []))
    e = len(graph.get("edges", []))
    return {
        "status": "ok",
        "source": source_label,
        "chat_notice": f"Brief received ({source_label}: {original_name}). Graph ready — **{n} nodes**, **{e} edges**.",
        "graph_path": str(graph_json_path),
        "graph": graph
    }

# ---------- Helpers ----------
def extract_project_name(brief_text: str, original_filename: str | None) -> str:
    """
    Try to find a project/masterplan name from the brief content.
    Fallbacks to first plausible title line or the original filename.
    """
    if not brief_text:
        return (os.path.splitext(original_filename)[0] if original_filename else "Untitled Project")

    lines = [l.strip() for l in brief_text.splitlines() if l.strip()]

    label_rx = re.compile(r"^(project\s*name|project|masterplan|title)\s*:\s*(.+)$", re.I)
    for l in lines[:50]:
        m = label_rx.match(l)
        if m:
            return m.group(2).strip().strip("-–:")[:120]

    for l in lines:
        if len(l) <= 80 and not re.search(r"^(page\s*\d+|confidential|draft|version|rev\.?)\b", l, re.I):
            return l.strip(" -–:")[:120]

    return (os.path.splitext(original_filename)[0] if original_filename else "Untitled Project")

@app.get("/brief")
async def get_brief():
    return {"brief": stored_brief[:1000] + "..."}

# === BRIEF to JSON ??????????? ===
def extract_first_json(text: str) -> str | None:
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text, flags=re.I)
    if fenced:
        text = fenced.group(1)
    m = re.search(r"\{[\s\S]*\}", text)
    return m.group(0) if m else None

# === JSON CLEAN ===
def clean_graph_schema(data: dict) -> dict:
    for n in data.get("nodes", []):
        n.setdefault("typology", "")
        n.setdefault("footprint", 0)
        n.setdefault("scale", "")
        n.setdefault("social_weight", 0.5)
    for e in data.get("edges", []):
        e.setdefault("type", "mobility")
        mv = e.get("mode", [])
        if mv is None: e["mode"] = []
        elif isinstance(mv, str): e["mode"] = [mv]
        elif not isinstance(mv, list): e["mode"] = []
    return data

# === BRIEF to GRAPH ===
def llm_extract_graph_from_brief(brief_text: str) -> dict:
    system = (
        "You are an expert urban planner who converts briefs into program graphs. "
        "Return only valid JSON with keys 'nodes' and 'edges'."
    )
    user = f"""
Extract a buildable program graph.

- Nodes: {{id, label, typology∈["residential","commercial","cultural","public_space","recreational","office"], footprint:int, scale∈["small","medium","large"], social_weight:0..1}}
- Include a root/masterplan node; connect top-level programs to it with type "contains".
- Edges: {{source, target, type∈["contains","mobility","adjacent"], mode:list}}
- For "contains" use "mode": [].

Brief:
\"\"\"{brief_text}\"\"\"
"""
    payload = {
        "model": "lmstudio",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": 1200,
        "stream": False
    }
    r = requests.post(LM_STUDIO_URL, json=payload, timeout=60)
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"]
    txt = extract_first_json(raw) or raw
    data = json.loads(txt)
    return clean_graph_schema(data)


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

    worker = PROJECT_DIR / "context" / "osm_worker.py"
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
# EVALUATION endpoint
# ============================
@app.post("/evaluate/run")
async def evaluate_run(payload: dict):
    """
    Launch evaluation worker as a background subprocess.
    Expects: { "job_dir": "<absolute path to job folder>" }
    """
    try:
        job_dir = payload.get("job_dir")
        if not job_dir or not os.path.isdir(job_dir):
            return {"ok": False, "error": "invalid job_dir"}

        worker = PROJECT_DIR / "4_evaluation" / "eval_worker.py"
        if not worker.exists():
            return {"ok": False, "error": f"Worker not found: {worker}"}

        env = os.environ.copy()
        env["JOB_DIR"] = str(job_dir)

        subprocess.Popen([_python_exe(), str(worker)], cwd=str(PROJECT_DIR), env=env)

        return {"ok": True, "message": "Evaluation started.", "job_dir": job_dir}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ============================
# Preview (UI state) endpoints
# ============================
UI_STATE_PATH = RUNTIME_DIR / "ui_state.json"

def _read_ui_state():
    if UI_STATE_PATH.exists():
        try:
            with open(UI_STATE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    # default state: both off
    return {"context_preview": False, "plot_preview": False}

def _write_ui_state(state):
    UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(UI_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

@app.get("/preview/state")
async def get_preview_state():
    return _read_ui_state()

@app.post("/preview/context")
async def set_context_preview(payload: dict):
    enabled = bool(payload.get("enabled", False))
    st = _read_ui_state()
    st["context_preview"] = enabled
    _write_ui_state(st)
    return {"ok": True, "context_preview": enabled}

@app.post("/preview/plot")
async def set_plot_preview(payload: dict):
    enabled = bool(payload.get("enabled", False))
    st = _read_ui_state()
    st["plot_preview"] = enabled
    _write_ui_state(st)
    return {"ok": True, "plot_preview": enabled}

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
