// JS front-end script

(function () {
  // ---------- Utilities ----------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function appendMessage(role, content) {
    const chat = document.getElementById("chat-history");
    const el = document.createElement("div");
    el.className = `message ${role}`;

    // Render Markdown
    const html = DOMPurify.sanitize(marked.parse(content));
    el.innerHTML = html;

    chat.appendChild(el);
    chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
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

    // Keyboard: open on focus, close on blur
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

    // Open file picker when clicking the pill or pressing Enter/Space
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

      uploadLabel.textContent = `Brief: ${file.name}`;
      setUploadEmptyState();

      const formData = new FormData();
      formData.append("file", file);

      try {
        const res = await fetch("http://localhost:8000/upload_brief", {
          method: "POST",
          body: formData,
        });
        const data = await res.json();

        if (data.chat_notice) appendMessage("assistant", data.chat_notice);
        uploadLabel.textContent = "Brief uploaded";

        console.log("Uploaded:", data);
        uploadLabel.textContent = "Brief uploaded";
      } catch (err) {
        console.error("Upload failed", err);
        uploadLabel.textContent = "Upload failed";
        // Optional: reset state on failure
        // fileInput.value = "";
        // setUploadEmptyState();
      }
    }

    fileInput.addEventListener("change", handleUpload);
    setUploadEmptyState();
  }

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
      const botReply = json.response || "No reply.";
      chatbox.removeChild(chatbox.lastChild);
      appendMessage("assistant", botReply);
    } catch (err) {
      console.error(err);
      chatbox.removeChild(chatbox.lastChild);
      appendMessage("assistant", "Error contacting the assistant.");
    }
  }

  function initChatControls() {
    document.getElementById("sendBtn").addEventListener("click", sendMessage);
    document.getElementById("chatInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter") sendMessage();
    });

    // Context setter
    document.getElementById("saveContextBtn").addEventListener("click", () => {
      const lat = document.getElementById("latInput").value;
      const long = document.getElementById("longInput").value;
      const radius = document.getElementById("radiusInput").value;
      if (!lat || !long || !radius) {
        alert("Please fill in all fields.");
        return;
      }
      console.log("📍 Context set:", { lat, long, radius });
      // TODO: Optionally POST this to backend when endpoint is available.
    });

    // Dropdowns
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

    // Clear placeholder and greet
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
})();