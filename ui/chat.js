// JS front-end script

(function () {
  // ---------- State ----------
  let Graph3DInstance = null;

  // ---------- Utilities ----------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const $ = (sel) => document.querySelector(sel);

  // Chat message renderer (Markdown + sanitize)
  function appendMessage(role, content) {
    const chat = document.getElementById("chat-history");
    const el = document.createElement("div");
    el.className = `message ${role}`;
    el.innerHTML = DOMPurify.sanitize(marked.parse(content));
    chat.appendChild(el);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
  }

  // Probe API health
  async function checkServer() {
    try {
      const res = await fetch("http://localhost:8000/initial_greeting?test=true", { credentials: "omit" });
      const json = await res.json();
      return json.dynamic === true;
    } catch {
      return false;
    }
  }

  // Sticky dropdowns
  function setupStickyDropdown(pillId, dropdownId) {
    const pill = document.getElementById(pillId);
    const dropdown = document.getElementById(dropdownId);
    let hideTimeout;

    const showDropdown = () => {
      clearTimeout(hideTimeout);
      dropdown.style.display = "block";
      if (pill) pill.setAttribute("aria-expanded", "true");
    };

    const hideDropdown = () => {
      hideTimeout = setTimeout(() => {
        dropdown.style.display = "none";
        if (pill) pill.setAttribute("aria-expanded", "false");
      }, 250);
    };

    pill.addEventListener("mouseenter", showDropdown);
    dropdown.addEventListener("mouseenter", showDropdown);
    pill.addEventListener("mouseleave", hideDropdown);
    dropdown.addEventListener("mouseleave", hideDropdown);
    pill.addEventListener("focus", showDropdown);
    pill.addEventListener("blur", hideDropdown);
  }

  // Upload brief → call backend → render graph in background
  function initUpload() {
    const fileInput   = document.getElementById("brief-upload");
    const uploadLabel = document.getElementById("uploadLabel");
    const uploadPill  = document.querySelector(".pill.upload-pill");

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

      try {
        const res = await fetch("http://localhost:8000/upload_brief", { method: "POST", body: formData });
        const data = await res.json();

        if (data.chat_notice) appendMessage("assistant", data.chat_notice);
        uploadLabel.textContent = "Brief uploaded";
        console.log("Uploaded:", data);

        if (data.graph && data.graph.nodes && data.graph.nodes.length) {
          console.log("[3D] rendering:", data.graph.nodes.length, "nodes,", data.graph.edges.length, "edges");
          showGraph3DBackground(data.graph);
        }
      } catch (err) {
        console.error("Upload failed", err);
        uploadLabel.textContent = "Upload failed";
      }
    }

    fileInput.addEventListener("change", handleUpload);
    setUploadEmptyState();
  }

  // Send chat message
  async function sendMessage() {
    const input = document.getElementById("chatInput");
    const text = input.value.trim();
    if (!text) return;

    appendMessage("user", text);
    input.value = "";

    appendMessage("assistant", "...");
    const chatbox = document.getElementById("chat-history");

    try {
      const res = await fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      const json = await res.json();
      chatbox.removeChild(chatbox.lastChild);
      appendMessage("assistant", json.response || "No reply.");
    } catch (err) {
      console.error(err);
      chatbox.removeChild(chatbox.lastChild);
      appendMessage("assistant", "Error contacting the assistant.");
    }
  }

  // Chat controls
  function initChatControls() {
    document.getElementById("sendBtn").addEventListener("click", sendMessage);
    document.getElementById("chatInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter") sendMessage();
    });

    document.getElementById("saveContextBtn").addEventListener("click", () => {
      const lat = document.getElementById("latInput").value;
      const long = document.getElementById("longInput").value;
      const radius = document.getElementById("radiusInput").value;
      if (!lat || !long || !radius) { alert("Please fill in all fields."); return; }
      console.log("Context set:", { lat, long, radius });
      // TODO: optionally POST to backend when endpoint is available
    });

    setupStickyDropdown("contextPill", "contextForm");
    setupStickyDropdown("paramPill", "paramForm");
  }

  // ---------- Boot ----------
  window.addEventListener("DOMContentLoaded", async () => {
    appendMessage("assistant", "Connecting...");
    let serverReady = false;

    for (let i = 0; i < 10; i++) {
      serverReady = await checkServer();
      if (serverReady) break;
      await sleep(1000);
    }

    document.getElementById("chat-history").innerHTML = "";
    if (serverReady) {
      try {
        const res = await fetch("http://localhost:8000/initial_greeting");
        const json = await res.json();
        appendMessage("assistant", json.response);
      } catch {
        appendMessage("assistant", "Couldn't fetch greeting, but the server seems up.");
      }
    } else {
      appendMessage("assistant", "Couldn't connect to the assistant.");
    }

    initChatControls();
    initUpload();
  });

  // ---------- 3D Graph (background) ----------
  // Renders or updates the 3D graph behind the chat
  // 3D graph as a live background with real 3D nodes and dynamic relaxation
// 3D graph as a live background with real 3D nodes and force-directed "wingy" motion
function showGraph3DBackground(dataGraph) {
  if (typeof ForceGraph3D !== "function") return;
  const mount = document.getElementById("graph3d");
  if (!mount) return;

  // Map {nodes, edges} -> {nodes, links}
  const nodes = (dataGraph.nodes || []).map(n => ({
    id: n.id,
    name: n.label || n.id,
    typology: n.typology || "",
    footprint: n.footprint || 0
  }));
  const links = (dataGraph.edges || []).map(e => ({
    source: e.source, target: e.target, type: e.type || "adjacent"
  }));

  // Degrees for distance scaling
  const deg = new Map(nodes.map(n => [n.id, 0]));
  links.forEach(l => {
    deg.set(l.source, (deg.get(l.source) || 0) + 1);
    deg.set(l.target, (deg.get(l.target) || 0) + 1);
  });

  // Init once
  if (!Graph3DInstance) {
    Graph3DInstance = ForceGraph3D()(mount)
      .backgroundColor("#f0f0f0ff")
      .cooldownTicks(100) // <-- keep the simulation running
      .d3VelocityDecay(0.12) // lower = floatier
      .nodeRelSize(4)
      .nodeOpacity(1)
      .nodeLabel(n => `${n.name}${n.typology ? " • " + n.typology : ""}`)
      .linkColor(() => "rgba(133, 133, 133, 0.85)")
      .enableNodeDrag(true)
      .nodeThreeObject(n => {
        const geom = new THREE.SphereGeometry(3.5, 16, 16);
        const mat  = new THREE.MeshLambertMaterial({ color: 0x6aa9ff });
        return new THREE.Mesh(geom, mat);
      });

    // Lights for Lambert shading
    const scene = Graph3DInstance.scene();
    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const dir = new THREE.DirectionalLight(0xffffff, 0.8);
    dir.position.set(1, 1, 1);
    scene.add(dir);
  }

  // Set data
  Graph3DInstance.graphData({ nodes, links });

  // Force tuning after data is set
  const charge = Graph3DInstance.d3Force('charge');
  if (charge && charge.strength) charge.strength(-160); // -120..-220 for more push

  const linkForce = Graph3DInstance.d3Force('link');
  if (linkForce && linkForce.distance && linkForce.strength) {
    linkForce
      .distance(l => {
        const s = l.source.id || l.source, t = l.target.id || l.target;
        const d = (deg.get(s) || 0) + (deg.get(t) || 0);
        return 40 + 8 * Math.sqrt(d); // 30..90
      })
      .strength(0.04); // 0.02..0.08 softer->springier
  }

  // Kick the solver
  try { Graph3DInstance.d3ReheatSimulation(); } catch {}

  // Fit view
  setTimeout(() => { try { Graph3DInstance.zoomToFit(800, 100); } catch {} }, 150);
}



})();
