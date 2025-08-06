from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import requests
import io
import PyPDF2
import os
import uvicorn
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

stored_brief = ""
copilot_name = "To_be_Determined"


# === GREETING endpoint ===
@app.get("/initial_greeting")
async def initial_greeting(test: bool = False):
    if test:
        return { "dynamic": True }
    return { "response": "Hello! I am the copilot {}. What would you like to do?".format(copilot_name)}

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploaded_brief"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# === CHAT endpoint ===
@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    user_message = data.get("message", "")

    messages = []
    if stored_brief:
        messages.append({ "role": "system", "content": f"Use this project brief as context:\n\n{stored_brief[:2000]}" })

    messages.append({ "role": "user", "content": user_message })

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
def run_llm():
    print("\n> Starting the server for LLM access ...\n")
    uvicorn.run("llm.llm:app", host="127.0.0.1", port=8000, reload=True)

# run_llm() # don't run llm.py because it's started by main.py