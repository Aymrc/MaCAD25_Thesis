// Visual / Graph orchestration (robust loads + retries)

const API_BASE = "http://localhost:8000";
const CONTEXT_GRAPH_PATH     = `${API_BASE}/graph/context`;
const MASSING_GRAPH_PATH     = `${API_BASE}/graph/massing`;
const MASSING_MTIME_PATH     = `${API_BASE}/graph/massing/mtime`;
const MASTERPLAN_GRAPH_PATH  = `${API_BASE}/graph/masterplan`;
const MASTERPLAN_MTIME_PATH  = `${API_BASE}/graph/masterplan/mtime`;

/** Retry helper for JSON fetches with small backoff. Treats empty graphs as retryable. */
async function fetchJsonWithRetry(url, attempts = 3, delayMs = 800) {
  let lastErr;
  for (let i = 0; i < attempts; i++) {
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();

      // Consider an empty graph as transient (retry)
      const maybeNodes = Array.isArray(j?.nodes) ? j.nodes : [];
      const maybeLinks = Array.isArray(j?.links) ? j.links
                        : Array.isArray(j?.edges) ? j.edges : [];
      if (maybeNodes.length === 0 && maybeLinks.length === 0) {
        throw new Error("Empty graph payload");
      }
      return j;
    } catch (e) {
      lastErr = e;
      if (i < attempts - 1) {
        await new Promise(r => setTimeout(r, delayMs * (1 + i))); // small incremental backoff
      }
    }
  }
  throw lastErr;
}

/** Graph adapter: normalize inputs (drop x/y; map u/v -> source/target; expose both edges/links). */
function adaptGraph(raw) {
  const inNodes = Array.isArray(raw?.nodes) ? raw.nodes : [];
  const inEdges = Array.isArray(raw?.links) ? raw.links
               : Array.isArray(raw?.edges) ? raw.edges
               : [];
  const nodes = inNodes.map(n => { const { x, y, ...rest } = (n || {}); return rest; });

  const edges = inEdges.map(e => {
    if (!e) return null;
    const hasUV = (e.u != null && e.v != null);
    const source = (e.source != null) ? e.source : (hasUV ? e.u : undefined);
    const target = (e.target != null) ? e.target : (hasUV ? e.v : undefined);
    if (source == null || target == null) return null;
    const { u, v, ...rest } = e;
    return { ...rest, source, target };
  }).filter(Boolean);

  return { nodes, edges, links: edges, meta: raw?.meta || {} };
}

// Expose adapter so chat.js can reuse the exact same normalizer.
window.adaptGraph = adaptGraph;

// --- Massing polling state ---
let _massingPoll = null;
let _massingLastMtime = 0;

// --- Masterplan polling state ---
let _mpPoll = null;
let _mpLastMtime = 0;

async function loadMassingGraphOnce() {
  try {
    const data = await fetchJsonWithRetry(MASSING_GRAPH_PATH, 3, 700);
    const adapted = adaptGraph(data);
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
  try {
    const r0 = await fetch(MASSING_MTIME_PATH, { cache: "no-store" });
    const j0 = await r0.json();
    _massingLastMtime = j0?.mtime || 0;
  } catch {}

  _massingPoll = setInterval(async () => {
    try {
      const r = await fetch(MASSING_MTIME_PATH, { cache: "no-store" });
      const { mtime } = await r.json();
      if (mtime && mtime !== _massingLastMtime) {
        _massingLastMtime = mtime;
        await loadMassingGraphOnce();
      }
    } catch {
      // keep polling silently
    }
  }, 2500);
}

function stopMassingPolling() {
  if (_massingPoll) {
    clearInterval(_massingPoll);
    _massingPoll = null;
  }
}

/** Load Context graph once with retries. */
async function loadContextGraphOnce() {
  try {
    const data = await fetchJsonWithRetry(CONTEXT_GRAPH_PATH, 3, 700);
    const adapted = adaptGraph(data);
    if (typeof window.showGraph3DBackground === "function") {
      window.showGraph3DBackground(adapted);
    }
  } catch (e) {
    console.warn("[UI] Could not fetch context graph:", e);
    if (typeof window.clearGraph === "function") window.clearGraph();
  }
}

/** Load Masterplan graph once with retries. */
async function loadMasterplanGraphOnce() {
  try {
    const data = await fetchJsonWithRetry(MASTERPLAN_GRAPH_PATH, 3, 700);
    const adapted = adaptGraph(data);
    if (typeof window.showGraph3DBackground === "function") {
      window.showGraph3DBackground(adapted);
    }
  } catch (e) {
    console.warn("[UI] Could not fetch masterplan graph:", e);
    if (typeof window.clearGraph === "function") window.clearGraph();
  }
}

async function startMasterplanPolling() {
  stopMasterplanPolling();
  try {
    const r0 = await fetch(MASTERPLAN_MTIME_PATH, { cache: "no-store" });
    const j0 = await r0.json();
    _mpLastMtime = j0?.mtime || 0;
  } catch {}

  _mpPoll = setInterval(async () => {
    try {
      const r = await fetch(MASTERPLAN_MTIME_PATH, { cache: "no-store" });
      const { mtime } = await r.json();
      if (mtime && mtime !== _mpLastMtime) {
        _mpLastMtime = mtime;
        await loadMasterplanGraphOnce();
      }
    } catch {
      // keep polling silently
    }
  }, 2500);
}

function stopMasterplanPolling() {
  if (_mpPoll) {
    clearInterval(_mpPoll);
    _mpPoll = null;
  }
}

// === Tab switching (visual) ===
document.querySelectorAll(".tab button").forEach(btn => {
  btn.addEventListener("click", async () => {
    document.querySelectorAll(".tab button").forEach(b => {
      b.classList.remove("active");
      b.setAttribute("aria-selected", "false");
    });
    btn.classList.add("active");
    btn.setAttribute("aria-selected", "true");

    const tab = btn.dataset.tab;

    if (tab === "context") {
      stopMassingPolling();
      stopMasterplanPolling();
      await loadContextGraphOnce();
      return;
    }

    if (tab === "brief") {
      stopMassingPolling();
      stopMasterplanPolling();
      if (window._briefGraph && typeof window.showGraph3DBackground === "function") {
        window.showGraph3DBackground(window._briefGraph);
      } else if (typeof window.clearGraph === "function") {
        window.clearGraph();
      }
      return;
    }

    if (tab === "massing") {
      stopMasterplanPolling();
      await loadMassingGraphOnce();
      startMassingPolling();
      return;
    }

    if (tab === "masterplan") {
      stopMassingPolling();
      await loadMasterplanGraphOnce();
      startMasterplanPolling();
      return;
    }

    // default: clear + stop all pollers
    stopMassingPolling();
    stopMasterplanPolling();
    if (typeof window.clearGraph === "function") window.clearGraph();
  });
});

// === Resizable chat history ===
const chatHistory = document.getElementById("chat-history");
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
    stopMasterplanPolling();
    await loadMassingGraphOnce();
    startMassingPolling();
  } else if (activeTab === "context") {
    stopMassingPolling();
    stopMasterplanPolling();
    await loadContextGraphOnce();
  } else if (activeTab === "masterplan") {
    stopMassingPolling();
    await loadMasterplanGraphOnce();
    startMasterplanPolling();
  } else if (activeTab === "brief") {
    stopMassingPolling();
    stopMasterplanPolling();
    if (window._briefGraph && typeof window.showGraph3DBackground === "function") {
      window.showGraph3DBackground(window._briefGraph);
    } else if (typeof window.clearGraph === "function") {
      window.clearGraph();
    }
  } else {
    stopMassingPolling();
    stopMasterplanPolling();
    if (typeof window.clearGraph === "function") window.clearGraph();
  }
});

// --- cleanup: stop polling when leaving page ---
window.addEventListener("beforeunload", () => {
  stopMassingPolling();
  stopMasterplanPolling();
});
