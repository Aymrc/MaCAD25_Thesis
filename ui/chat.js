window.addEventListener("load", async () => {
  console.log("Page loaded, initializing chat.js...");

  const chatbox = document.getElementById("chat-history");
  appendMessage("assistant", "Connecting...");

  // --- Try to connect to LLM server ---
  async function checkServer() {
    try {
      const res = await fetch("http://localhost:8000/initial_greeting?test=true");
      const json = await res.json();
      return json.dynamic === true;
    } catch {
      return false;
    }
  }

  let serverReady = false;
  for (let i = 0; i < 10; i++) {
    serverReady = await checkServer();
    if (serverReady) break;
    await new Promise(resolve => setTimeout(resolve, 1000));
  }

  chatbox.innerHTML = "";
  if (serverReady) {
  console.log("Connected to Rhino server"); 
} else {
  appendMessage("assistant", "Couldn't connect to the assistant.");
}

  // --- Attach button event handlers after DOM is loaded ---

  // Send message on click or Enter
  const sendBtn = document.getElementById("sendBtn");
  const chatInput = document.getElementById("chatInput");

  if (sendBtn && chatInput) {
    sendBtn.addEventListener("click", sendMessage);
    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") sendMessage();
    });
  }

  // Handle Set button
  const saveContextBtn = document.getElementById("saveContextBtn");
  if (saveContextBtn) {
    console.log("Attaching click handler to Set button");

    saveContextBtn.addEventListener("click", () => {
  console.log("Set button clicked");

  const lat = document.getElementById("latInput").value;
  const long = document.getElementById("longInput").value;
  const radius = document.getElementById("radiusInput").value;

  console.log("Context set:", { lat, long, radius });

  fetch("http://localhost:8000/run_context_script", { method: "POST" })
    .then(res => {
      console.log("Server responded:", res.status, res.statusText);
      return res.json();
    })
    .then(data => {
      console.log("Script executed:", data); 
    })
    .catch(err => {
      console.error("Fetch error:", err);
    });
});

  }

  // Handle file upload
  const fileInput = document.getElementById("brief-upload");
  const uploadLabel = document.getElementById("uploadLabel");

  if (fileInput) {
    fileInput.addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (!file || !file.name.endsWith(".pdf")) {
        alert("Please upload a PDF file.");
        return;
      }

      uploadLabel.textContent = `Brief: ${file.name}`;

      const formData = new FormData();
      formData.append("file", file);

      fetch("http://localhost:8000/upload_brief", {
        method: "POST",
        body: formData
      })
        .then(res => res.json())
        .then(data => {
          console.log("Uploaded:", data);
          uploadLabel.textContent = "Brief uploaded";
        })
        .catch(err => {
          console.error("Upload failed", err);
          uploadLabel.textContent = "Upload failed";
        });
    });
  }

  // Initialize dropdowns
  setupStickyDropdown("contextPill", "contextForm");
  setupStickyDropdown("paramPill", "paramForm");
});

// --- Chat send function ---
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
      body: JSON.stringify({ message: text })
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

// --- Append message to chat history ---
function appendMessage(role, content) {
  const chat = document.getElementById("chat-history");
  const el = document.createElement("div");
  el.className = `message ${role}`;
  el.textContent = content;
  chat.appendChild(el);
  chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
}

// --- Sticky dropdown utility ---
function setupStickyDropdown(pillId, dropdownId) {
  const pill = document.getElementById(pillId);
  const dropdown = document.getElementById(dropdownId);

  if (!pill || !dropdown) return;

  let hideTimeout;

  const showDropdown = () => {
    clearTimeout(hideTimeout);
    dropdown.style.display = "block";
  };

  const hideDropdown = () => {
    hideTimeout = setTimeout(() => {
      dropdown.style.display = "none";
    }, 250);
  };

  pill.addEventListener("mouseenter", showDropdown);
  dropdown.addEventListener("mouseenter", showDropdown);

  pill.addEventListener("mouseleave", hideDropdown);
  dropdown.addEventListener("mouseleave", hideDropdown);
}
