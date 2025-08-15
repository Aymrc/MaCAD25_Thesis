// LLM + upload + dropdowns + optional 3D graph

(function () {
  // ---------- Config ----------
  const API = "http://localhost:8000";

  // ---------- Utilities ----------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const $ = (sel) => document.querySelector(sel);

  /* ---------------- Loading chip helpers ---------------- */
  const chip = () => document.getElementById("graph-loading");
  const chipLabel = () => {
    const c = chip();
    if (!c) return null;
    return c.querySelector(".label") || c.querySelector("span:last-child");
  };

  function showLoading(text) {
    const c = chip();
    if (!c) return;
    const lbl = chipLabel();
    if (lbl && text) lbl.textContent = text;
    c.classList.add("show");
  }
  function setLoading(text) {
    const lbl = chipLabel();
    if (lbl && typeof text === "string") lbl.textContent = text;
  }
  function hideLoading() {
    const c = chip();
    if (!c) return;
    c.classList.remove("show");
  }

  /* ---------------- Chat rendering ---------------- */
  // Main: #chat-history receives .message ROLE
  function appendMessageMain(role, content) {
    const chat = document.getElementById("chat-history");
    if (!chat) return;
    const el = document.createElement("div");
    el.className = "message " + role;
    const html = DOMPurify.sanitize(marked.parse(content));
    el.innerHTML = html;
    chat.appendChild(el);
    if (chat.parentElement) chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
  }

  // Legacy: uses #chat-history .history-content and strips outer <p>
  function appendMessageLegacy(role, content) {
    const wrapper = document.getElementById("chat-history");
    if (!wrapper) return;
    const box = wrapper.querySelector(".history-content") || wrapper;

    const el = document.createElement("div");
    el.className = `msg ${role}`;
    el.innerHTML = DOMPurify.sanitize(
      marked.parse(content).replace(/^<p>|<\/p>$/g, "")
    );
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
  }

  // Dispatcher: prefer legacy when .history-content exists
  function appendMessage(role, content) {
    const hasLegacyBox = !!document.querySelector("#chat-history .history-content");
    return hasLegacyBox ? appendMessageLegacy(role, content) : appendMessageMain(role, content);
  }

  function setStatus(text) {
    const s = document.getElementById("status");
    if (s) s.textContent = text || "";
    else console.log("[status]", text);
  }

  // Strip accidental meta prefixes from LLM greetings
  function cleanGreeting(t) {
    if (!t) return t;
    t = t.replace(/^```(?:\w+)?\s*|\s*```$/g, "");
    t = t.replace(
      /^\s*(?:need\b.*?greet\w*|greeting|assistant|system|note|meta)\s*[:\-.\]]*\s*/i,
      ""
    );
    if (/^need\b/i.test(t)) {
      const m = t.match(/\.\s*([\s\S]+)$/);
      if (m) t = m[1];
    }

    t = t.replace(/\s+(?:User:|Assistant:|System:).*/i, "");

    const m2 = t.match(/^(.+?[.!?])(\s|$)/s);
    return (m2 ? m2[1] : t).trim();
  }

  /* ---------------- Server health ---------------- */
  async function checkServer() {
    try {
      const res = await fetch(`${API}/initial_greeting?test=true`, { credentials: "omit" });
      const json = await res.json();
      return json.dynamic === true;
    } catch {
      return false;
    }
  }

  /* ---------------- Context dropdown ---------------- */
  function setupStickyDropdown(pillId, dropdownId) {
    const pill = document.getElementById(pillId);
    const dropdown = document.getElementById(dropdownId);
    if (!pill || !dropdown) return;

    let hideTimeout;

    const open = () => {
      clearTimeout(hideTimeout);
      dropdown.style.display = "block";
      pill.setAttribute("aria-expanded", "true");
    };

    const scheduleClose = () => {
      hideTimeout = setTimeout(() => {
        dropdown.style.display = "none";
        pill.setAttribute("aria-expanded", "false");
      }, 200);
    };

    // Hover support
    pill.addEventListener("mouseenter", open);
    dropdown.addEventListener("mouseenter", open);
    pill.addEventListener("mouseleave", scheduleClose);
    dropdown.addEventListener("mouseleave", scheduleClose);

    // Click toggle
    pill.addEventListener("click", (e) => {
      e.stopPropagation();
      const visible = dropdown.style.display === "block";
      if (visible) scheduleClose();
      else open();
    });

    // Keep open while interacting inside
    dropdown.addEventListener("click", (e) => e.stopPropagation());
    dropdown.addEventListener("mousedown", (e) => e.stopPropagation());
    dropdown.addEventListener("focusin", open);

    // Close on outside click
    document.addEventListener("click", (e) => {
      const outside = !pill.contains(e.target) && !dropdown.contains(e.target);
      if (outside) {
        clearTimeout(hideTimeout);
        dropdown.style.display = "none";
        pill.setAttribute("aria-expanded", "false");
      }
    });

    // Esc to close
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        clearTimeout(hideTimeout);
        dropdown.style.display = "none";
        pill.setAttribute("aria-expanded", "false");
        pill.focus();
      }
    });
  }

  /* ---------------- Brief upload ---------------- */
  function initUpload() {
    const fileInput   = document.getElementById("brief-upload");
    const uploadLabel = document.getElementById("uploadLabel");
    const uploadPill  = document.querySelector(".pill.upload-pill");
    if (!fileInput || !uploadLabel || !uploadPill) return;

    const setUploadEmptyState = () => {
      const isEmpty = !fileInput.files || fileInput.files.length === 0;
      uploadPill.classList.toggle("empty", isEmpty);
    };

    uploadPill.addEventListener("click", () => fileInput.click());
    uploadPill.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
    });

    async function handleUpload(e) {
      const file = e.target.files[0];
      if (!file || !file.name.toLowerCase().endsWith(".pdf")) {
        alert("Please upload a PDF file.");
        fileInput.value = "";
        setUploadEmptyState();
        return;
      }

      uploadLabel.textContent = `Brief: ${file.name}`;
      setUploadEmptyState();

      const formData = new FormData();
      formData.append("file", file);

      // UX: immediate feedback
      appendMessage("assistant", "Reading brief… extracting entities… building graph.");
      showLoading("Reading brief…");

      // simple messages while waiting
      let stageTimers = [];
      stageTimers.push(setTimeout(() => setLoading("Extracting entities…"), 1200));
      stageTimers.push(setTimeout(() => setLoading("Building graph…"), 2600));

      try {
        const res = await fetch(`${API}/upload_brief`, {
          method: "POST",
          body: formData,
        });
        const data = await res.json();

        // Tooltip filename on the pill
        if (uploadPill && file?.name) {
          uploadPill.setAttribute("data-filename", file.name);
        }

        if (data.chat_notice) appendMessage("assistant", data.chat_notice);
        uploadLabel.textContent = "Brief uploaded";
        console.log("Uploaded:", data);

        // Store the brief graph globally ; render only if "Brief" tab is active
        if (data.graph && data.graph.nodes?.length) {
          window._briefGraph = data.graph;
          const briefActive = document.querySelector('.tab button.active[data-tab="brief"]');
          if (briefActive && typeof window.showGraph3DBackground === "function") {
            window.showGraph3DBackground(window._briefGraph);
          }
        }
      } catch (err) {
        console.error("Upload failed", err);
        uploadLabel.textContent = "Upload failed";
        appendMessage("assistant", "Hmm, that failed to process. Try again?");
      } finally {
        // clear staged timers & hide chip
        stageTimers.forEach(t => clearTimeout(t));
        hideLoading();
      }
    }

    fileInput.addEventListener("change", handleUpload);
    setUploadEmptyState();
  }

  /* ---------------- Rhino toggles + bake ---------------- */
  function bindRhinoPanel() {
    const API  = "http://localhost:8000";
    const tPlot = document.getElementById("togglePlot");
    const tCtx  = document.getElementById("toggleContext");
    const bake  = document.getElementById("bakeBtn");

    async function postPreview(kind, enabled) {
      // kind: "context" | "plot"
      try {
        const res = await fetch(`${API}/preview/${kind}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !!enabled })
        });
        const j = await res.json();
        if (!j.ok) console.warn(`[preview] ${kind} -> backend said not ok`, j);
      } catch (e) {
        console.warn(`[preview] ${kind} toggle failed (server not ready?)`, e);
      }
    }

    if (tPlot) {
      tPlot.addEventListener("change", () => {
        postPreview("plot", tPlot.checked);   // <-- antes solo hacía console.log
      });
    }

    if (tCtx) {
      tCtx.addEventListener("change", () => {
        postPreview("context", tCtx.checked); // <-- antes solo hacía console.log
      });
    }

    if (bake) {
      bake.addEventListener("click", async () => {
        try {
          await fetch(`${API}/rhino/bake_masterplan`, { method: "POST" });
        } catch (e) {
          console.warn("[Rhino] bake failed", e);
        }
      });
    }
  }


  // ---------- OSM run + polling (silent to chat) ----------
  function toNumber(val) {
    var n = parseFloat((val || "").toString().trim());
    return isNaN(n) ? null : n;
  }

  function startOsm(lat, lon, radius_km) {
    return fetch(`${API}/osm/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: lat, lon: lon, radius_km: radius_km })
    }).then((r) => r.json());
  }

  function pollStatus(jobId) {
    return fetch(`${API}/osm/status/${jobId}`).then((r) => r.json());
  }

  // ---------- Preview toggles (Context Graph / Plot Graph) ----------
  async function postPreview(kind, enabled) {
    // kind: "context" | "plot"
    try {
      const res = await fetch(`${API}/preview/${kind}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !!enabled })
      });
      const j = await res.json();
      if (!j.ok) console.warn(`[preview] ${kind} -> backend said not ok`, j);
    } catch (e) {
      console.warn(`[preview] ${kind} toggle failed (server not ready?)`, e);
    }
  }

  async function fetchPreviewState() {
    try {
      const res = await fetch(`${API}/preview/state`);
      if (!res.ok) return null;
      return await res.json(); // {context_preview: bool, plot_preview: bool}
    } catch {
      return null;
    }
  }

  function getParamTogglesByLabel() {
    // Your HTML reuses the same id for several inputs.
    // We select by label text to avoid relying on unique IDs.
    const form = document.getElementById("paramForm");
    const out = { viewContext: null, contextGraph: null, plotGraph: null };
    if (!form) return out;

    const labels = Array.from(form.querySelectorAll("label"));
    for (const lbl of labels) {
      const txt = (lbl.textContent || "").trim();
      const input = lbl.querySelector('input[type="checkbox"]');
      if (!input) continue;
      if (txt === "View Context") out.viewContext = input;
      else if (txt === "Context Graph") out.contextGraph = input;
      else if (txt === "Plot Graph") out.plotGraph = input;
    }
    return out;
  }

  function initParamToggles() {
    const { contextGraph, plotGraph } = getParamTogglesByLabel();

    if (contextGraph) {
      contextGraph.addEventListener("change", () => {
        postPreview("context", contextGraph.checked);
      });
    }
    if (plotGraph) {
      plotGraph.addEventListener("change", () => {
        postPreview("plot", plotGraph.checked);
      });
    }
  }

  /* ---------------- Sync preview from server ---------------- */
  async function syncPreviewTogglesFromServer() {
    const st = await fetchPreviewState();
    if (!st) return;
    const { contextGraph, plotGraph } = getParamTogglesByLabel();
    if (contextGraph) contextGraph.checked = !!st.context_preview;
    if (plotGraph)   plotGraph.checked   = !!st.plot_preview;
  }

  // ---------- Chat controls (merged) ----------
  function initChatControls() {
    // Support both ID schemes
    const sendBtnA = document.getElementById("sendBtn");
    const inputA   = document.getElementById("chatInput");
    const sendBtnB = document.getElementById("chat-send");
    const inputB   = document.getElementById("chat-input");

    if (sendBtnA) sendBtnA.addEventListener("click", sendMessage);
    if (inputA)   inputA.addEventListener("keydown", (e) => { if (e.key === "Enter") sendMessage(); });

    if (sendBtnB) sendBtnB.addEventListener("click", sendMessage);
    if (inputB) {
      inputB.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
      });
    }

    // Context setter -> Start OSM job and poll status (NO chat messages)
    const saveBtn = document.getElementById("saveContextBtn");
    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        var lat = toNumber(document.getElementById("latInput").value);
        var lon = toNumber(document.getElementById("longInput").value);
        var radiusMeters = toNumber(document.getElementById("radiusInput").value);
        var radius = (radiusMeters === null ? null : radiusMeters / 1000);

        if (lat === null || lon === null || radius === null) {
          alert("Please enter valid numbers for Lat, Long and Radius.");
          return;
        }
        if (lat < -90 || lat > 90 || lon < -180 || lon > 180 || radius <= 0) {
          alert("Lat must be [-90,90], Long [-180,180], Radius > 0.");
          return;
        }

        setStatus("Starting OSM job...");
        startOsm(lat, lon, radius).then(function (resp) {
          if (!resp.ok) {
            setStatus("OSM job failed: " + (resp.error || "unknown error"));
            return;
          }
          var jobId = resp.job_id;
          try { localStorage.setItem("latest_osm_job", jobId); } catch (e) {}
          setStatus("OSM job running...");

          var intv = setInterval(function () {
            pollStatus(jobId).then(function (st) {
              if (!st.ok) {
                clearInterval(intv);
                setStatus("Status error: " + (st.error || "unknown"));
                return;
              }
              if (st.status === "finished") {
                clearInterval(intv);
                setStatus("OSM job finished. Importing into Rhino...");
              } else if (st.status === "failed") {
                clearInterval(intv);
                setStatus("OSM job failed. See FAILED.txt");
              }
            }).catch(function (err) {
              clearInterval(intv);
              setStatus("Error checking job: " + err);
            });
          }, 3000);
        }).catch(function (err) {
          setStatus("Error starting OSM job: " + err);
        });
      });
    }
  }

  /* ---------------- Graph3D fullscreen helpers ---------------- */
  function ensureGraph3DFullscreen() {
    const mount = document.getElementById("graph3d");
    if (!mount) return;

    const parent = mount.parentElement || document.body;
    const parentStyle = getComputedStyle(parent);
    if (parentStyle.position === "static") parent.style.position = "relative";

    // Try to fill parent; if parent is too small, fallback to viewport
    const parentRect = parent.getBoundingClientRect();
    const parentTooSmall = (parentRect.width < 400 || parentRect.height < 300);

    if (parentTooSmall && parent === document.body) {
      mount.style.position = "fixed";
      mount.style.inset = "0";
    } else {
      mount.style.position = "absolute";
      mount.style.inset = "0";
    }
    mount.style.width = "100%";
    mount.style.height = "100%";
    mount.style.zIndex = "0"; // keep UI above; raise if you need interactions
  }

  function resizeGraph3D() {
    const mount = document.getElementById("graph3d");
    if (!mount || !window.Graph3DInstance) return;
    const w = mount.clientWidth  || window.innerWidth;
    const h = mount.clientHeight || window.innerHeight;
    try { window.Graph3DInstance.width(w).height(h); } catch {}
  }

  /* ---------------- Chat send ---------------- */
  async function sendMessage() {
    const input = document.getElementById("chat-input") || document.getElementById("chatInput");
    const text = (input?.value || "").trim();
    if (!text) return;

    appendMessage("user", text);
    if (input) input.value = "";

    // typing indicator placeholder
    const hasLegacyBox = !!document.querySelector("#chat-history .history-content");
    let placeholder = null;
    if (hasLegacyBox) {
      const box = document.querySelector("#chat-history .history-content");
      placeholder = document.createElement("div");
      placeholder.className = "msg assistant";
      placeholder.textContent = "…";
      box.appendChild(placeholder);
      box.scrollTop = box.scrollHeight;
    } else {
      appendMessage("assistant", "…");
    }

    try {
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      const json = await res.json();

      if (placeholder?.parentElement) placeholder.parentElement.removeChild(placeholder);
      appendMessage("assistant", json.response || "No reply.");
    } catch (err) {
      console.error(err);
      if (placeholder?.parentElement) placeholder.parentElement.removeChild(placeholder);
      appendMessage("assistant", "Error contacting the assistant.");
    }
  }

  /* ---------------- Boot ---------------- */
  window.addEventListener("DOMContentLoaded", async () => {
    // keep handle + wrapper ; clear only messages
    const hc = document.querySelector("#chat-history .history-content");
    if (hc) hc.innerHTML = "";

    appendMessage("assistant", "Connecting...");
    let serverReady = false;
    for (let i = 0; i < 10; i++) {
      serverReady = await checkServer();
      if (serverReady) break;
      await sleep(800);
    }

    // remove the last "Connecting..." line in chat (legacy UI)
    if (hc?.lastElementChild) hc.removeChild(hc.lastElementChild);

    // If no legacy box, clear the simple container
    if (!document.querySelector("#chat-history .history-content")) {
      const ch = document.getElementById("chat-history");
      if (ch) ch.innerHTML = "";
    }

    // Only show LLM greeting if available
    if (serverReady) {
      try {
        const res = await fetch(`${API}/initial_greeting`);
        const json = await res.json();
        appendMessage("assistant", json.response);
      } catch (e) {
        console.log("[greeting] failed", e);
        appendMessage("assistant", "Couldn't fetch greeting, but the server seems up.");
      }
    } else {
      appendMessage("assistant", "Couldn't connect to the assistant.");
    }

    initChatControls();
    initUpload();
    setupStickyDropdown("contextPill", "contextForm");
    setupStickyDropdown("paramPill", "paramForm");
    setupStickyDropdown("rhinoPill", "rhinoForm");
    bindRhinoPanel();
    syncPreviewTogglesFromServer();
    initParamToggles();

    // Try to sync preview toggles with server state (if backend exposes it)
    if (serverReady) {
      syncPreviewTogglesFromServer();
    }
  });

  /* ---------------- 3D Graph (background, interactive) ---------------- */
  // Expose helpers globally so app.js tab logic can call them
  /* ---------------- 3D Graph (background, interactive) ---------------- */
  // Expose helpers globally so app.js tab logic can call them
  // window.showGraph3DBackground = function showGraph3DBackground(dataGraph) {
  //   if (typeof ForceGraph3D !== "function") return;
  //   const mount = document.getElementById("graph3d");
  //   if (!mount) return;

  //   ensureGraph3DFullscreen();

  //   const nodes = (dataGraph.nodes || []).map(n => {
  //     const buildingId = n.building_id || (typeof n.id === "string" ? n.id.split("|")[0] : "");
  //     return {
  //       id: n.id,
  //       name: n.label || n.clean_id || n.id, // prefer clean label if present
  //       typology: n.typology || "",
  //       footprint: n.footprint || 0,
  //       buildingId,
  //       kind: n.type || "",
  //       area: Number.isFinite(n.area) ? n.area : null,
  //       level: Number.isFinite(n.level) ? n.level : null
  //     };
  //   });

  //   const links = (dataGraph.edges || dataGraph.links || []).map(e => ({
  //     source: e.source,
  //     target: e.target,
  //     type: e.type || "adjacent"
  //   }));

  //   // --- Build monochrome gradient per building (light grey -> black) ---
  //   const uniqueBuildings = Array.from(new Set(
  //     nodes.map(n => n.buildingId).filter(Boolean).sort()
  //   ));

  //   const colorMap = {};
  //   uniqueBuildings.forEach((bid, idx) => {
  //     const t = idx / Math.max(1, uniqueBuildings.length - 1); // 0..1
  //     const shade = Math.round(238 - t * 238); // 238 -> 0
  //     const hex = shade.toString(16).padStart(2, "0");
  //     colorMap[bid] = `#${hex}${hex}${hex}`;
  //   });

  //   const deg = new Map(nodes.map(n => [n.id, 0]));
  //   links.forEach(l => {
  //     deg.set(l.source, (deg.get(l.source) || 0) + 1);
  //     deg.set(l.target, (deg.get(l.target) || 0) + 1);
  //   });

  //   if (!window.Graph3DInstance) {
  //     window.Graph3DInstance = ForceGraph3D()(mount)
  //       .backgroundColor("#f0f0f0")
  //       .cooldownTicks(500)
  //       .d3VelocityDecay(0.12)
  //       .nodeRelSize(15)
  //       .nodeOpacity(1)
  //       .nodeLabel(n => {
  //         if (n.id === "PLOT" || n.kind === "plot") return "Plot";
  //         const parts = [];

  //         if (n.buildingId) parts.push(`<b>Building:</b> ${n.buildingId}`);
  //         parts.push(`<b>Name:</b> ${n.name}`);
  //         if (Number.isFinite(n.level)) parts.push(`<b>Level:</b> ${n.level}`);

  //         if (Number.isFinite(n.area)) {
  //           const rounded = Math.round(n.area);
  //           parts.push(`<b>Area:</b> ${rounded.toLocaleString()} m²`);
  //         }
          
  //         if (n.typology) parts.push(`<b>Typology:</b> ${n.typology}`);
  //         return parts.join("<br>"); // multi-line
  //       })
  //       .enableNodeDrag(true)
  //       .showNavInfo(false)
  //       .warmupTicks(60);
  //   }

  //   window.Graph3DInstance
  //     .nodeColor(n => {
  //       if (n.id === "PLOT" || n.kind === "plot") return "#ff0000"; // plot node in red
  //       return colorMap[n.buildingId] || "#000000";
  //     })
  //     .linkColor(() => "rgba(138, 138, 138, 1)");

  //   window.Graph3DInstance.graphData({ nodes, links });
  //   resizeGraph3D();
  //   if (!window._graph3dResizeBound) {
  //     window.addEventListener("resize", resizeGraph3D);
  //     window._graph3dResizeBound = true;
  //   }

  //   requestAnimationFrame(() => {
  //     const charge = window.Graph3DInstance.d3Force('charge');
  //     if (charge?.strength) charge.strength(-160);

  //     const link = window.Graph3DInstance.d3Force('link');
  //     if (link?.distance && link?.strength) {
  //       link
  //         .distance(l => {
  //           const s = l.source.id || l.source;
  //           const t = l.target.id || l.target;
  //           const d = (deg.get(s) || 0) + (deg.get(t) || 0);
  //           return 40 + 8 * Math.sqrt(d);
  //         })
  //         .strength(0.04);
  //     }
  //     try { window.Graph3DInstance.d3ReheatSimulation(); } catch {}
  //   });

  //   setTimeout(() => {
  //     try {
  //       window.Graph3DInstance.zoomToFit(600, 8);
  //       const controls = window.Graph3DInstance.controls?.();
  //       if (controls?.dollyIn) { controls.dollyIn(1.2); controls.update(); }
  //     } catch {}
  //   }, 150);
  // };

  window.showGraph3DBackground = function showGraph3DBackground(dataGraph) {
    if (typeof ForceGraph3D !== "function") return;
    const mount = document.getElementById("graph3d");
    if (!mount) return;

    ensureGraph3DFullscreen();

    // ---- Category color palette (hex; mirrors your FromArgb) ----
    const CAT_COLORS = {
      Residential: "#DC2D46", // (220,45,70)
      Office:      "#0070B8", // (0,112,184)
      Leisure:     "#00AA46", // (0,170,70)
      Cultural:    "#8C008C", // (140,0,140)
      Green:       "#50B478"  // (80,180,120)
    };

    // ---- Edge color palette ----
    const EDGE_COLORS = {
      street: "#8A8A8A", // (138,138,138)
      access: "#8A8A8A"  // (138,138,138)
    };

    // ---- JS port of your categorize_node(attrs) heuristic ----
    function categorizeNode(attrs) {
      try {
        const tags = {};
        for (const [k, v] of Object.entries(attrs || {})) {
          try { tags[String(k).toLowerCase()] = String(v).toLowerCase(); } catch {}
        }

        const b = tags["building"];

        const residential = new Set([
          "apartments","house","residential","semidetached_house",
          "terrace","bungalow","detached","dormitory"
        ]);
        const office = new Set([
          "office","commercial","industrial","retail","manufacture",
          "warehouse","service"
        ]);
        const cultural = new Set([
          "college","school","kindergarten","government","civic",
          "church","fire_station","prison"
        ]);
        const leisure = new Set(["hotel","boathouse","houseboat","bridge"]);
        const green   = new Set(["greenhouse","allotment_house"]);

        if (b === "yes") return "Residential";
        if (residential.has(b)) return "Residential";
        if (office.has(b)) return "Office";
        if (cultural.has(b)) return "Cultural";
        if (leisure.has(b)) return "Leisure";
        if (green.has(b)) return "Green";

        const amen = tags["amenity"] || "";
        if (amen.includes("museum") || amen.includes("theatre") || amen.includes("gallery")) {
          return "Cultural";
        }

        const leis = tags["leisure"] || "";
        if (leis.includes("park") || leis.includes("recreation") || leis.includes("garden")) {
          return "Leisure";
        }

        if ((tags["landuse"] === "grass" || tags["landuse"] === "meadow") ||
            (tags["type"] || "").includes("green")) {
          return "Green";
        }

        return null;
      } catch { return null; }
    }

    // ---- Try multiple hints to guess category ----
    function pickCategory(n) {
      const byAttrs = categorizeNode(n.attrs || {});
      if (byAttrs) return byAttrs;

      const hint = (n.typology || n.kind || "").toLowerCase();
      if (!hint) return null;
      if (/(res|housing|living|residential)/.test(hint)) return "Residential";
      if (/(office|commercial|retail|work)/.test(hint))    return "Office";
      if (/(museum|theatre|gallery|school|college|civic|gov|cultural)/.test(hint)) return "Cultural";
      if (/(leisure|hotel|park|garden|recreation)/.test(hint)) return "Leisure";
      if (/(green|grass|meadow|landscape)/.test(hint)) return "Green";
      return null;
    }

    // ---- Build nodes/links, preserving original attrs for categorization ----
    const nodes = (dataGraph.nodes || []).map(n => {
      const buildingId = n.building_id || (typeof n.id === "string" ? n.id.split("|")[0] : "");
      return {
        id: n.id,
        name: n.label || n.clean_id || n.id,
        typology: n.typology || "",
        footprint: n.footprint || 0,
        buildingId,
        kind: n.type || n.kind || "",
        area: Number.isFinite(n.area) ? n.area : null,
        level: Number.isFinite(n.level) ? n.level : null,
        attrs: n // keep raw attributes for categorizeNode
      };
    });

    const links = (dataGraph.edges || dataGraph.links || []).map(e => ({
      source: e.source,
      target: e.target,
      type: e.type || "adjacent"
    }));

    // ---- Fallback monochrome per-building (used only if no category) ----
    const uniqueBuildings = Array.from(new Set(
      nodes.map(n => n.buildingId).filter(Boolean).sort()
    ));
    const monoColorMap = {};
    uniqueBuildings.forEach((bid, idx) => {
      const t = idx / Math.max(1, uniqueBuildings.length - 1);
      const shade = Math.round(238 - t * 238); // 238 -> 0
      const hex = shade.toString(16).padStart(2, "0");
      monoColorMap[bid] = `#${hex}${hex}${hex}`;
    });

    // ---- Degree cache for distance heuristic ----
    const deg = new Map(nodes.map(n => [n.id, 0]));
    links.forEach(l => {
      deg.set(l.source, (deg.get(l.source) || 0) + 1);
      deg.set(l.target, (deg.get(l.target) || 0) + 1);
    });

    // ---- Init ForceGraph3D once ----
    if (!window.Graph3DInstance) {
      window.Graph3DInstance = ForceGraph3D()(mount)
        .backgroundColor("#f0f0f0")
        .cooldownTicks(500)
        .d3VelocityDecay(0.12)
        .nodeRelSize(30)
        .nodeOpacity(1)
        .nodeLabel(n => {
          if (n.id === "PLOT" || n.kind === "plot") return "Plot";
          const parts = [];
          if (n.buildingId) parts.push(`<b>Building:</b> ${n.buildingId}`);
          parts.push(`<b>Name:</b> ${n.name}`);
          if (Number.isFinite(n.level)) parts.push(`<b>Level:</b> ${n.level}`);
          if (Number.isFinite(n.area)) {
            const rounded = Math.round(n.area);
            parts.push(`<b>Area:</b> ${rounded.toLocaleString()} m²`);
          }
          const cat = pickCategory(n);
          if (cat) parts.push(`<b>Category:</b> ${cat}`);
          if (n.typology) parts.push(`<b>Typology:</b> ${n.typology}`);
          return parts.join("<br>");
        })
        .enableNodeDrag(true)
        .showNavInfo(false)
        .warmupTicks(60);
    }

    // ---- Color rules for nodes and edges ----
    window.Graph3DInstance
      .nodeColor(n => {
        if (n.id === "PLOT" || n.kind === "plot") return "#ff0000";
        const cat = pickCategory(n);
        if (cat && CAT_COLORS[cat]) return CAT_COLORS[cat];
        return monoColorMap[n.buildingId] || "#000000";
      })
      .linkColor(l => {
        const t = (l.type || "").toString().toLowerCase();
        return EDGE_COLORS[t] || "#8A8A8A";
      })
      .nodeVal(n => {
        // Smaller for streets
        if ((n.kind || "").toLowerCase() === "street") return 2;

        // Larger for buildings/parks categories
        const cat = pickCategory(n);
        if (cat && ["Residential","Office","Leisure","Cultural","Green"].includes(cat)) {
          return 12;
        }

        // Default size
        return 6;
      });

    // ---- Push data & forces ----
    window.Graph3DInstance.graphData({ nodes, links });
    resizeGraph3D();
    if (!window._graph3dResizeBound) {
      window.addEventListener("resize", resizeGraph3D);
      window._graph3dResizeBound = true;
    }

    requestAnimationFrame(() => {
      const charge = window.Graph3DInstance.d3Force('charge');
      if (charge?.strength) charge.strength(-160);

      const link = window.Graph3DInstance.d3Force('link');
      if (link?.distance && link?.strength) {
        link
          .distance(l => {
            const s = l.source.id || l.source;
            const t = l.target.id || l.target;
            const d = (deg.get(s) || 0) + (deg.get(t) || 0);
            return 40 + 8 * Math.sqrt(d);
          })
          .strength(0.04);
      }
      try { window.Graph3DInstance.d3ReheatSimulation(); } catch {}
    });

    setTimeout(() => {
      try {
        window.Graph3DInstance.zoomToFit(600, 8);
        const controls = window.Graph3DInstance.controls?.();
        if (controls?.dollyIn) { controls.dollyIn(1.2); controls.update(); }
      } catch {}
    }, 150);
  };



  window.clearGraph = function clearGraph() {
    if (window.Graph3DInstance) {
      window.Graph3DInstance.graphData({ nodes: [], links: [] });
    }
  };
})();
