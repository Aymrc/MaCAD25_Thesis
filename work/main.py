from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import requests
import io
import PyPDF2
import os
from datetime import datetime
from pathlib import Path

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"

# === GLOBAL VARIABLE (must be declared early) ===
stored_brief = ""

# === GREETING endpoint ===
@app.get("/initial_greeting")
async def initial_greeting(test: bool = False):
    if test:
        return { "dynamic": True }
    return { "response": "Hello! I'm your Copilot. What would you like to do?" }

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploaded_briefs"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# === CHAT endpoint ===
@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    user_message = data.get("message", "")

    messages = []
    if stored_brief:
        messages.append({ "role": "system", "content": f"""
Use the project brief as context for your interactions:\n\n{stored_brief[:2000]}

You are Graph Copilot for an urban design project.

SCOPE & ROLE
- Support the user through all phases: (1) set CITY context, (2) read PROJECT BRIEF, (3) build SEMANTIC graph from the brief, (4) build TOPOLOGICAL graph from 3D massing, (5) MERGE the two, (6) INSERT into the GLOBAL CITY graph, (7) EVALUATE and advise.
- Stay on project. If asked off‑topic, say it’s out of scope.

INTERACTION STYLE
- Default to short, human‑friendly answers (1–5 bullets or a short paragraph).
- Only produce structured JSON or code when the user asks for it (e.g., “give JSON”, “export”, “schema”, “patch”), or when a tool obviously needs it.
- Don’t restate the full brief; surface only the parts needed for the current step.
- If something is missing, ask ONE precise question and stop. Don’t invent data or IDs.

GRAPH PRINCIPLES
- Use the provided ontology/schemas. If none provided, propose a minimal, consistent set and proceed.
- Be explicit about assumptions and data provenance (brief/semantic/topo/city/global).
- When merging graphs, align by labels/refs, resolve conflicts, and note uncertainties.

EVALUATION & ADVICE
- When evaluating, focus on the KPIs or constraints the user mentions (e.g., FAR, height, access, program mix, carbon/cost).
- Offer clear next steps or trade‑offs. If uncertainty > 20%, state it and what would reduce it.

GUARDRAILS
- Do not reveal internal chain‑of‑thought. Provide final reasoning only.
- If data is insufficient, say so and request the minimal additional input.


    """ })

    messages.append({ "role": "user", "content": user_message })

    try:
        lmstudio_payload = {
            "messages": messages,
            "stream": False,
            "temperature": 0.5,
            "max_tokens": 250,
            "model": "lmstudio"
        }

        res = requests.post(LM_STUDIO_URL, json=lmstudio_payload)
        lmstudio_response = res.json()
        assistant_reply = lmstudio_response["choices"][0]["message"]["content"]

        return { "response": assistant_reply }

    except Exception as e:
        return { "error": str(e), "response": "Failed to reach LM Studio." }


# === BRIEF upload endpoint ===
@app.post("/upload_brief")
async def upload_brief(file: UploadFile = File(None), text: str = Form(None)):
    global stored_brief

    if text:
        stored_brief = text
        return { "status": "ok", "source": "text" }

    elif file and file.content_type == "application/pdf":
        contents = await file.read()

        # Save to disk with timestamped name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_filename = f"{UPLOAD_FOLDER}/brief_{timestamp}.pdf"
        with open(saved_filename, "wb") as f:
            f.write(contents)

        # Extract text to memory for context
        reader = PyPDF2.PdfReader(io.BytesIO(contents))
        brief_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        stored_brief = brief_text

        return {
            "status": "ok",
            "source": "pdf",
            "filename": saved_filename,
            "length": len(brief_text)
        }

    return { "status": "error", "message": "No valid input received." }

# === BRIEF preview ===
@app.get("/brief")
async def get_brief():
    return { "brief": stored_brief[:1000] + "..." }

# === START SERVER ===
if __name__ == "__main__":
    import uvicorn
    print("\n> Starting the server for LLM access ...\n")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
