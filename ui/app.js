// Visual / Graph orchestration (robust loads + retries)

const API_BASE = "http://localhost:8000";
const CONTEXT_GRAPH_PATH     = `${API_BASE}/graph/context`;
const MASSING_GRAPH_PATH     = `${API_BASE}/graph/massing`;
const MASSING_MTIME_PATH     = `${API_BASE}/graph/massing/mtime`;
const MASTERPLAN_GRAPH_PATH  = `${API_BASE}/graph/masterplan`;
const MASTERPLAN_MTIME_PATH  = `${API_BASE}/graph/masterplan/mtime`;
const ENRICHED_LATEST_PATH   = `${API_BASE}/graph/knowledge/iteration/latest`;

/** Retry helper for JSON fetches with small backoff.
    Pass {allowEmpty:true} when an empty graph should NOT be treated as an error. */
async function fetchJsonWithRetry(url, attempts = 3, delayMs = 800, { allowEmpty = false } = {}) {
  let lastErr;
  for (let i = 0; i < attempts; i++) {
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();

      const maybeNodes = Array.isArray(j?.nodes) ? j.nodes : [];
      const maybeLinks = Array.isArray(j?.links) ? j.links
                        : Array.isArray(j?.edges) ? j.edges : [];
      if (!allowEmpty && maybeNodes.length === 0 && maybeLinks.length === 0) {
        throw new Error("Empty graph payload");
      }
      return j;
    } catch (e) {
      lastErr = e;
      if (i < attempts - 1) {
        await new Promise(r => setTimeout(r, delayMs * (1 + i)));
      }
    }
  }
  throw lastErr;
}

/** Graph adapter: drop x/y; map u/v -> source/target; expose edges/links. */
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

window.adaptGraph = adaptGraph;

// -------- Massing --------
let _massingPoll = null;
let _massingLastMtime = 0;

async function loadMassingGraphOnce() {
  try {
    // allowEmpty: true => an empty massing file is valid, not an error
    const data = await fetchJsonWithRetry(MASSING_GRAPH_PATH, 3, 700, { allowEmpty: true });
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
      /* keep polling silently */
    }
  }, 2500);
}

function stopMassingPolling() {
  if (_massingPoll) { clearInterval(_massingPoll); _massingPoll = null; }
}

// -------- Context --------
async function loadContextGraphOnce() {
  try {
    // Gracefully handle 404 (means you don’t have a context graph yet)
    const r = await fetch(CONTEXT_GRAPH_PATH, { cache: "no-store" });
    if (r.status === 404) {
      if (typeof window.clearGraph === "function") window.clearGraph();
      return;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);

    const data = await r.json();
    const adapted = adaptGraph(data);
    if (typeof window.showGraph3DBackground === "function") {
      window.showGraph3DBackground(adapted);
    }
  } catch (e) {
    console.warn("[UI] Could not fetch context graph:", e);
    if (typeof window.clearGraph === "function") window.clearGraph();
  }
}

// -------- Enriched (latest only) --------
let _enrichedPoll = null;
let _enrichedLastTag = null;

async function findLatestEnrichedCandidate() {
  const j = await fetchJsonWithRetry(ENRICHED_LATEST_PATH, 2, 600);
  const nodes = Array.isArray(j?.nodes) ? j.nodes : [];
  const links = Array.isArray(j?.links) ? j.links : Array.isArray(j?.edges) ? j.edges : [];
  const tag = `api:${nodes.length}:${links.length}:${j?.meta?.iteration_file ?? ""}`;
  return { source: "api", data: j, tag };
}

async function loadEnrichedGraphOnce() {
  const cand = await findLatestEnrichedCandidate();
  const adapted = adaptGraph(cand.data);
  if (typeof window.showGraph3DBackground === "function") {
    window.showGraph3DBackground(adapted);
  }
  _enrichedLastTag = cand.tag;
}

function stopEnrichedPolling() {
  if (_enrichedPoll) { clearInterval(_enrichedPoll); _enrichedPoll = null; }
}

async function startEnrichedPolling() {
  stopEnrichedPolling();
  try {
    await loadEnrichedGraphOnce();
  } catch (e) {
    console.warn("[UI] Could not fetch enriched graph:", e);
    if (typeof window.clearGraph === "function") window.clearGraph();
  }
  _enrichedPoll = setInterval(async () => {
    try {
      const cand = await findLatestEnrichedCandidate();
      if (cand.tag !== _enrichedLastTag) {
        const adapted = adaptGraph(cand.data);
        if (typeof window.showGraph3DBackground === "function") {
          window.showGraph3DBackground(adapted);
        }
        _enrichedLastTag = cand.tag;
      }
    } catch { /* silent */ }
  }, 3000);
}

// -------- Masterplan --------
let _mpPoll = null;
let _mpLastMtime = 0;

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
    // toggle active state
    document.querySelectorAll(".tab button").forEach(b => {
      b.classList.remove("active");
      b.setAttribute("aria-selected", "false");
    });
    btn.classList.add("active");
    btn.setAttribute("aria-selected", "true");

    const tab = btn.dataset.tab;

    if (tab === "context") {
      stopMassingPolling();
      stopEnrichedPolling();
      stopMasterplanPolling();
      await loadContextGraphOnce();
      return;
    }

    if (tab === "brief") {
      stopMassingPolling();
      stopEnrichedPolling();
      stopMasterplanPolling();
      if (window._briefGraph && typeof window.showGraph3DBackground === "function") {
        window.showGraph3DBackground(window._briefGraph);
      } else if (typeof window.clearGraph === "function") {
        window.clearGraph();
      }
      return;
    }

    if (tab === "massing") {
      stopEnrichedPolling();
      stopMasterplanPolling();
      await loadMassingGraphOnce();
      startMassingPolling();
      return;
    }

    if (tab === "enriched") {
      stopMassingPolling();
      stopMasterplanPolling();
      startEnrichedPolling();
      return;
    }

    if (tab === "masterplan") {
      stopMassingPolling();
      stopEnrichedPolling();
      await loadMasterplanGraphOnce();
      startMasterplanPolling();
      return;
    }

    // default: clear + stop all pollers
    stopMassingPolling();
    stopEnrichedPolling();
    stopMasterplanPolling();
    if (typeof window.clearGraph === "function") window.clearGraph();
  });
});

// Initial boot: show the active tab’s graph once
document.addEventListener("DOMContentLoaded", async () => {
  const activeTab = document.querySelector('.tab button.active')?.dataset?.tab;
  if (activeTab === "massing") {
    stopMasterplanPolling();
    stopEnrichedPolling();
    await loadMassingGraphOnce();
    startMassingPolling();
  } else if (activeTab === "context") {
    stopMassingPolling();
    stopEnrichedPolling();
    stopMasterplanPolling();
    await loadContextGraphOnce();
  } else if (activeTab === "masterplan") {
    stopMassingPolling();
    stopEnrichedPolling();
    await loadMasterplanGraphOnce();
    startMasterplanPolling();
  } else if (activeTab === "brief") {
    stopMassingPolling();
    stopEnrichedPolling();
    stopMasterplanPolling();
    if (window._briefGraph && typeof window.showGraph3DBackground === "function") {
      window.showGraph3DBackground(window._briefGraph);
    } else if (typeof window.clearGraph === "function") {
      window.clearGraph();
    }
  } else if (activeTab === "enriched") {
    stopMassingPolling();
    stopMasterplanPolling();
    startEnrichedPolling();
  } else {
    stopMassingPolling();
    stopEnrichedPolling();
    stopMasterplanPolling();
    if (typeof window.clearGraph === "function") window.clearGraph();
  }
});

window.addEventListener("beforeunload", () => {
  stopMassingPolling();
  stopEnrichedPolling();
  stopMasterplanPolling();
});
