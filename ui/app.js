// Visual 

// === Tab switching (visual) ===
document.querySelectorAll(".tab button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");

    const tab = btn.dataset.tab;
    if (tab === "brief") {
      // show brief graph if we have one
      if (window._briefGraph && typeof window.showGraph3DBackground === "function") {
        window.showGraph3DBackground(window._briefGraph);
      }
    } else {
      // other tabs (empty for now !!! WIP)
      if (typeof window.clearGraph === "function") window.clearGraph();
    }
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