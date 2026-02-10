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
const negativeImage = document.getElementById("negativeImage");
const debugLabel   = document.getElementById("debugLabel");
const debugImageSelect = document.getElementById("debugImageSelect");
const viewerEl     = document.getElementById("viewer");
const modelLabel   = document.getElementById("modelLabel");

const tabBtns = document.querySelectorAll(".tab-btn");
const progressSection = document.getElementById("progressSection");
const progressLabel = document.getElementById("progressLabel");
const progressFill = document.getElementById("progressFill");
let currentView = "outline";

// ── Progress bar helpers ──────────────────────────────────────────

const PROGRESS_STAGES = {
  "Validating outline...": 5,
  "Placing components & routing traces...": 15,
  "Generating enclosure...": 70,
  "Compiling STL models...": 85,
  "Pipeline complete!": 100,
};

function updateProgress(stage) {
  progressSection.style.display = "block";
  progressLabel.textContent = stage;

  // Check for exact match first
  if (PROGRESS_STAGES[stage] !== undefined) {
    progressFill.style.width = PROGRESS_STAGES[stage] + "%";
    return;
  }

  // Check for screening/routing patterns
  const screenMatch = stage.match(/Screening placement (\d+)\/(\d+)/);
  if (screenMatch) {
    const current = parseInt(screenMatch[1]);
    const total = parseInt(screenMatch[2]);
    // Screening goes from 15% to 55%
    const pct = 15 + (current / total) * 40;
    progressFill.style.width = pct + "%";
    return;
  }

  const thoroughMatch = stage.match(/Thorough routing placement/);
  if (thoroughMatch) {
    // Thorough routing is 55-70%
    progressFill.style.width = "60%";
    return;
  }
}

function hideProgress() {
  progressSection.style.display = "none";
  progressFill.style.width = "0%";
}

// ── Debug log ─────────────────────────────────────────────────────
// DEBUG: This section is to be removed later

const debugLog = document.getElementById("debugLog");
const debugToggleBtn = document.getElementById("debugToggleBtn");

function logDebug(msg) {
  console.log(`[DEBUG] ${msg}`);
  if (debugLog) {
    const line = document.createElement("div");
    line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    debugLog.appendChild(line);
    debugLog.scrollTop = debugLog.scrollHeight;
  }
}

// DEBUG: Toggle switch handler - TO BE REMOVED
if (debugToggleBtn && debugLog) {
  debugToggleBtn.addEventListener("change", () => {
    debugLog.style.display = debugToggleBtn.checked ? "block" : "none";
  });
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
  if (statusBadge) {
    statusBadge.textContent = text;
    statusBadge.className = `status ${type || ""}`;
  }
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
  outlineLabel.style.display = "none";  // Hide label when outline is rendered
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

    // Hide placeholder label
    if (modelLabel) modelLabel.style.display = "none";

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

// ── Image zoom and drag state ─────────────────────────────────────

let debugZoom = 1;
let outlineZoom = 1;

const imageDragStates = {
  debug: { translateX: 0, translateY: 0 },
  negative: { translateX: 0, translateY: 0 },
  outline: { translateX: 0, translateY: 0 }
};

function applyImageTransform(img, zoom, dragState) {
  img.style.transform = `scale(${zoom}) translate(${dragState.translateX / zoom}px, ${dragState.translateY / zoom}px)`;
  img.style.transformOrigin = "center center";
}

function applyOutlineTransform() {
  const state = imageDragStates.outline;
  outlineSvg.style.transform = `scale(${outlineZoom}) translate(${state.translateX / outlineZoom}px, ${state.translateY / outlineZoom}px)`;
  outlineSvg.style.transformOrigin = "center center";
}

// Debug view reset
document.getElementById("resetDebugView").addEventListener("click", () => {
  debugZoom = 1;
  imageDragStates.debug.translateX = 0;
  imageDragStates.debug.translateY = 0;
  imageDragStates.negative.translateX = 0;
  imageDragStates.negative.translateY = 0;
  applyImageTransform(debugImage, debugZoom, imageDragStates.debug);
  applyImageTransform(negativeImage, debugZoom, imageDragStates.negative);
});

// Outline view reset
document.getElementById("resetOutlineView").addEventListener("click", () => {
  outlineZoom = 1;
  imageDragStates.outline.translateX = 0;
  imageDragStates.outline.translateY = 0;
  applyOutlineTransform();
});

// Debug image dropdown toggle
debugImageSelect.addEventListener("change", () => {
  // Only toggle images if content has been loaded (label is hidden)
  if (debugLabel && debugLabel.style.display === "none") {
    if (debugImageSelect.value === "debug") {
      debugImage.style.display = "";
      negativeImage.style.display = "none";
    } else {
      debugImage.style.display = "none";
      negativeImage.style.display = "";
    }
  }
});

// ── Zoom button handlers ──────────────────────────────────────────

document.querySelectorAll(".zoom-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.view;
    const action = btn.dataset.action;
    
    if (view === "debug") {
      if (action === "in") {
        debugZoom = Math.min(debugZoom * 1.25, 5);
      } else {
        debugZoom = Math.max(debugZoom / 1.25, 0.2);
      }
      applyImageTransform(debugImage, debugZoom, imageDragStates.debug);
      applyImageTransform(negativeImage, debugZoom, imageDragStates.negative);
    } else if (view === "outline") {
      if (action === "in") {
        outlineZoom = Math.min(outlineZoom * 1.25, 5);
      } else {
        outlineZoom = Math.max(outlineZoom / 1.25, 0.2);
      }
      applyOutlineTransform();
    }
  });
});

// ── Wheel zoom for views ──────────────────────────────────────────

debugView.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (e.deltaY < 0) {
    debugZoom = Math.min(debugZoom * 1.1, 5);
  } else {
    debugZoom = Math.max(debugZoom / 1.1, 0.2);
  }
  applyImageTransform(debugImage, debugZoom, imageDragStates.debug);
  applyImageTransform(negativeImage, debugZoom, imageDragStates.negative);
}, { passive: false });

outlineView.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (e.deltaY < 0) {
    outlineZoom = Math.min(outlineZoom * 1.1, 5);
  } else {
    outlineZoom = Math.max(outlineZoom / 1.1, 0.2);
  }
  applyOutlineTransform();
}, { passive: false });

// ── Drag functionality for images ─────────────────────────────────

function setupImageDrag(element, stateKey, getZoom, applyFn) {
  const state = imageDragStates[stateKey];
  let isDragging = false;
  let startX = 0;
  let startY = 0;
  
  element.addEventListener("mousedown", (e) => {
    e.preventDefault();
    isDragging = true;
    startX = e.clientX - state.translateX;
    startY = e.clientY - state.translateY;
    element.style.cursor = "grabbing";
  });
  
  document.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    state.translateX = e.clientX - startX;
    state.translateY = e.clientY - startY;
    applyFn(element, getZoom(), state);
  });
  
  document.addEventListener("mouseup", () => {
    if (isDragging) {
      isDragging = false;
      element.style.cursor = "grab";
    }
  });
  
  // Double-click to reset position
  element.addEventListener("dblclick", () => {
    state.translateX = 0;
    state.translateY = 0;
    applyFn(element, getZoom(), state);
  });
  
  element.style.cursor = "grab";
}

// Setup drag for debug images
setupImageDrag(debugImage, "debug", () => debugZoom, applyImageTransform);
setupImageDrag(negativeImage, "negative", () => debugZoom, applyImageTransform);

// Setup drag for outline SVG
setupImageDrag(outlineSvg, "outline", () => outlineZoom, (el, zoom, state) => {
  el.style.transform = `scale(${zoom}) translate(${state.translateX / zoom}px, ${state.translateY / zoom}px)`;
  el.style.transformOrigin = "center center";
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
    outlineLabel.style.display = "";
    // Reset debug view
    debugImage.src = "";
    debugImage.style.display = "none";
    negativeImage.src = "";
    negativeImage.style.display = "none";
    if (debugLabel) {
      debugLabel.textContent = "No PCB layout yet";
      debugLabel.style.display = "";
    }
    debugImageSelect.value = "debug";
    // Reset 3D view
    if (currentMesh) {
      scene.remove(currentMesh);
      currentMesh.geometry.dispose();
      currentMesh.material.dispose();
      currentMesh = null;
    }
    if (modelLabel) {
      modelLabel.textContent = "No 3D model yet";
      modelLabel.style.display = "";
    }
    latestModelName = null;
    downloadBtn.classList.add("disabled");
    hideProgress();
    // Reset zoom and drag states
    debugZoom = 1;
    outlineZoom = 1;
    imageDragStates.debug.translateX = 0;
    imageDragStates.debug.translateY = 0;
    imageDragStates.negative.translateX = 0;
    imageDragStates.negative.translateY = 0;
    imageDragStates.outline.translateX = 0;
    imageDragStates.outline.translateY = 0;
    applyImageTransform(debugImage, 1, imageDragStates.debug);
    applyImageTransform(negativeImage, 1, imageDragStates.negative);
    applyOutlineTransform();
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
  progressFill.style.width = "0%";
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
            negativeImage.src = `/api/images/negative?t=${Date.now()}`;
            // Show debug image, hide label
            debugImage.style.display = "";
            if (debugLabel) debugLabel.style.display = "none";
            debugImageSelect.value = "debug";
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
            updateProgress(ev.stage || "Working…");
            break;

          case "error":
            logDebug(`ERROR event: ${ev.message}`);
            addMessage("assistant", `Error: ${ev.message}`);
            setStatus("Error", "error");
            hideProgress();
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
    hideProgress();
  } finally {
    logDebug(`Finally block — re-enabling send button`);
    sendBtn.disabled = false;
    if (statusBadge?.textContent === "Generating…") setStatus("Ready", "");
    // Hide progress bar after a short delay if complete
    setTimeout(() => {
      if (progressFill.style.width === "100%") hideProgress();
    }, 1500);
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

// ── Speech-to-text (dictation) ────────────────────────────────────

const micBtn = document.getElementById("micBtn");
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

if (SpeechRecognition && micBtn) {
  const recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = "en-US";
  
  let isListening = false;
  let finalTranscript = "";
  let textBeforeDictation = "";
  let isUpdatingFromSpeech = false;
  
  // Detect manual edits during dictation and reset transcript
  promptInput.addEventListener("input", () => {
    if (isListening && !isUpdatingFromSpeech) {
      // User manually edited - reset and use current text as new base
      finalTranscript = "";
      textBeforeDictation = promptInput.value.replace(/\s*\[.*\]$/, "").trim();
    }
  });
  
  micBtn.addEventListener("click", () => {
    if (isListening) {
      recognition.stop();
    } else {
      finalTranscript = "";
      textBeforeDictation = promptInput.value.trim();
      recognition.start();
    }
  });
  
  recognition.addEventListener("start", () => {
    isListening = true;
    micBtn.classList.add("listening");
    micBtn.title = "Listening... Click to stop";
  });
  
  recognition.addEventListener("end", () => {
    isListening = false;
    micBtn.classList.remove("listening");
    micBtn.title = "Click to dictate";
    // Clean up any interim markers
    promptInput.value = promptInput.value.replace(/\s*\[.*\]$/, "").trim();
  });
  
  recognition.addEventListener("result", (e) => {
    let interimTranscript = "";
    
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const transcript = e.results[i][0].transcript;
      if (e.results[i].isFinal) {
        finalTranscript += transcript + " ";
      } else {
        interimTranscript += transcript;
      }
    }
    
    // Build the full text: original text + transcribed text
    const prefix = textBeforeDictation ? textBeforeDictation + " " : "";
    
    isUpdatingFromSpeech = true;
    if (interimTranscript) {
      promptInput.value = prefix + finalTranscript + "[" + interimTranscript + "]";
    } else {
      promptInput.value = prefix + finalTranscript.trim();
    }
    isUpdatingFromSpeech = false;
  });
  
  recognition.addEventListener("error", (e) => {
    console.error("Speech recognition error:", e.error);
    isListening = false;
    micBtn.classList.remove("listening");
    micBtn.title = "Click to dictate";
    
    if (e.error === "not-allowed") {
      addMessage("assistant", "Microphone access denied. Please allow microphone access in your browser settings.");
    }
  });
} else if (micBtn) {
  // Browser doesn't support speech recognition
  micBtn.style.display = "none";
  console.warn("Speech recognition not supported in this browser");
}
