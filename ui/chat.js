// LLM + upload + dropdowns + optional 3D graph

(function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
  function appendMessage(role, content) {
    const wrapper = document.getElementById("chat-history");
    const box = wrapper.querySelector(".history-content") || wrapper;

    const el = document.createElement("div");
    el.className = `msg ${role}`;
    el.innerHTML = DOMPurify.sanitize(
      marked.parse(content).replace(/^<p>|<\/p>$/g, "")
    );
    box.appendChild(el);

    // handle stays pinned
    box.scrollTop = box.scrollHeight;
  }

  /* ---------------- Server health ---------------- */
  async function checkServer() {
    try {
      const res = await fetch("http://localhost:8000/initial_greeting?test=true", { credentials: "omit" });
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

    // Keep open when interacting inside
    dropdown.addEventListener("click", (e) => e.stopPropagation());
    dropdown.addEventListener("mousedown", (e) => e.stopPropagation());
    dropdown.addEventListener("focusin", open); // stays open while inputs focused

    // Close only when clicking OUTSIDE pill+dropdown
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
      appendMessage("bot", "Reading brief… extracting entities… building graph.");
      showLoading("Reading brief…");

      // simple messages while waiting
      let stageTimers = [];
      stageTimers.push(setTimeout(() => setLoading("Extracting entities…"), 1200));
      stageTimers.push(setTimeout(() => setLoading("Building graph…"), 2600));

      try {
        const res = await fetch("http://localhost:8000/upload_brief", { method: "POST", body: formData });
        const data = await res.json();

        // Tooltip filename on the pill
        if (uploadPill && file?.name) {
          uploadPill.setAttribute("data-filename", file.name);
        }

        if (data.chat_notice) appendMessage("bot", data.chat_notice);
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
        appendMessage("bot", "Hmm, that failed to process. Try again?");
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
    const tPlot = document.getElementById("togglePlot");
    const tCtx  = document.getElementById("toggleContext");
    const bake  = document.getElementById("bakeBtn");

    if (tPlot) {
      tPlot.addEventListener("change", () => {
        console.log("[Rhino] Plot graph:", tPlot.checked);
        // <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< @CESAR
        // TODO: fetch("/rhino/plot_graph", { method:"POST", headers:{'Content-Type':'application/json'}, body: JSON.stringify({ enabled: tPlot.checked }) })
      });
    }

    if (tCtx) {
      tCtx.addEventListener("change", () => {
        console.log("[Rhino] Context graph:", tCtx.checked);
        // <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< @CESAR
        // TODO: fetch("/rhino/context_graph", { method:"POST", headers:{'Content-Type':'application/json'}, body: JSON.stringify({ enabled: tCtx.checked }) })
      });
    }

    if (bake) {
      bake.addEventListener("click", async () => {
        appendMessage("user", "Bake masterplan");
        appendMessage("bot", "Starting bake…");
        try {
          // Example placeholder; replace with real endpoint: <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< @CESAR
          // const res = await fetch("http://localhost:8000/rhino/bake_masterplan", { method: "POST" });
          // const json = await res.json();
          // appendMessage("bot", json?.status || "Bake complete.");
          appendMessage("bot", "Bake complete.");
        } catch (e) {
          appendMessage("bot", "Bake failed.");
        }
      });
    }
  }

  /* ---------------- Chat send ---------------- */
  async function sendMessage() {
    const input = document.getElementById("chat-input");
    const text = (input?.value || "").trim();
    if (!text) return;

    appendMessage("user", text);
    input.value = "";

    // placeholder
    const box = document.querySelector("#chat-history .history-content");
    appendMessage("bot", "...");

    try {
      const res = await fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      const json = await res.json();

      // remove placeholder
      if (box?.lastElementChild) box.removeChild(box.lastElementChild);
      appendMessage("bot", json.response || "No reply.");
    } catch (err) {
      console.error(err);
      if (box?.lastElementChild) box.removeChild(box.lastElementChild);
      appendMessage("bot", "Error contacting the assistant.");
    }
  }

  function initChatControls() {
    const sendBtn = document.getElementById("chat-send");
    const inputEl = document.getElementById("chat-input");

    if (sendBtn) sendBtn.addEventListener("click", sendMessage);
    if (inputEl) {
      inputEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
      });
    }
  }

  /* ---------------- Boot ---------------- */
  window.addEventListener("DOMContentLoaded", async () => {
    // keep handle + wrapper ; clear only messages
    const hc = document.querySelector("#chat-history .history-content");
    if (hc) hc.innerHTML = "";

    appendMessage("bot", "Connecting...");
    let serverReady = false;

    for (let i = 0; i < 10; i++) {
      serverReady = await checkServer();
      if (serverReady) break;
      await sleep(800);
    }

    // remove the last "Connecting..." line in chat
    if (hc?.lastElementChild) hc.removeChild(hc.lastElementChild);

    if (serverReady) {
      try {
        const res = await fetch("http://localhost:8000/initial_greeting");
        const json = await res.json();
        appendMessage("bot", json.response);
      } catch {
        appendMessage("bot", "Couldn't fetch greeting, but the server seems up.");
      }
    } else {
      appendMessage("bot", "Couldn't connect to the assistant.");
    }

    initChatControls();
    initUpload();
    setupStickyDropdown("contextPill", "contextForm");
    setupStickyDropdown("paramPill", "paramForm");
    setupStickyDropdown("rhinoPill", "rhinoForm");
    bindRhinoPanel();
  });

  /* ---------------- 3D Graph (background, interactive) ---------------- */
  // Expose helpers globally so app.js tab logic can call them
  window.showGraph3DBackground = function showGraph3DBackground(dataGraph) {
    if (typeof ForceGraph3D !== "function") return;
    const mount = document.getElementById("graph3d");
    if (!mount) return;

    const nodes = (dataGraph.nodes || []).map(n => ({
      id: n.id,
      name: n.label || n.id,
      typology: n.typology || "",
      footprint: n.footprint || 0
    }));
    const links = (dataGraph.edges || []).map(e => ({
      source: e.source, target: e.target, type: e.type || "adjacent"
    }));

    const deg = new Map(nodes.map(n => [n.id, 0]));
    links.forEach(l => {
      deg.set(l.source, (deg.get(l.source) || 0) + 1);
      deg.set(l.target, (deg.get(l.target) || 0) + 1);
    });

    if (!window.Graph3DInstance) {
      // IMPORTANT: allow interaction; nothing overlays the empty areas
      window.Graph3DInstance = ForceGraph3D()(mount)
        .backgroundColor("#f0f0f0")
        .cooldownTicks(500) // finite settle
        .d3VelocityDecay(0.12)
        .nodeRelSize(8)
        .nodeOpacity(1)
        .nodeLabel(n => `${n.name}${n.typology ? " • " + n.typology : ""}`)
        .nodeColor(() => "#000000ff")
        .linkColor(() => "rgba(138, 138, 138, 1)")
        .enableNodeDrag(true) // drag nodes
        .showNavInfo(false) // cleaner look
        .warmupTicks(60); // quicker initial layout
    }

    window.Graph3DInstance.graphData({ nodes, links });

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
        window.Graph3DInstance.zoomToFit(600, 8); // ms, paddingPx
        const controls = window.Graph3DInstance.controls?.();
        if (controls && typeof controls.dollyIn === "function") {
          controls.dollyIn(1.2); // move camera closer
          controls.update();
        }
      } catch {}
    }, 150);
  };

  window.clearGraph = function clearGraph() {
    if (window.Graph3DInstance) {
      window.Graph3DInstance.graphData({ nodes: [], links: [] });
    }
  };
})();
