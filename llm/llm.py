import requests, io, PyPDF2, os, uvicorn, sys, re, glob, json, shutil, csv
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from pathlib import Path

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

# === BRIEF preview ===
@app.get("/brief")
async def get_brief():
    text = (stored_brief or "")
    return {"brief": (text[:1000] + "...") if len(text) > 1000 else text}

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



if __name__ == "__main__":
    try:
        run_llm(reload=True)
    except Exception as e:
        print("LLM crashed:", e)
        input("Press Enter to close...")
