from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import requests, io, PyPDF2, os, uvicorn, sys, re, glob
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
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploaded_brief"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
UPLOAD_PATTERN = "brief_*.pdf"

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
            "max_tokens": 300 # concise
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

    # if input = TEXT
    if text:
        stored_brief = text
        project_name = extract_project_name(text, None)
        return {
            "status": "ok",
            "source": "text",
            "project_name": project_name,
            "chat_notice": f"Brief received for **{project_name}** (via text)."
        }

    # if input = PDF
    elif file and file.content_type == "application/pdf":
        contents = await file.read()

        # Remove previous brief history
        try:
            for old in glob.glob(str(UPLOAD_FOLDER / UPLOAD_PATTERN)):
                try:
                    os.remove(old)
                    print("Old briefs cleared.")
                except Exception:
                    pass
        except Exception:
            pass

        # Save PDF
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_filename = UPLOAD_FOLDER / f"brief_{timestamp}.pdf"
        with open(saved_filename, "wb") as f:
            f.write(contents)

        # text to memory
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(contents))
            brief_text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            brief_text = ""

        stored_brief = brief_text or ""
        project_name = extract_project_name(stored_brief, file.filename)

        return {
            "status": "ok",
            "source": "pdf",
            "filename": str(saved_filename),
            "length": len(stored_brief),
            "project_name": project_name,
            "chat_notice": f"Brief received for **{project_name}** (PDF: {file.filename})."
        }

    return {"status": "error", "message": "No valid input received."}

# === BRIEF preview ===
@app.get("/brief")
async def get_brief():
    text = (stored_brief or "")
    return {"brief": (text[:1000] + "...") if len(text) > 1000 else text}

# === START SERVER ===
def run_llm(reload=False):
    print("[LLM] Starting the server for LLM access ...\n")
    uvicorn.run("llm:app", host="127.0.0.1", port=8000, reload=reload)

if __name__ == "__main__":
    try:
        run_llm(reload=True)
    except Exception as e:
        print("LLM crashed:", e)
        input("Press Enter to close...")
