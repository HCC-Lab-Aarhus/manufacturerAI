/* ── ManufacturerAI — app.js ────────────────────────────────────── */

const chatEl      = document.getElementById("chat");
const promptInput = document.getElementById("promptInput");
const sendBtn     = document.getElementById("sendBtn");
const statusBadge = document.getElementById("statusBadge");
const downloadBtn = document.getElementById("downloadBtn");
const resetBtn    = document.getElementById("resetBtn");

const outlineView  = document.getElementById("outlineView");
const outlineSvg   = document.getElementById("outlineSvg");
const outlineLabel = document.getElementById("outlineLabel");
const debugView    = document.getElementById("debugView");
const debugImage   = document.getElementById("debugImage");
const viewerEl     = document.getElementById("viewer");

const tabBtns = document.querySelectorAll(".tab-btn");
let currentView = "outline";

// ── Debug log ─────────────────────────────────────────────────────

const debugLog = document.getElementById("debugLog");

function logDebug(msg) {
  console.log(`[DEBUG] ${msg}`);
  if (debugLog) {
    const line = document.createElement("div");
    line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    debugLog.appendChild(line);
    debugLog.scrollTop = debugLog.scrollHeight;
  }
}

// ── Tab switching ─────────────────────────────────────────────────

function switchTab(view) {
  currentView = view;
  tabBtns.forEach(b => b.classList.toggle("active", b.dataset.view === view));
  outlineView.classList.toggle("active", view === "outline");
  debugView.classList.toggle("active", view === "debug");
  viewerEl.classList.toggle("active", view === "3d");
  if (view === "3d") setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
}

tabBtns.forEach(btn => btn.addEventListener("click", () => switchTab(btn.dataset.view)));

// ── MutationObserver on chat — catch anything that removes/clears nodes ──

const _chatObserver = new MutationObserver((mutations) => {
  for (const m of mutations) {
    if (m.removedNodes.length) {
      for (const node of m.removedNodes) {
        console.warn("[MUTATION] Removed node from chat:", node.className, node.textContent?.slice(0, 80));
        logDebug(`⚠ DOM REMOVED: <${node.tagName}> .${node.className} "${(node.textContent||'').slice(0,60)}"`);
      }
      console.trace("[MUTATION] Stack trace for removal:");
    }
    if (m.type === "childList" && m.addedNodes.length) {
      for (const node of m.addedNodes) {
        if (node.nodeType === 1) {
          logDebug(`✓ DOM ADDED: <${node.tagName}> .${node.className} "${(node.textContent||'').slice(0,60)}"`);
        }
      }
    }
    if (m.type === "characterData") {
      logDebug(`⚠ TEXT CHANGED on .${m.target.parentElement?.className}: "${(m.target.textContent||'').slice(0,60)}"`);
    }
  }
});
_chatObserver.observe(chatEl, { childList: true, subtree: true, characterData: true });

// ── Chat helpers ──────────────────────────────────────────────────

function addMessage(role, text) {
  if (!text) return;                       // never add empty messages
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.textContent = text;
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function addThinking(text) {
  const div = document.createElement("div");
  div.className = "message thinking";
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Agent thinking…";
  details.appendChild(summary);
  const body = document.createElement("p");
  body.textContent = text;
  details.appendChild(body);
  div.appendChild(details);
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function addToolCall(name) {
  const div = document.createElement("div");
  div.className = "message tool-call";
  div.textContent = `⚙ ${name}`;
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function setStatus(text, type) {
  statusBadge.textContent = text;
  statusBadge.className = `status ${type || ""}`;
}

// ── Outline SVG rendering ─────────────────────────────────────────

function renderOutline(outline, buttons, label) {
  if (!outline || outline.length < 3) return;

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const [x, y] of outline) {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  const pad = 10;
  const vw = maxX - minX + pad * 2;
  const vh = maxY - minY + pad * 2;
  outlineSvg.setAttribute("viewBox", `${minX - pad} ${minY - pad} ${vw} ${vh}`);
  outlineSvg.innerHTML = "";

  const pts = outline.map(([x, y]) => `${x},${y}`).join(" ");
  const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
  poly.setAttribute("points", pts);
  poly.setAttribute("fill", "rgba(59,130,246,0.15)");
  poly.setAttribute("stroke", "#3b82f6");
  poly.setAttribute("stroke-width", String(Math.max(0.5, vw / 200)));
  outlineSvg.appendChild(poly);

  const btnRadius = 3;
  const fontSize = Math.max(2, vw / 60);
  for (const btn of (buttons || [])) {
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("cx", btn.x);
    c.setAttribute("cy", btn.y);
    c.setAttribute("r", btnRadius);
    c.setAttribute("fill", "rgba(239,68,68,0.6)");
    c.setAttribute("stroke", "#ef4444");
    c.setAttribute("stroke-width", "0.5");
    outlineSvg.appendChild(c);

    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", btn.x);
    t.setAttribute("y", btn.y - btnRadius - 1.5);
    t.setAttribute("text-anchor", "middle");
    t.setAttribute("fill", "#e5e7eb");
    t.setAttribute("font-size", fontSize);
    t.textContent = btn.label || btn.id;
    outlineSvg.appendChild(t);
  }

  outlineLabel.textContent = label || "Outline preview";
  switchTab("outline");
}

// ── Three.js setup ────────────────────────────────────────────────

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b1120);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 2000);
camera.position.set(0, -140, 120);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(viewerEl.clientWidth || 400, viewerEl.clientHeight || 400);
viewerEl.appendChild(renderer.domElement);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;

scene.add(new THREE.AmbientLight(0xffffff, 0.6));
const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
dirLight.position.set(200, -100, 200);
scene.add(dirLight);

let currentMesh = null;

function loadModel(url) {
  const loader = new THREE.STLLoader();
  loader.load(url, (geometry) => {
    if (currentMesh) {
      scene.remove(currentMesh);
      currentMesh.geometry.dispose();
      currentMesh.material.dispose();
    }
    const mat = new THREE.MeshStandardMaterial({ color: 0x93c5fd, metalness: 0.1, roughness: 0.5 });
    const mesh = new THREE.Mesh(geometry, mat);
    geometry.computeBoundingBox();
    geometry.center();
    mesh.rotation.x = Math.PI / 2;
    scene.add(mesh);
    currentMesh = mesh;

    const box = new THREE.Box3().setFromObject(mesh);
    const size = new THREE.Vector3();
    box.getSize(size);
    const d = Math.max(size.x, size.y, size.z) * 1.7;
    camera.position.set(0, -d, d * 0.7);
    controls.target.set(0, 0, 0);
    controls.update();

    downloadBtn.classList.remove("disabled");
    switchTab("3d");
  });
}

(function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
})();

window.addEventListener("resize", () => {
  const w = viewerEl.clientWidth;
  const h = viewerEl.clientHeight;
  if (w && h) {
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }
});

// 3D zoom
document.getElementById("zoomIn").addEventListener("click", () => {
  const d = new THREE.Vector3(); camera.getWorldDirection(d);
  camera.position.addScaledVector(d, 20); controls.update();
});
document.getElementById("zoomOut").addEventListener("click", () => {
  const d = new THREE.Vector3(); camera.getWorldDirection(d);
  camera.position.addScaledVector(d, -20); controls.update();
});
document.getElementById("reset3DView").addEventListener("click", () => {
  camera.position.set(0, -140, 120);
  controls.target.set(0, 0, 0);
  controls.update();
});

// Debug view reset
document.getElementById("resetDebugView").addEventListener("click", () => {
  debugImage.style.transform = "";
});

// Download
let latestModelName = null;
downloadBtn.addEventListener("click", () => {
  if (downloadBtn.classList.contains("disabled") || !latestModelName) return;
  window.location.href = `/api/model/download/${latestModelName}`;
});

// ── Reset session ─────────────────────────────────────────────────

if (resetBtn) {
  resetBtn.addEventListener("click", async () => {
    await fetch("/api/reset", { method: "POST" });
    chatEl.innerHTML = "";
    setStatus("Ready", "");
    outlineSvg.innerHTML = "";
    outlineLabel.textContent = "No outline yet";
    latestModelName = null;
    downloadBtn.classList.add("disabled");
  });
}

// ── SSE streaming ─────────────────────────────────────────────────

promptInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendBtn.click(); }
});

sendBtn.addEventListener("click", async () => {
  const msg = promptInput.value.trim();
  if (!msg) return;

  addMessage("user", msg);
  promptInput.value = "";
  sendBtn.disabled = true;
  setStatus("Generating…", "working");
  logDebug(`Sending: "${msg}"`);

  try {
    const res = await fetch("/api/generate/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg }),
    });

    logDebug(`HTTP status: ${res.status}`);

    if (!res.ok) {
      const err = await res.text();
      logDebug(`HTTP error body: ${err}`);
      addMessage("assistant", `Error: ${err}`);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let eventCount = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        logDebug(`Stream ended (done=true). Total events: ${eventCount}`);
        break;
      }
      const chunk = decoder.decode(value, { stream: true });
      logDebug(`Chunk received (${chunk.length} chars)`);
      buf += chunk;

      const lines = buf.split("\n");
      buf = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const jsonStr = line.slice(6).trim();
        if (!jsonStr) continue;

        let ev;
        try { ev = JSON.parse(jsonStr); } catch (e) {
          logDebug(`JSON parse error: ${e.message} — raw: ${jsonStr.slice(0,200)}`);
          continue;
        }

        eventCount++;
        logDebug(`Event #${eventCount}: type=${ev.type} ${ev.type === 'chat' ? `role=${ev.role} text="${(ev.text||'').slice(0,80)}"` : JSON.stringify(ev).slice(0,120)}`);

        switch (ev.type) {
          case "thinking":
            addThinking(ev.text);
            break;

          case "chat":
            if (ev.role === "assistant") {
              logDebug(`Adding assistant message (${(ev.text||'').length} chars)`);
              addMessage("assistant", ev.text);
              logDebug(`Chat element children: ${chatEl.children.length}`);
            }
            break;

          case "outline_preview":
            renderOutline(ev.outline, ev.buttons, ev.label);
            break;

          case "debug_image":
            debugImage.src = `/api/images/${ev.label}?t=${Date.now()}`;
            switchTab("debug");
            break;

          case "model":
            latestModelName = ev.name;
            loadModel(`/api/model/${ev.name}?t=${Date.now()}`);
            break;

          case "tool_call":
            addToolCall(ev.name);
            break;

          case "tool_error":
            addMessage("assistant", `Tool error (${ev.name}): ${ev.error}`);
            break;

          case "progress":
            setStatus(ev.stage || "Working…", "working");
            break;

          case "error":
            logDebug(`ERROR event: ${ev.message}`);
            addMessage("assistant", `Error: ${ev.message}`);
            setStatus("Error", "error");
            break;

          default:
            logDebug(`Unknown event type: ${ev.type}`);
            break;
        }
      }
    }
  } catch (err) {
    logDebug(`Fetch/stream exception: ${err.message}\n${err.stack}`);
    addMessage("assistant", `Network error: ${err.message}`);
    setStatus("Error", "error");
  } finally {
    logDebug(`Finally block — re-enabling send button`);
    sendBtn.disabled = false;
    if (statusBadge.textContent === "Generating…") setStatus("Ready", "");
  }
});

// ── Panel resizer ─────────────────────────────────────────────────

const resizer = document.getElementById("resizer");
const appEl   = document.querySelector(".app");
let resizing  = false;

resizer.addEventListener("mousedown", () => {
  resizing = true;
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";
});

document.addEventListener("mousemove", (e) => {
  if (!resizing) return;
  const rect = appEl.getBoundingClientRect();
  const lw = Math.max(200, Math.min(rect.width * 0.6, e.clientX - rect.left));
  const rw = rect.width - lw - 6;
  appEl.style.gridTemplateColumns = `${lw}px 6px ${rw}px`;
  window.dispatchEvent(new Event("resize"));
});

document.addEventListener("mouseup", () => {
  if (resizing) { resizing = false; document.body.style.cursor = ""; document.body.style.userSelect = ""; }
});
