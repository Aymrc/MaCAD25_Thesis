import os, sys, io, re, json, csv, glob, uuid, shutil, subprocess
import requests, PyPDF2, uvicorn, logging

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

# ---- Project config ----
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import copilot_name

# ----------------------------
# App & CORS
# ----------------------------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Constants / Paths
# ----------------------------
LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"

BASE_DIR = Path(__file__).resolve().parent  # .../llm
PROJECT_DIR = BASE_DIR.parent               # project root
CONTEXT_DIR = PROJECT_DIR / "context"
RUNTIME_DIR = CONTEXT_DIR / "runtime"
KNOWLEDGE_DIR = PROJECT_DIR / "knowledge"
OSM_DIR = KNOWLEDGE_DIR / "osm"
BRIEFS_DIR = KNOWLEDGE_DIR / "briefs"
ENRICHED_FILE = KNOWLEDGE_DIR / "enriched" / "enriched_graph.json" 

for d in (RUNTIME_DIR, OSM_DIR, BRIEFS_DIR):
    os.makedirs(d, exist_ok=True)

# --- Massing graph endpoints ---
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH_PATH = os.path.join(REPO_ROOT, "knowledge", "massing_graph.json")
MASTERPLAN_PATH = os.path.join(REPO_ROOT, "knowledge", "merge", "masterplan_graph.json")

# Serve files at /files/* from knowledge/osm for debugging (optional)
app.mount("/files", StaticFiles(directory=str(OSM_DIR)), name="files")


# In-memory job registry
JOBS: Dict[str, Dict] = {}

# In-memory context for brief
stored_brief: str = ""

# Track last OSM workspace so we can purge it before creating a new one
LAST_JOB_MARK = OSM_DIR / "_last_job.txt"

# ----------------------------
# Small helpers
# ----------------------------
def _python_exe():
    # Use the same interpreter that runs FastAPI
    return sys.executable

def _job_dir(job_id):
    return OSM_DIR / job_id

def _write_json(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _delete_folder(p: Path) -> bool:
    """Best-effort recursive directory delete with rename fallback."""
    try:
        shutil.rmtree(str(p), ignore_errors=True)
        return True
    except Exception:
        try:
            trash = p.parent / (p.name + "_trash")
            os.rename(str(p), str(trash))
            shutil.rmtree(str(trash), ignore_errors=True)
            return True
        except Exception:
            return False

UUID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

def _looks_like_uuid(name: str) -> bool:
    return bool(UUID_RX.match(name))

def _job_candidates() -> list[Path]:
    """All subfolders in OSM_DIR that look like jobs: 'osm_*' or UUID."""
    try:
        items = [p for p in OSM_DIR.iterdir() if p.is_dir()]
    except Exception:
        return []
    out = []
    for p in items:
        n = p.name
        if n.startswith("osm_") or _looks_like_uuid(n):
            out.append(p)
    return out

def _purge_previous_osm_workspace():
    """
    Delete *all* previous job folders in knowledge/osm before creating a new one.
    Removes both 'osm_*' (renamed by Rhino) and UUID-named folders.
    Also cleans up the legacy 'knowledge/osm/_tmp' if it exists.
    """
    try:
        # 1) If the marker points to an existing path, delete it
        if LAST_JOB_MARK.exists():
            prev_txt = LAST_JOB_MARK.read_text(encoding="utf-8").strip()
            if prev_txt:
                prev = Path(prev_txt)
                if prev.exists() and prev.is_dir():
                    _delete_folder(prev)

        # 2) Delete all detected job folders (osm_* or UUID)
        cands = _job_candidates()
        # Sorted by mtime descending only for logging/debugging; all will be deleted anyway
        cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in cands:
            _delete_folder(p)

        # 3) Clean up old temporary folder
        legacy_tmp = OSM_DIR / "_tmp"
        if legacy_tmp.exists() and legacy_tmp.is_dir():
            _delete_folder(legacy_tmp)

    except Exception:
        # Silent: cleanup should not break /osm/run
        pass


# ============================
# GREETING endpoint
# ============================
@app.get("/initial_greeting")
async def initial_greeting(test: bool = False):
    if test:
        return {"dynamic": True}

    sys_msg = (
        "You are a friendly, professional urban design project copilot. "
        f"Your name is {copilot_name}. "
        "Greet the user in ONE short sentence (6–14 words), warm and proactive, "
        "and make it clearly about design work (e.g., masterplan, context, brief, site, or graph). "
        "Output ONLY the sentence—no labels, no instructions, no emojis."
    )

    try:
        res = requests.post(
            LM_STUDIO_URL,
            json={
                "model": "lmstudio",
                "messages": [
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": "Please greet me now."},
                ],
                "temperature": 0.8,
                "top_p": 0.95,
                "max_tokens": 250,
                "stream": False
            },
            timeout=10,
        )
        res.raise_for_status()
        greeting = res.json()["choices"][0]["message"]["content"].strip()

        bad_bits = ("use", "need", "instruction", "one sentence", "at least one", "output only")
        if len(greeting.split()) < 4 or any(b in greeting.lower() for b in bad_bits):
            import random
            fallbacks = [
                f"Let’s dive into your masterplan—I’m {copilot_name}, ready to help.",
                f"Share your site or brief and I’ll start mapping the graph.",
                f"Ready to explore the context and grow your masterplan graph?",
                f"I’m {copilot_name}—shall we sketch the site context and program?",
                f"Drop your brief and I’ll turn it into a project graph.",
                f"Tell me about the site; I’ll outline the masterplan steps.",
            ]
            greeting = random.choice(fallbacks)
    except Exception:
        greeting = f"Let’s dive into your masterplan—I’m {copilot_name}, ready to help."

    return {"response": greeting}

# ============================
# CHAT endpoint
# ============================
@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    user_message = data.get("message", "")

    messages = [
        {"role": "system", "content": """
You are Graph Copilot for an urban design project.

SCOPE & ROLE
- Support across phases: set CITY context; read PROJECT BRIEF; build SEMANTIC graph;
  build TOPOLOGICAL graph from 3D massing; MERGE the two; INSERT into the GLOBAL CITY graph; EVALUATE and advise.
- Stay on project. If asked off-topic, say it’s out of scope.
- Use the project brief and massing graph (json) as context if present.
         
INTERACTION STYLE
- Default to short, human-friendly answers (1–5 bullets or a short paragraph).
- Only produce structured JSON or code when the user asks for it.
- Don’t restate the full brief; surface only what’s needed now.
- If something is missing, ask ONE precise question and stop. Don’t invent data or IDs.

GUARDRAILS
- Do not reveal internal chain-of-thought. Provide final reasoning only.
When helpful, format replies in Markdown (bold, lists, short headings).
        """}
    ]

    # Add brief graph
    if stored_brief:
        messages.append({
            "role": "system",
            "content": f"PROJECT BRIEF (context):\n{stored_brief[:4000]}"
        })
    
    # Add massing graph context if present
    try:
        massing_txt = _massing_context_text()
    except Exception as e:
        print("Error building massing context:", e)
        massing_txt = ""
    
    if massing_txt:
        messages.append({
            "role": "system",
            "content": massing_txt[:6000]
        })
    messages.append({"role": "user", "content": user_message})

    try:
        lmstudio_payload = {
            "model": "lmstudio",
            "messages": messages,
            "stream": False,
            "temperature": 0.3, # concise
            "top_p": 0.9,
            "max_tokens": 500,
            "stop": ["User:", "Assistant:", "System:"],
        }

        res = requests.post(LM_STUDIO_URL, json=lmstudio_payload, timeout=30)
        res.raise_for_status()
        lmstudio_response = res.json()
        assistant_reply = lmstudio_response["choices"][0]["message"]["content"].strip()

        return {"response": assistant_reply}

    except Exception as e:
        return {"error": str(e), "response": "Failed to reach LM Studio."}

# ============================
# BRIEF upload endpoint
# ============================
@app.post("/upload_brief")
async def upload_brief(file: UploadFile = File(None), text: str = Form(None)):
    global stored_brief

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = BRIEFS_DIR / f"brief_{timestamp}"

    # Clean previous briefs (folders + stray PDFs)
    for p in BRIEFS_DIR.glob("brief_*"):
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

    # Save JSON in brief folder
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
    return {"brief": (stored_brief[:1000] + "...") if stored_brief else ""}

# === BRIEF to JSON extraction ===
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
\"\"\"{brief_text}\"\"\""""
    payload = {
        "model": "lmstudio",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 1200,
        "stream": False,
        "stop": ["User:", "Assistant:", "System:"],
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

    # NEW: remove previous workspace before creating the new one
    _purge_previous_osm_workspace()

    job_id = str(uuid.uuid4())
    out_dir = _job_dir(job_id)
    os.makedirs(out_dir, exist_ok=True)

    # Record this as the last job to be purged on next run
    try:
        LAST_JOB_MARK.write_text(str(out_dir), encoding="utf-8")
    except Exception:
        pass

    env = os.environ.copy()
    env["LAT"] = str(lat)
    env["LON"] = str(lon)
    env["RADIUS_KM"] = str(radius_km)
    env["OUT_DIR"] = str(out_dir)

    worker = PROJECT_DIR / "context" / "osm_worker.py"
    if not worker.exists():
        return {"ok": False, "error": f"Worker not found: {worker}"}

    try:
        subprocess.Popen([_python_exe(), str(worker)], cwd=str(PROJECT_DIR), env=env)
        # UI gets status via /osm/status
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
# MASSING graph endpoint
# ============================
@app.get("/graph/context")
def get_context_graph():
    path = KNOWLEDGE_DIR / "osm" / "graph_context.json"
    if not path.exists():
        return JSONResponse({"nodes": [], "edges": [], "meta": {}}, status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    links = data.get("links", data.get("edges", []))
    return {
        "nodes": data.get("nodes", []),
        "links": links,
        "edges": links,
        "meta": data.get("meta", {})
    }


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

        worker = PROJECT_DIR / "evaluation" / "eval_worker.py"
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
UI_STATE_PATH = OSM_DIR / "ui_state.json"

def _read_ui_state():
    if UI_STATE_PATH.exists():
        try:
            with open(UI_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # default state: both off
    return {"context_preview": False, "plot_preview": False}

def _write_ui_state(state):
    UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(UI_STATE_PATH, "w", encoding="utf-8") as f:
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
# Massing graph endpoints
# ============================
def _read_graph():
    if not os.path.exists(GRAPH_PATH):
        return {"nodes": [], "links": [], "edges": [], "meta": {}}
    with open(GRAPH_PATH, "r") as f:
        data = json.load(f)
    links = data.get("links", data.get("edges", []))
    return {
        "nodes": data.get("nodes", []),
        "links": links,
        "edges": links,
        "meta": data.get("meta", {})
    }

@app.get("/graph/massing")
def get_massing_graph():
    return JSONResponse(_read_graph())

@app.get("/graph/massing/mtime")
def get_massing_mtime():
    try:
        return {"mtime": os.path.getmtime(GRAPH_PATH)}
    except:
        return {"mtime": 0.0}

@app.get("/graph/masterplan")
def get_masterplan_graph():
    try:
        if not os.path.exists(MASTERPLAN_PATH):
                        return {"nodes": [], "links": [], "edges": [], "meta": {"missing": True}}
        with open(MASTERPLAN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        links = data.get("links", data.get("edges", []))
        return {
            "nodes": data.get("nodes", []),
            "links": links,
            "edges": links,
            "meta": data.get("meta", {})
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/graph/masterplan/mtime")
def get_masterplan_mtime():
    try:
        return {"mtime": os.path.getmtime(MASTERPLAN_PATH)}
    except:
        return {"mtime": 0.0}

# ---- Massing context condenser ----
def _massing_context_text(max_nodes: int = 200, max_edges: int = 200, include_stats: bool = True) -> str:
    """
    Returns a concise, LLM-friendly text summary of the massing graph, aligned to the actual schema.
    Truncates to avoid blowing the token budget.
    """
    try:
        with open(GRAPH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""

    nodes = data.get("nodes", []) or []
    edges = data.get("links", data.get("edges", [])) or []
    meta  = data.get("meta", {}) or {}

    # ---- Per-building stats (levels are nodes) ----
    # Only treat nodes with type == "level" as floors that belong to a building.
    by_bldg = defaultdict(lambda: {"levels": 0, "total_area": 0.0, "levels_list": []})
    for n in nodes:
        if (n.get("type") == "level") and ("building_id" in n):
            b = by_bldg[n["building_id"]]
            b["levels"] += 1
            b["total_area"] += float(n.get("area", 0.0) or 0.0)
            b["levels_list"].append(int(n.get("level", -1)))

    # Sort buildings alphanumerically for stable output
    building_ids = sorted(by_bldg.keys(), key=lambda x: (str(x)))
    buildings_total = len(building_ids)

    # ---- Light truncation (stable order) ----
    n_show = nodes[:max_nodes]
    e_show = edges[:max_edges]

    # ---- Node lines (aligned with your schema) ----
    node_lines = []
    for n in n_show:
        node_lines.append(
            f"{n.get('id','?')}|{n.get('label','')}|{n.get('building_id','')}|{n.get('level','')}|{n.get('area','')}|{n.get('type','')}"
        )

    # ---- Edge lines ----
    edge_lines = []
    for e in e_show:
        edge_lines.append(
            f"{e.get('source','?')}->{e.get('target','?')}|{e.get('type', e.get('relation',''))}"
        )

    # ---- Building stats lines ----
    stats_lines = []
    if include_stats and buildings_total > 0:
        stats_lines.append(f"buildings_total={buildings_total}")
        stats_lines.append("building_id|levels_count|total_area_sqm|min_level|max_level")
        for bid in building_ids:
            info = by_bldg[bid]
            lvls = sorted(x for x in info["levels_list"] if isinstance(x, int))
            min_lvl = lvls[0] if lvls else ""
            max_lvl = lvls[-1] if lvls else ""
            stats_lines.append(f"{bid}|{info['levels']}|{round(info['total_area'], 2)}|{min_lvl}|{max_lvl}")

    # ---- Meta lines (optional, helpful for floor height) ----
    meta_lines = []
    if meta:
        fh = meta.get("floor_height", "")
        flo = meta.get("floor_levels", [])
        if fh != "":
            meta_lines.append(f"floor_height={fh}")
        if flo:
            meta_lines.append(f"floor_levels={flo}")

    # ---- Guidance note so the LLM counts buildings correctly ----
    guidance = [
        "DATA NOTE: Nodes with type=='level' represent FLOORS, not buildings.",
        "To count buildings, group nodes by 'building_id'. To compute GFA, sum 'area' per building_id.",
    ]

    # ---- Final assembled summary ----
    summary = [
        "MASSING GRAPH SUMMARY (LLM CONTEXT):",
        *guidance,
    ]
    if meta_lines:
        summary += ["META:", *meta_lines]

    summary += [
        f"nodes_total={len(nodes)}, edges_total={len(edges)}",
        "nodes_shown=id|label|building_id|level|area|type",
        *node_lines,
        "edges_shown=source->target|type",
        *edge_lines,
    ]

    if stats_lines:
        summary += ["BUILDING STATS:", *stats_lines]

    return "\n".join(summary)

@app.get("/graph/enriched/latest")
def get_enriched_latest():
    """Serve a single fixed enriched graph file."""
    if not ENRICHED_FILE.exists():
        return JSONResponse({"nodes": [], "edges": [], "meta": {}}, status_code=404)

    with open(ENRICHED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    links = data.get("links", data.get("edges", []))
    return {
        "nodes": data.get("nodes", []),
        "links": links,
        "edges": links,  # keep both keys for the frontend adapter
        "meta": {**data.get("meta", {}), "iteration_file": ENRICHED_FILE.name}
    }

@app.get("/graph/enriched/mtime")
def get_enriched_mtime():
    try:
        return {"mtime": os.path.getmtime(ENRICHED_FILE)}
    except:
        return {"mtime": 0.0}


# ---- Quiet Uvicorn access logs for mtime polling ----
ACCESS_LOG_MUTE_ENDPOINTS = ("/graph/massing/mtime", "/graph/enriched/mtime") # ("/graph/massing/mtime", "...") add whatever we need to clean

def _install_access_log_filter():
    """Silence polluting mtime polling endpoint in Uvicorn access logs"""
    class _MutePolling(logging.Filter):
        def filter(self, record):
            try:
                msg = record.getMessage()
            except Exception:
                return True
            return not any(ep in msg for ep in ACCESS_LOG_MUTE_ENDPOINTS)
    logging.getLogger("uvicorn.access").addFilter(_MutePolling())


# ============================
# Server entry point
# ============================
def run_llm(reload=False):
    _install_access_log_filter()
    print("[LLM] Starting the server for LLM access ...")
    uvicorn.run("llm:app",
                host="127.0.0.1",
                port=8000,
                reload=reload)

if __name__ == "__main__":
    try:
        run_llm(reload=True)
    except Exception as e:
        print("LLM crashed:", e)
        raw_input = input
        raw_input("Press Enter to close...")