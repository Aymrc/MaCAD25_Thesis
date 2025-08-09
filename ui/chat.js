// JS front-end script (clean: only LLM messages go to chat)

(function () {
  // ---------- Utilities ----------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function appendMessage(role, content) {
    const chat = document.getElementById("chat-history");
    const el = document.createElement("div");
    el.className = "message " + role;
    const html = DOMPurify.sanitize(marked.parse(content));
    el.innerHTML = html;
    chat.appendChild(el);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
  }

  function setStatus(text) {
    const s = document.getElementById("status");
    if (s) s.textContent = text || "";
    else console.log("[status]", text);
  }

  async function checkServer() {
    try {
      const res = await fetch("http://localhost:8000/initial_greeting?test=true", { credentials: "omit" });
      const json = await res.json();
      return json.dynamic === true;
    } catch {
      return false;
    }
  }

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
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        fileInput.click();
      }
    });

    async function handleUpload(e) {
      const file = e.target.files[0];
      if (!file || !file.name.toLowerCase().endsWith(".pdf")) {
        alert("Please upload a PDF file.");
        fileInput.value = "";
        setUploadEmptyState();
        return;
      }

      uploadLabel.textContent = "Brief: " + file.name;
      setUploadEmptyState();

      const formData = new FormData();
      formData.append("file", file);

      try {
        const res = await fetch("http://localhost:8000/upload_brief", {
          method: "POST",
          body: formData,
        });
        const data = await res.json();
        console.log("[brief] uploaded:", data);
        uploadLabel.textContent = "Brief uploaded";
      } catch (err) {
        console.error("[brief] upload failed", err);
        uploadLabel.textContent = "Upload failed";
      }
    }

    fileInput.addEventListener("change", handleUpload);
    setUploadEmptyState();
  }

  // ---------- OSM run + polling (silent to chat) ----------
  function toNumber(val) {
    var n = parseFloat((val || "").toString().trim());
    return isNaN(n) ? null : n;
  }

  function startOsm(lat, lon, radius_km) {
    return fetch("http://localhost:8000/osm/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: lat, lon: lon, radius_km: radius_km })
    }).then(function (r) { return r.json(); });
  }

  function pollStatus(jobId) {
    return fetch("http://localhost:8000/osm/status/" + jobId)
      .then(function (r) { return r.json(); });
  }

  function initChatControls() {
    document.getElementById("sendBtn").addEventListener("click", sendMessage);
    document.getElementById("chatInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter") sendMessage();
    });

    // Context setter -> Start OSM job and poll status (NO chat messages)
    document.getElementById("saveContextBtn").addEventListener("click", function () {
      var lat = toNumber(document.getElementById("latInput").value);
      var lon = toNumber(document.getElementById("longInput").value);
      var radius = toNumber(document.getElementById("radiusInput").value);

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

    setupStickyDropdown("contextPill", "contextForm");
    setupStickyDropdown("paramPill", "paramForm");
  }

  async function sendMessage() {
    const input = document.getElementById("chatInput");
    const text = input.value.trim();
    if (!text) return;

    appendMessage("user", text);
    input.value = "";

    // Temporary typing indicator
    appendMessage("assistant", "...");
    const chatbox = document.getElementById("chat-history");

    try {
      const res = await fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      const json = await res.json();
      const botReply = json.response || "No reply.";
      chatbox.removeChild(chatbox.lastChild);
      appendMessage("assistant", botReply);
    } catch (err) {
      console.error(err);
      chatbox.removeChild(chatbox.lastChild);
      appendMessage("assistant", "Error contacting the assistant.");
    }
  }

  // ---------- Boot ----------
  window.addEventListener("DOMContentLoaded", async () => {
    // No "Connecting..." message in chat
    let serverReady = false;
    for (let i = 0; i < 10; i++) {
      serverReady = await checkServer();
      if (serverReady) break;
      await sleep(1000);
    }

    document.getElementById("chat-history").innerHTML = "";

    // Only show LLM greeting if available
    if (serverReady) {
      try {
        const res = await fetch("http://localhost:8000/initial_greeting");
        const json = await res.json();
        if (json && json.response) appendMessage("assistant", json.response);
      } catch (e) {
        console.log("[greeting] failed", e);
      }
    }

    initChatControls();
    initUpload();
  });
})();
