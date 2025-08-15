// Visual 

// --- API base (same as chat.js) ---
const API_BASE = "http://localhost:8000";
const CONTEXT_GRAPH_PATH = `${API_BASE}/graph/context`;


// --- Graph adapter (rules) ---

function adaptGraph(raw) {
  // Normalize input containers
  const inNodes = Array.isArray(raw?.nodes) ? raw.nodes : [];
  const inEdges = Array.isArray(raw?.links) ? raw.links
                 : Array.isArray(raw?.edges) ? raw.edges
                 : [];
  // Rule 1: drop x/y from nodes if present
  // Nodes: explicitly strip x/y to avoid interfering with ForceGraph3D internals
  const nodes = inNodes.map(n => {
    const { x, y, ...rest } = (n || {});
    return rest;
  });

  // Rule 2: map u/v -> source/target on edges, without duplicating keys
  // Edges: rename u/v to source/target if those are missing
  const edges = inEdges
    .map(e => {
      if (!e) return null;
      const hasUV = (e.u != null && e.v != null);
      const source = (e.source != null) ? e.source : (hasUV ? e.u : undefined);
      const target = (e.target != null) ? e.target : (hasUV ? e.v : undefined);
      if (source == null || target == null) return null;

      // Remove u/v to avoid ambiguity and keep a clean shape
      const { u, v, ...rest } = e;
      return { ...rest, source, target };
    })
    .filter(Boolean);

  return {
    nodes,
    edges,
    links: edges,           // keep both keys for downstream compatibility
    meta: raw?.meta || {}
  };
}


// --- Massing polling state ---
let _massingPoll = null;
let _massingLastMtime = 0;

async function loadMassingGraphOnce() {
  try {
    const res = await fetch(`${API_BASE}/graph/massing`, { cache: "no-store" });
    const data = await res.json();
    const adapted = adaptGraph(data); // ← apply rules
    if (typeof window.showGraph3DBackground === "function") {
      window.showGraph3DBackground(adapted);
    }
  } catch (e) {
    console.warn("[UI] Could not fetch massing graph:", e);
    if (typeof window.clearGraph === "function") window.clearGraph();
  }
}

async function startMassingPolling() {
  stopMassingPolling();
  // seed mtime to current state to avoid double fetch
  try {
    const r0 = await fetch(`${API_BASE}/graph/massing/mtime`, { cache: "no-store" });
    const j0 = await r0.json();
    _massingLastMtime = j0?.mtime || 0;
  } catch {}

  _massingPoll = setInterval(async () => {
    try {
      const r = await fetch(`${API_BASE}/graph/massing/mtime`, { cache: "no-store" });
      const { mtime } = await r.json();
      if (mtime && mtime !== _massingLastMtime) {
        _massingLastMtime = mtime;
        await loadMassingGraphOnce();
      }
    } catch {
      // silent; keep polling
    }
  }, 2500);
}

function stopMassingPolling() {
  if (_massingPoll) {
    clearInterval(_massingPoll);
    _massingPoll = null;
  }
}

/* === Context Graph loader === */
async function loadContextGraphOnce() {
  try {
    const res = await fetch(CONTEXT_GRAPH_PATH, { cache: "no-store" });
    const data = await res.json();
    const adapted = adaptGraph(data); // ← apply rules
    if (typeof window.showGraph3DBackground === "function") {
      window.showGraph3DBackground(adapted);
    }
  } catch (e) {
    console.warn("[UI] Could not fetch context graph:", e);
    if (typeof window.clearGraph === "function") window.clearGraph();
  }
}

// === Tab switching (visual) ===
document.querySelectorAll(".tab button").forEach(btn => {
  btn.addEventListener("click", async () => {
    document.querySelectorAll(".tab button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");

    const tab = btn.dataset.tab;

    if (tab === "context") {
      // show static context graph from knowledge/osm/graph_context.json
      stopMassingPolling();
      await loadContextGraphOnce();
      return;
    }

    if (tab === "brief") {
      stopMassingPolling();
      // show brief graph if we have one
      if (window._briefGraph && typeof window.showGraph3DBackground === "function") {
        window.showGraph3DBackground(window._briefGraph);
      } else if (typeof window.clearGraph === "function") {
        window.clearGraph();
      }
      return;
    }

    if (tab === "massing") {
      await loadMassingGraphOnce();
      startMassingPolling();
      return;
    }

    // masterplan or others: clear + stop any massing poll
    stopMassingPolling();
    if (typeof window.clearGraph === "function") window.clearGraph(); // <<<<<<<<< to update with masterplan graph later 
  });
});


// === Resizable chat history ===
const chatHistory = document.getElementById("chat-history");
const historyContent = chatHistory?.querySelector(".history-content");
let isResizing = false;
let startY = 0;
let startHeight = 0;

const resizeHandle = document.querySelector(".chat-resize-handle");
if (resizeHandle && chatHistory) {
  resizeHandle.addEventListener("mousedown", e => {
    isResizing = true;
    startY = e.clientY;
    startHeight = chatHistory.offsetHeight;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "ns-resize";
  });

  document.addEventListener("mousemove", e => {
    if (!isResizing) return;
    const dy = e.clientY - startY;

    // clamp: min 60px, max 50vh
    const maxH = Math.round(window.innerHeight * 0.5);
    const next = Math.max(60, Math.min(maxH, startHeight - dy));
    chatHistory.style.height = `${next}px`;
  });

  document.addEventListener("mouseup", () => {
    isResizing = false;
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
  });
}

// --- boot: load the correct tab state on first paint ---
document.addEventListener("DOMContentLoaded", async () => {
  const activeBtn = document.querySelector('.tab button.active');
  const activeTab = activeBtn?.dataset?.tab;

  if (activeTab === "massing") {
    await loadMassingGraphOnce();
    startMassingPolling();
  } else if (activeTab === "context") {
    // initial load if Context tab is active by default
    await loadContextGraphOnce();
  } else if (activeTab === "brief") {
    if (window._briefGraph && typeof window.showGraph3DBackground === "function") {
      window.showGraph3DBackground(window._briefGraph);
    } else if (typeof window.clearGraph === "function") {
      window.clearGraph();
    }
  } else {
    // anything else → clear and make sure no stray polling runs
    stopMassingPolling();
    if (typeof window.clearGraph === "function") window.clearGraph();
  }
});

// --- cleanup: stop polling when leaving page ---
window.addEventListener("beforeunload", () => {
  stopMassingPolling();
});
