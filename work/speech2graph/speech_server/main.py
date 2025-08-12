# speech_server/main.py
# pip install fastapi uvicorn websockets faster-whisper numpy
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from faster_whisper import WhisperModel
import numpy as np
import json
import re

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5500", "http://127.0.0.1:5500", "*"],  # adjust if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load a small local model (good speed on CPU). Try "small.en" if needed.
model = WhisperModel("base.en", compute_type="int8")  # local, fast-ish

# In-memory graph state (very basic)
current_graph = {"nodes": [], "edges": []}

def ensure_node(node_id, label=None):
    if not any(n["id"] == node_id for n in current_graph["nodes"]):
        current_graph["nodes"].append({"id": node_id, "label": label or node_id})
        return {"nodes": [{"id": node_id, "label": label or node_id}]}
    return {"nodes": []}

def add_edge(a, b):
    if not any((e["source"] == a and e["target"] == b) or (e["source"] == b and e["target"] == a) for e in current_graph["edges"]):
        current_graph["edges"].append({"source": a, "target": b})
        return {"edges": [{"source": a, "target": b}]}
    return {"edges": []}

def remove_node(node_id):
    before_n = len(current_graph["nodes"])
    before_e = len(current_graph["edges"])
    current_graph["nodes"] = [n for n in current_graph["nodes"] if n["id"] != node_id]
    current_graph["edges"] = [e for e in current_graph["edges"] if e["source"] != node_id and e["target"] != node_id]
    return {
        "remove": {
            "nodes": [{"id": node_id}] if len(current_graph["nodes"]) != before_n else [],
            "edges": [],  # for simplicity we won’t enumerate removed edges
        }
    }

def parse_command(text: str):
    """
    Very simple parser for demo:
      "add node housing"
      "connect housing to retail"
      "remove node housing"
    Return a graph patch to apply client-side.
    """
    t = text.strip().lower()

    m = re.match(r"add node (.+)$", t)
    if m:
        nid = m.group(1).strip().replace(" ", "_")
        patch = ensure_node(nid, label=m.group(1).strip())
        return {"action": "add_node", "patch": patch}

    m = re.match(r"connect (.+) to (.+)$", t)
    if m:
        a = m.group(1).strip().replace(" ", "_")
        b = m.group(2).strip().replace(" ", "_")
        patch_nodes = {}
        # ensure both nodes exist first
        pn1 = ensure_node(a, label=m.group(1).strip())
        pn2 = ensure_node(b, label=m.group(2).strip())
        # add edge
        pe = add_edge(a, b)
        # merge node patches
        nodes = (pn1.get("nodes", []) or []) + (pn2.get("nodes", []) or [])
        patch_nodes["nodes"] = nodes
        patch_nodes["edges"] = pe.get("edges", [])
        return {"action": "connect", "patch": patch_nodes}

    m = re.match(r"remove node (.+)$", t)
    if m:
        nid = m.group(1).strip().replace(" ", "_")
        return {"action": "remove_node", "patch": remove_node(nid)}

    # No-op fallback
    return {"action": "none", "patch": {}}

@app.websocket("/stt")
async def stt(ws: WebSocket):
    await ws.accept()
    buf = np.empty(0, dtype=np.int16)

    try:
        while True:
            data = await ws.receive_bytes()
            chunk = np.frombuffer(data, dtype=np.int16)
            if chunk.size == 0:
                continue
            buf = np.concatenate([buf, chunk])

            # Process every ~2 seconds of audio (16000 Hz mono)
            if buf.size >= 16000 * 2:
                audio = buf.astype(np.float32) / 32768.0
                segments, info = model.transcribe(audio, language="en", vad_filter=True)
                text = " ".join(seg.text.strip() for seg in segments).strip()

                if text:
                    # send partial transcript (you can show this live if you want)
                    await ws.send_text(json.dumps({"type": "partial", "text": text}))

                    # treat this chunk as final for now
                    cmd = parse_command(text)
                    await ws.send_text(json.dumps({
                        "type": "final",
                        "text": text,
                        "cmd": cmd["action"],
                        "patch": cmd["patch"]
                    }))

                # reset buffer
                buf = np.empty(0, dtype=np.int16)
    except Exception:
        # client disconnected or closed
        return
