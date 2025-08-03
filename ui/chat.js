window.addEventListener("load", async () => {
  const chatbox = document.getElementById("chat-history");
  appendMessage("assistant", "Connecting...");

  // Try to connect to LLM server
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
    const res = await fetch("http://localhost:8000/initial_greeting");
    const json = await res.json();
    appendMessage("assistant", json.response);
  } else {
    appendMessage("assistant", "Couldn't connect to the assistant.");
  }

  setupStickyDropdown("contextPill", "contextForm");
  setupStickyDropdown("paramPill", "paramForm");
});

// Chat send
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

// Add message to chat history
function appendMessage(role, content) {
  const chat = document.getElementById("chat-history");
  const el = document.createElement("div");
  el.className = `message ${role}`;
  el.textContent = content;
  chat.appendChild(el);
  chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
}

// Send on button or Enter
document.getElementById("sendBtn").addEventListener("click", sendMessage);
document.getElementById("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage();
});

// Handle context button
document.getElementById("saveContextBtn").addEventListener("click", () => {
  const lat = document.getElementById("latInput").value;
  const long = document.getElementById("longInput").value;
  const radius = document.getElementById("radiusInput").value;

  if (!lat || !long || !radius) {
    alert("Please fill in all fields.");
    return;
  }

  console.log("📍 Context set:", { lat, long, radius });
  // Optionally send to backend
});

// Upload logic (single source of truth)
const fileInput = document.getElementById("brief-upload");
const uploadLabel = document.getElementById("uploadLabel");

fileInput.addEventListener("change", handleUpload);

function handleUpload(e) {
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
}

// Sticky dropdown logic
function setupStickyDropdown(pillId, dropdownId) {
  const pill = document.getElementById(pillId);
  const dropdown = document.getElementById(dropdownId);

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
  dropdown.addEventListener("
