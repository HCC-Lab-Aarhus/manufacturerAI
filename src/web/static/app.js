/* ── ManufacturerAI — app.js ────────────────────────────────────── */

const chatEl      = document.getElementById("chat");
const promptInput = document.getElementById("promptInput");
const sendBtn     = document.getElementById("sendBtn");
const statusBadge = document.getElementById("statusBadge");
const downloadBtn = document.getElementById("downloadBtn");
const resetBtn    = document.getElementById("resetBtn");

// Ready to print / Geocode elements
const readyToPrintBtn   = document.getElementById("readyToPrintBtn");
const geocodeOverlay    = document.getElementById("geocodeOverlay");
const geocodeStatus     = document.getElementById("geocodeStatus");
const geocodeDownloadBtn = document.getElementById("geocodeDownloadBtn");
const geocodeBackBtn    = document.getElementById("geocodeBackBtn");
const stepByStepBtn     = document.getElementById("stepByStepBtn");
const stepByStepScreen  = document.getElementById("stepByStepScreen");
const backToDesignBtn   = document.getElementById("backToDesignBtn");
const backToGcodeBtn    = document.getElementById("backToGcodeBtn");

// Step-by-step guide elements
const toggleQuestionWindowBtn = document.getElementById("toggleQuestionWindowBtn");
const questionSidebar     = document.getElementById("questionSidebar");
const guidePromptInput    = document.getElementById("guidePromptInput");
const guideAskBtn         = document.getElementById("guideAskBtn");
const guideResponse       = document.getElementById("guideResponse");
const guidePrevBtn        = document.getElementById("guidePrevBtn");
const guideNextBtn        = document.getElementById("guideNextBtn");

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
  "Placing components & routing traces...": 10,
  "Optimizing component placement...": 15,
  "Routing traces...": 30,
  "Generating enclosure...": 70,
  "Compiling STL models...": 85,
  "Pipeline complete!": 100,
};

let _lastProgressPct = 0;

function updateProgress(stage) {
  progressSection.style.display = "block";
  progressLabel.textContent = stage;

  if (PROGRESS_STAGES[stage] !== undefined) {
    const pct = PROGRESS_STAGES[stage];
    // Never go backwards
    if (pct >= _lastProgressPct) {
      _lastProgressPct = pct;
      progressFill.style.width = pct + "%";
    }
  }
}

function hideProgress() {
  progressSection.style.display = "none";
  progressFill.style.width = "0%";
  _lastProgressPct = 0;
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

// ── Render outline with placed components ─────────────────────────

const COMP_COLORS = {
  battery:    { fill: "rgba(234,179,8,0.15)",  stroke: "#eab308" },
  controller: { fill: "rgba(16,185,129,0.15)", stroke: "#10b981" },
  diode:      { fill: "rgba(168,85,247,0.15)", stroke: "#a855f7" },
  button:     { fill: "rgba(239,68,68,0.15)",  stroke: "#ef4444" },
};
const DEFAULT_COMP_COLOR = { fill: "rgba(148,163,184,0.15)", stroke: "#94a3b8" };

function renderOutlineWithComponents(layout) {
  const outline = layout.board && layout.board.outline_polygon;
  const components = layout.components || [];
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

  // Polygon outline
  const pts = outline.map(([x, y]) => `${x},${y}`).join(" ");
  const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
  poly.setAttribute("points", pts);
  poly.setAttribute("fill", "rgba(59,130,246,0.10)");
  poly.setAttribute("stroke", "#3b82f6");
  poly.setAttribute("stroke-width", String(Math.max(0.5, vw / 200)));
  outlineSvg.appendChild(poly);

  const fontSize = Math.max(2, vw / 60);
  const sw = Math.max(0.3, vw / 300);

  for (const comp of components) {
    const [cx, cy] = comp.center;
    const colors = COMP_COLORS[comp.type] || DEFAULT_COMP_COLOR;
    const ko = comp.keepout || {};

    if (ko.type === "rectangle") {
      const w = ko.width_mm;
      const h = ko.height_mm;
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", cx - w / 2);
      rect.setAttribute("y", cy - h / 2);
      rect.setAttribute("width", w);
      rect.setAttribute("height", h);
      rect.setAttribute("rx", Math.min(1, w / 10));
      rect.setAttribute("fill", colors.fill);
      rect.setAttribute("stroke", colors.stroke);
      rect.setAttribute("stroke-width", sw);
      outlineSvg.appendChild(rect);
    } else if (ko.type === "circle") {
      const r = ko.radius_mm;
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", cx);
      circle.setAttribute("cy", cy);
      circle.setAttribute("r", r);
      circle.setAttribute("fill", colors.fill);
      circle.setAttribute("stroke", colors.stroke);
      circle.setAttribute("stroke-width", sw);
      outlineSvg.appendChild(circle);
    }

    // Label
    const label = comp.ref || comp.id;
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", cx);
    t.setAttribute("y", cy + fontSize * 0.35);
    t.setAttribute("text-anchor", "middle");
    t.setAttribute("fill", colors.stroke);
    t.setAttribute("font-size", fontSize);
    t.setAttribute("font-weight", "600");
    t.textContent = label;
    outlineSvg.appendChild(t);
  }

  outlineLabel.textContent = "Component placement";
  outlineLabel.style.display = "none";
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
    readyToPrintBtn.classList.remove("disabled");
    switchTab("3d");
  },
  undefined,
  (err) => {
    console.error("STL load error:", err);
    if (modelLabel) { modelLabel.textContent = "Failed to load 3D model"; modelLabel.style.display = ""; }
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

// ── Printer list ──────────────────────────────────────────────────

(async () => {
  try {
    const resp = await fetch("/api/printers");
    if (resp.ok) {
      const { printers } = await resp.json();
      const sel = document.getElementById("printerSelect");
      sel.innerHTML = '<option value="" disabled selected>Choose printer...</option>';
      for (const p of printers) {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = `${p.label} (${p.bed})`;
        sel.appendChild(opt);
      }
    }
  } catch (_) { /* keep hardcoded fallback */ }
})();

// ── Ready to Print / G-code functionality ─────────────────────────

readyToPrintBtn.addEventListener("click", async () => {
  if (readyToPrintBtn.classList.contains("disabled")) return;
  
  // Show the geocode overlay
  geocodeOverlay.style.display = "flex";
  geocodeStatus.textContent = "";
  geocodeStatus.classList.remove("ready", "slicing");
  geocodeDownloadBtn.disabled = true;
});

// Start slicing when printer is selected
document.getElementById("printerSelect").addEventListener("change", async (e) => {
  const printer = e.target.value;
  if (!printer) return;
  
  geocodeStatus.textContent = "Slicing model & generating G-code ...";
  geocodeStatus.classList.remove("ready");
  geocodeStatus.classList.add("slicing");
  geocodeDownloadBtn.disabled = true;

  try {
    const resp = await fetch("/api/slice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ printer }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      geocodeStatus.classList.remove("slicing");
      geocodeStatus.textContent = `G-code failed: ${err.detail || err.message || "Unknown error"}`;
      return;
    }
    const data = await resp.json();
    
    // Store results for download and step-by-step guide
    window._gcodeResult = data;
    
    geocodeStatus.textContent = "G-code ready for download";
    geocodeStatus.classList.remove("slicing");
    geocodeStatus.classList.add("ready");
    geocodeDownloadBtn.disabled = false;
    _enableGcodeButtons();
  } catch (e) {
    geocodeStatus.classList.remove("slicing");
    geocodeStatus.textContent = `G-code error: ${e.message}`;
  }
});

geocodeDownloadBtn.addEventListener("click", () => {
  if (geocodeDownloadBtn.disabled) return;
  window.location.href = "/api/gcode/download/enclosure_staged";
});

geocodeBackBtn.addEventListener("click", () => {
  geocodeOverlay.style.display = "none";
});

stepByStepBtn.addEventListener("click", () => {
  geocodeOverlay.style.display = "none";
  stepByStepScreen.style.display = "flex";
  
  // Populate the guide with real print stages
  const guideContent = stepByStepScreen.querySelector(".guide-content");
  const data = window._gcodeResult;
  
  const steps = [];
  if (data && data.pause_points) {
    const pp = data.pause_points;
    const components = data.components || [];
    
    // ── PAUSE 1: Ink Deposition ──────────────────────────────────
    steps.push({
      title: "Pause 1: Ink Deposition",
      subtitle: "Overview",
      body: `The printer will pause at layer ${pp.ink_layer_number} (Z = ${pp.ink_layer_z.toFixed(1)} mm).\n\n` +
            `At this pause you need to apply conductive ink to create the circuit traces.`,
      isPauseHeader: true,
      pauseNumber: 1,
    });
    steps.push({
      title: "Apply Conductive Ink",
      subtitle: "Pause 1 — Step 1",
      body: `Apply conductive ink along the trace channels carved into the floor surface.\n\n` +
            `Important:\n` +
            `• Follow the channel grooves carefully\n` +
            `• Ensure continuous lines without gaps\n` +
            `• Let the ink settle into the channels\n` +
            `• The ink connects all component pin holes\n\n` +
            `When done, press the knob on the printer to resume.`,
      pauseNumber: 1,
    });
    
    // ── PAUSE 2: Component Insertion ─────────────────────────────
    steps.push({
      title: "Pause 2: Component Insertion",
      subtitle: "Overview",
      body: `The printer will pause at layer ${pp.component_layer_number} (Z = ${pp.component_insert_z.toFixed(1)} mm).\n\n` +
            `At this pause you need to insert all electronic components into their pockets.`,
      isPauseHeader: true,
      pauseNumber: 2,
    });
    
    // Add each component as a separate step
    let componentStep = 1;
    const totalComponents = components.length;
    
    for (const comp of components) {
      const ctype = comp.type || "";
      const cid = comp.id || comp.ref || ctype;
      const rotation = comp.rotation_deg || 0;
      const center = comp.center || [0, 0];
      
      let title = "";
      let body = "";
      
      if (ctype === "button") {
        title = `Insert Button: ${cid}`;
        body = `Component: Tactile Push Button (12×12mm)\n` +
               `Location: ${_describePosition(center)}\n\n` +
               `Instructions:\n` +
               `• Find the square pocket for this button\n` +
               `• Orient the button so the pins align with the holes\n` +
               `• Press firmly until the button sits flush\n` +
               `• The button cap should protrude through the top hole`;
      } else if (ctype === "battery") {
        title = `Insert Battery Holder: ${cid}`;
        body = `Component: Battery Compartment (${comp.footprint || "2xAAA"})\n` +
               `Location: ${_describePosition(center)}\n\n` +
               `Instructions:\n` +
               `• Find the rectangular battery pocket\n` +
               `• Insert the battery holder with contacts facing the correct direction\n` +
               `• Ensure the spring contacts align with the pin holes\n` +
               `• Note: Batteries are inserted after printing is complete`;
      } else if (ctype === "controller") {
        title = `Insert Microcontroller: ${cid}`;
        const footprint = comp.footprint || "ATmega328P";
        body = `Component: ${footprint} (DIP package)\n` +
               `Location: ${_describePosition(center)}\n` +
               `Rotation: ${rotation}°\n\n` +
               `Instructions:\n` +
               `• Find the rectangular DIP pocket with pin holes\n` +
               `• IMPORTANT: Locate pin 1 marker (notch or dot on chip)\n` +
               `• Pin 1 should be at the ${_pinOneDirection(rotation)} of the pocket\n` +
               `• Align all pins with the holes before pressing down\n` +
               `• Press gently and evenly to seat all pins`;
      } else if (ctype === "diode") {
        title = `Insert IR Diode: ${cid}`;
        body = `Component: Infrared LED\n` +
               `Location: ${_describePosition(center)} (near edge for IR transmission)\n\n` +
               `Instructions:\n` +
               `• Find the round pocket near the board edge\n` +
               `• IMPORTANT: The longer leg (anode, +) goes in the marked hole\n` +
               `• The shorter leg (cathode, -) goes in the other hole\n` +
               `• The LED should point outward through the wall slot`;
      } else {
        title = `Insert Component: ${cid}`;
        body = `Component type: ${ctype}\n` +
               `Location: ${_describePosition(center)}\n\n` +
               `Insert this component into its designated pocket.\n` +
               `Ensure all pins align with the holes before pressing down.`;
      }
      
      steps.push({
        title,
        subtitle: `Pause 2 — Component ${componentStep} of ${totalComponents}`,
        body,
        pauseNumber: 2,
        componentIndex: componentStep,
        totalComponents,
      });
      componentStep++;
    }
    
    // Final step for pause 2
    steps.push({
      title: "Resume Printing",
      subtitle: "Pause 2 — Final Step",
      body: `All components have been inserted.\n\n` +
            `Checklist before resuming:\n` +
            `• All buttons are seated flush\n` +
            `• Microcontroller pin 1 is correctly oriented\n` +
            `• IR diode polarity is correct (long leg = +)\n` +
            `• All pins are fully inserted into their holes\n\n` +
            `Press the knob on the printer to resume.\n` +
            `The printer will complete the ceiling to seal the enclosure.`,
      pauseNumber: 2,
    });
    
  } else {
    steps.push({
      title: "No G-code data",
      subtitle: "",
      body: "Generate G-code first by clicking 'Ready to print'.",
    });
  }
  
  // Store steps and current index for navigation
  window._guideSteps = steps;
  window._guideIndex = 0;
  _renderGuideStep();
});

function _describePosition(center) {
  const [x, y] = center;
  // Basic position description based on coordinates
  const xPos = x < 30 ? "left" : x > 60 ? "right" : "center";
  const yPos = y < 30 ? "bottom" : y > 60 ? "top" : "middle";
  return `${yPos}-${xPos} area (X: ${x.toFixed(1)}mm, Y: ${y.toFixed(1)}mm)`;
}

function _pinOneDirection(rotation) {
  // Map rotation to pin 1 location description
  const r = ((rotation % 360) + 360) % 360;
  if (r < 45 || r >= 315) return "left side";
  if (r < 135) return "top";
  if (r < 225) return "right side";
  return "bottom";
}

function _renderGuideStep() {
  const guideContent = document.querySelector(".guide-content");
  if (!window._guideSteps || !guideContent) return;
  const step = window._guideSteps[window._guideIndex];
  const total = window._guideSteps.length;
  
  let subtitleHtml = step.subtitle ? `<div class="guide-subtitle">${step.subtitle}</div>` : "";
  let pauseBadge = "";
  if (step.pauseNumber) {
    pauseBadge = `<span class="pause-badge">Pause ${step.pauseNumber}</span>`;
  }
  
  guideContent.innerHTML = `
    <div class="guide-header-section">
      ${pauseBadge}
      <h2>${step.title}</h2>
      ${subtitleHtml}
    </div>
    <div class="guide-body-section">
      <p style="white-space:pre-wrap;">${step.body}</p>
      <div class="guide-step-counter">
        Step ${window._guideIndex + 1} of ${total}
      </div>
    </div>
  `;
}

backToDesignBtn.addEventListener("click", () => {
  stepByStepScreen.style.display = "none";
});

backToGcodeBtn.addEventListener("click", () => {
  stepByStepScreen.style.display = "none";
  geocodeOverlay.style.display = "flex";
});

// Toggle question window sidebar
toggleQuestionWindowBtn.addEventListener("click", () => {
  const isVisible = questionSidebar.style.display !== "none";
  questionSidebar.style.display = isVisible ? "none" : "flex";
  toggleQuestionWindowBtn.textContent = isVisible ? "Question window: Off" : "Question window: On";
  toggleQuestionWindowBtn.classList.toggle("active", !isVisible);
});

// Guide navigation buttons
guidePrevBtn.addEventListener("click", () => {
  if (!window._guideSteps || window._guideIndex <= 0) return;
  window._guideIndex--;
  _renderGuideStep();
});

guideNextBtn.addEventListener("click", () => {
  if (!window._guideSteps || window._guideIndex >= window._guideSteps.length - 1) return;
  window._guideIndex++;
  _renderGuideStep();
});

// Guide ask button
guideAskBtn.addEventListener("click", () => {
  const question = guidePromptInput.value.trim();
  if (!question) return;
  // TODO: Implement question handling
  guideResponse.textContent = "Response will appear here...";
  guidePromptInput.value = "";
});

// ── G-code preview ────────────────────────────────────────────────

const gcodePreviewBtn     = document.getElementById("gcodePreviewBtn");
const gcodePreviewScreen  = document.getElementById("gcodePreviewScreen");
const gcodePreviewBackBtn = document.getElementById("gcodePreviewBackBtn");
const gcodeLayerView      = document.getElementById("gcodeLayerView");
const gcodeCodeView       = document.getElementById("gcodeCodeView");
const layerDiagramSvg     = document.getElementById("layerDiagramSvg");
const gcodeLineCount      = document.getElementById("gcodeLineCount");
const gcodeLayerCount     = document.getElementById("gcodeLayerCount");
const gcodePauseJumps     = document.getElementById("gcodePauseJumps");
const gcodeCodeArea       = document.getElementById("gcodeCodeArea");
const gcodeTabBtns        = document.querySelectorAll(".gcode-tab");

// Tab switching
gcodeTabBtns.forEach(btn => btn.addEventListener("click", () => {
  const view = btn.dataset.gcodeView;
  gcodeTabBtns.forEach(b => b.classList.toggle("active", b.dataset.gcodeView === view));
  gcodeLayerView.classList.toggle("active", view === "layers");
  gcodeCodeView.classList.toggle("active", view === "code");
}));

// Back button
gcodePreviewBackBtn.addEventListener("click", () => {
  gcodePreviewScreen.style.display = "none";
  geocodeOverlay.style.display = "flex";
});

// Open in PrusaSlicer
const viewGcodeBtn  = document.getElementById("viewGcodeBtn");

async function _openInPrusaSlicer() {
  try {
    const resp = await fetch("/api/gcode/open-viewer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format: "gcode" }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(err.detail || "Failed to open PrusaSlicer");
    }
  } catch (e) {
    alert("Error: " + e.message);
  }
}

viewGcodeBtn.addEventListener("click", () => {
  if (!viewGcodeBtn.disabled) _openInPrusaSlicer();
});

// Preview button — fetch metadata + raw G-code, then show preview screen
gcodePreviewBtn.addEventListener("click", async () => {
  if (gcodePreviewBtn.disabled) return;
  gcodePreviewScreen.style.display = "flex";
  geocodeOverlay.style.display = "none";

  // Fetch preview metadata
  try {
    const [metaResp, codeResp] = await Promise.all([
      fetch("/api/gcode/preview/enclosure_staged"),
      fetch("/api/gcode/enclosure_staged"),
    ]);
    if (metaResp.ok) {
      const meta = await metaResp.json();
      gcodeLineCount.textContent = meta.total_lines.toLocaleString() + " lines";
      gcodeLayerCount.textContent = meta.total_layers + " layers";
      renderLayerDiagram(meta);
      renderPauseJumpButtons(meta.pauses);
    }
    if (codeResp.ok) {
      const raw = await codeResp.text();
      renderGcodeText(raw);
    }
  } catch (e) {
    console.error("G-code preview error:", e);
  }
});

// ── Layer diagram renderer ────────────────────────────────────────

function renderLayerDiagram(meta) {
  const svg = layerDiagramSvg;
  svg.innerHTML = "";

  const W = 600, H = 400;
  const pad = { top: 30, right: 30, bottom: 40, left: 70 };
  const drawW = W - pad.left - pad.right;
  const drawH = H - pad.top - pad.bottom;

  // Get data from gcodeResult
  const pp = window._gcodeResult?.pause_points;
  if (!pp) return;

  const totalH = pp.total_height || 16.5;
  const inkZ = pp.ink_layer_z;
  const compZ = pp.component_insert_z;

  // Scale helper: Z mm → SVG y (inverted: bottom = high Z)
  const yOf = z => pad.top + drawH - (z / totalH) * drawH;
  const xL = pad.left;
  const xR = pad.left + drawW;

  // Background
  const bg = _svgEl("rect", { x: 0, y: 0, width: W, height: H, fill: "#0b1120" });
  svg.appendChild(bg);

  // Color palette
  const stageColors = ["#3b82f6", "#10b981", "#6366f1", "#f59e0b", "#ef4444"];

  // Define stages
  const stages = [
    { label: "Floor", z0: 0, z1: inkZ, color: stageColors[0] },
    { label: "Ink deposit", z0: inkZ, z1: inkZ, color: stageColors[1], isPause: true },
    { label: "Walls", z0: inkZ, z1: compZ, color: stageColors[2] },
    { label: "Components", z0: compZ, z1: compZ, color: stageColors[3], isPause: true },
    { label: "Ceiling", z0: compZ, z1: totalH, color: stageColors[4] },
  ];

  // Draw filled stage blocks (print stages only)
  stages.forEach(s => {
    if (s.isPause) return;
    const y1 = yOf(s.z1);
    const y0 = yOf(s.z0);
    const h = y0 - y1;
    if (h < 1) return;
    const rect = _svgEl("rect", {
      x: xL + 60, y: y1, width: drawW - 120, height: h,
      fill: s.color, opacity: 0.25, rx: 4,
    });
    svg.appendChild(rect);

    // Stage label (centered)
    const label = _svgEl("text", {
      x: W / 2, y: y1 + h / 2 + 4,
      fill: s.color, "font-size": 13, "font-weight": 500,
      "text-anchor": "middle", "font-family": "inherit",
    });
    label.textContent = s.label;
    svg.appendChild(label);
  });

  // Draw pause lines (dashed)
  stages.filter(s => s.isPause).forEach(s => {
    const y = yOf(s.z0);
    const line = _svgEl("line", {
      x1: xL + 20, y1: y, x2: xR - 20, y2: y,
      stroke: s.color, "stroke-width": 2, "stroke-dasharray": "6,4",
    });
    svg.appendChild(line);

    // Pause label
    const lbl = _svgEl("text", {
      x: xR - 16, y: y - 6,
      fill: s.color, "font-size": 11, "text-anchor": "end", "font-family": "inherit",
    });
    lbl.textContent = `⏸ ${s.label} @ Z=${s.z0.toFixed(1)}mm`;
    svg.appendChild(lbl);
  });

  // Y axis — Z height labels
  const zTicks = [0, inkZ, compZ, totalH];
  zTicks.forEach(z => {
    const y = yOf(z);
    // tick
    const tick = _svgEl("line", {
      x1: xL - 4, y1: y, x2: xL, y2: y,
      stroke: "#4b5563", "stroke-width": 1,
    });
    svg.appendChild(tick);
    // label
    const lbl = _svgEl("text", {
      x: xL - 8, y: y + 4,
      fill: "#9ca3af", "font-size": 11, "text-anchor": "end", "font-family": "inherit",
    });
    lbl.textContent = z.toFixed(1) + " mm";
    svg.appendChild(lbl);
  });

  // Axis line
  const axis = _svgEl("line", {
    x1: xL, y1: pad.top, x2: xL, y2: pad.top + drawH,
    stroke: "#4b5563", "stroke-width": 1,
  });
  svg.appendChild(axis);

  // Title
  const title = _svgEl("text", {
    x: W / 2, y: 18,
    fill: "#e5e7eb", "font-size": 14, "font-weight": 600,
    "text-anchor": "middle", "font-family": "inherit",
  });
  title.textContent = "Print Cross-Section — Layer Heights & Pause Points";
  svg.appendChild(title);

  // Layer count annotation
  if (meta.total_layers) {
    const ann = _svgEl("text", {
      x: W / 2, y: H - 10,
      fill: "#6b7280", "font-size": 11,
      "text-anchor": "middle", "font-family": "inherit",
    });
    ann.textContent = `${meta.total_layers} layers @ 0.2mm — ${meta.total_lines.toLocaleString()} G-code lines`;
    svg.appendChild(ann);
  }
}

function _svgEl(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

// ── G-code text renderer ──────────────────────────────────────────

function renderGcodeText(raw) {
  const lines = raw.split("\n");
  const MAX_RENDER = 50000; // Limit for performance
  const truncated = lines.length > MAX_RENDER;

  let html = "";
  const limit = Math.min(lines.length, MAX_RENDER);

  for (let i = 0; i < limit; i++) {
    const line = lines[i];
    const num = `<span class="gcode-line-num">${i + 1}</span>`;
    const escaped = line
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");

    if (/^M601\b/.test(line) || /PAUSE|MANUAL STEP/i.test(line)) {
      html += `<span class="gcode-line gcode-pause" data-line="${i + 1}">${num}${escaped}</span>`;
    } else if (/^;\s*(INK|CONDUCTIVE)/i.test(line) || /INK_TRACE/i.test(line)) {
      html += `<span class="gcode-line gcode-ink">${num}${escaped}</span>`;
    } else if (/^;Z:/i.test(line) || /^;LAYER_CHANGE/i.test(line)) {
      html += `<span class="gcode-line gcode-layer">${num}${escaped}</span>`;
    } else if (/^;/.test(line)) {
      html += `<span class="gcode-line gcode-comment">${num}${escaped}</span>`;
    } else if (/^G[01]\b/.test(line)) {
      html += `<span class="gcode-line gcode-move">${num}${escaped}</span>`;
    } else {
      html += `<span class="gcode-line">${num}${escaped}</span>`;
    }
  }

  if (truncated) {
    html += `<span class="gcode-line gcode-comment"><span class="gcode-line-num">...</span>; (${(lines.length - MAX_RENDER).toLocaleString()} more lines truncated for performance)</span>`;
  }

  gcodeCodeArea.innerHTML = html;
}

// ── Pause jump buttons ────────────────────────────────────────────

function renderPauseJumpButtons(pauses) {
  if (!pauses || !pauses.length) return;
  gcodePauseJumps.innerHTML = "";

  pauses.forEach((p, i) => {
    const btn = document.createElement("button");
    btn.className = "gcode-pause-jump-btn";
    btn.textContent = `⏸ ${p.label || "Pause"} (L${p.line})`;
    btn.addEventListener("click", () => {
      // Switch to code tab
      gcodeTabBtns.forEach(b => b.classList.toggle("active", b.dataset.gcodeView === "code"));
      gcodeLayerView.classList.remove("active");
      gcodeCodeView.classList.add("active");
      // Scroll to the pause line
      const target = gcodeCodeArea.querySelector(`[data-line="${p.line}"]`);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        target.style.outline = "2px solid #f59e0b";
        setTimeout(() => target.style.outline = "", 2000);
      }
    });
    gcodePauseJumps.appendChild(btn);
  });
}

// Enable preview + viewer buttons when G-code result is available
function _enableGcodeButtons() {
  if (window._gcodeResult) {
    gcodePreviewBtn.disabled = false;
    viewGcodeBtn.disabled = false;
  }
}

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
    readyToPrintBtn.classList.add("disabled");
    // Hide geocode/guide screens
    geocodeOverlay.style.display = "none";
    stepByStepScreen.style.display = "none";
    gcodePreviewScreen.style.display = "none";
    gcodePreviewBtn.disabled = true;
    viewGcodeBtn.disabled = true;
    gcodeCodeArea.innerHTML = "";
    layerDiagramSvg.innerHTML = "";
    gcodePauseJumps.innerHTML = "";
    window._gcodeResult = null;
    hideProgress();
    // Reset curve editor
    curveEditor.style.display = "none";
    _curveLength = 0;
    _curveHeight = 0;
    _bottomCurveLength = 0;
    _bottomCurveHeight = 0;
    _curveActiveTab = "top";
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

// ── Curve editor — interactive fillet profile control ──────────────

const curveEditor    = document.getElementById("curveEditor");
const curveCanvas    = document.getElementById("curveCanvas");
const curveCtx       = curveCanvas.getContext("2d");
const curveLengthLbl = document.getElementById("curveLengthLabel");
const curveHeightLbl = document.getElementById("curveHeightLabel");
const curveToggleBtn = document.getElementById("curveEditorToggle");
const curveBody      = document.getElementById("curveEditorBody");
const recompileOverlay = document.getElementById("recompileOverlay");

// Physical limits (mm)
let _shellHeight  = 16.5;      // updated from backend
const MAX_CURVE_DIM = 10;      // max mm for both length & height (equal axes)

// Current curve state — top
let _curveLength = 0;
let _curveHeight = 0;
// Current curve state — bottom
let _bottomCurveLength = 0;
let _bottomCurveHeight = 0;

let _curveDragging = false;
let _curveActiveTab = "top";  // "top" or "bottom"

// Fetch true shell height from backend
async function _fetchShellHeight() {
  try {
    const r = await fetch("/api/shell_height");
    if (r.ok) {
      const d = await r.json();
      if (d.height_mm) _shellHeight = d.height_mm;
    }
  } catch (_) { /* fallback to default */ }
}

// Set curve to model-chosen values and show the widget
function showCurveEditor(topLen, topHt, bottomLen, bottomHt) {
  _curveLength = topLen || 0;
  _curveHeight = topHt || 0;
  _bottomCurveLength = bottomLen || 0;
  _bottomCurveHeight = bottomHt || 0;
  curveEditor.style.display = "";
  _updateCurveTabs();
  _drawCurveProfile();
}

// Toggle collapse
curveToggleBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  curveBody.classList.toggle("collapsed");
  curveToggleBtn.textContent = curveBody.classList.contains("collapsed") ? "▸" : "▾";
});

// Tab switching
function _updateCurveTabs() {
  document.querySelectorAll(".curve-tab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.curve === _curveActiveTab);
  });
}

document.querySelectorAll(".curve-tab").forEach(btn => {
  btn.addEventListener("click", () => {
    _curveActiveTab = btn.dataset.curve;
    _updateCurveTabs();
    _drawCurveProfile();
  });
});

// ── Draw the profile visualization ────────────────────────────────
// Top: shows top-right corner (inset from right, height from top).
// Bottom: shows bottom-right corner (inset from right, height from bottom).

function _drawCurveProfile() {
  const isTop = _curveActiveTab === "top";
  const activeLength = isTop ? _curveLength : _bottomCurveLength;
  const activeHeight = isTop ? _curveHeight : _bottomCurveHeight;

  const dpr = window.devicePixelRatio || 1;
  const cw = 152, ch = 140;
  curveCanvas.width  = cw * dpr;
  curveCanvas.height = ch * dpr;
  curveCanvas.style.width  = cw + "px";
  curveCanvas.style.height = ch + "px";
  curveCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  curveCtx.clearRect(0, 0, cw, ch);

  // Equal-scale square drawing area
  const scale = Math.min(cw - 40, ch - 40) / MAX_CURVE_DIM;
  const used  = MAX_CURVE_DIM * scale;

  // Center in canvas
  const mx = (cw - used) / 2;
  const my = (ch - used) / 2;

  // For top: origin at top-right (right=perimeter, top=shell top)
  // For bottom: origin at bottom-right (right=perimeter, bottom=shell bottom)
  const ox = mx + used;  // right (perimeter)
  const oy = isTop ? my : (my + used);  // top or bottom edge

  // Y direction: +1 means downward in canvas
  const yDir = isTop ? 1 : -1;  // top: curve goes down from top; bottom: curve goes up from bottom

  // ── Grid ──
  curveCtx.strokeStyle = "rgba(148,163,184,0.08)";
  curveCtx.lineWidth = 0.5;
  for (let mm = 0; mm <= MAX_CURVE_DIM; mm += 2) {
    const p = mm * scale;
    // horizontal
    curveCtx.beginPath();
    curveCtx.moveTo(ox - used, oy + yDir * p);
    curveCtx.lineTo(ox, oy + yDir * p);
    curveCtx.stroke();
    // vertical
    curveCtx.beginPath();
    curveCtx.moveTo(ox - p, my);
    curveCtx.lineTo(ox - p, my + used);
    curveCtx.stroke();
  }

  // ── Axis labels ──
  curveCtx.fillStyle = "rgba(148,163,184,0.4)";
  curveCtx.font = "9px system-ui";
  curveCtx.textAlign = "center";
  if (isTop) {
    curveCtx.fillText(`← ${MAX_CURVE_DIM} mm inset`, mx + used / 2, oy + used + 13);
  } else {
    curveCtx.fillText(`← ${MAX_CURVE_DIM} mm inset`, mx + used / 2, oy - used - 5);
  }
  curveCtx.save();
  curveCtx.translate(ox + 14, my + used / 2);
  curveCtx.rotate(-Math.PI / 2);
  curveCtx.fillText(isTop ? `${MAX_CURVE_DIM} mm ↓` : `${MAX_CURVE_DIM} mm ↑`, 0, 0);
  curveCtx.restore();

  // ── Wall endpoint (far from curve) ──
  const wallEnd = oy + yDir * used;  // bottom (top mode) or top (bottom mode)

  // ── Straight wall (right edge) ──
  curveCtx.strokeStyle = "rgba(59,130,246,0.5)";
  curveCtx.lineWidth = 2;
  curveCtx.beginPath();
  curveCtx.moveTo(ox, wallEnd);
  curveCtx.lineTo(ox, oy + yDir * activeHeight * scale);
  curveCtx.stroke();

  if (activeLength > 0 && activeHeight > 0) {
    // ── Fillet curve (elliptical quarter-arc) ──
    const steps = 48;
    curveCtx.strokeStyle = "#3b82f6";
    curveCtx.lineWidth = 2.5;
    curveCtx.beginPath();
    for (let i = 0; i <= steps; i++) {
      const theta = (i / steps) * (Math.PI / 2);
      const inset    = activeLength * (1 - Math.cos(theta));
      const hFromEdge = activeHeight * (1 - Math.sin(theta));
      const px = ox - inset * scale;
      const py = oy + yDir * hFromEdge * scale;
      if (i === 0) curveCtx.moveTo(px, py); else curveCtx.lineTo(px, py);
    }
    curveCtx.stroke();

    // ── Surface line (from curve end leftward) ──
    curveCtx.strokeStyle = "rgba(59,130,246,0.5)";
    curveCtx.lineWidth = 2;
    curveCtx.beginPath();
    curveCtx.moveTo(ox - activeLength * scale, oy);
    curveCtx.lineTo(ox - used, oy);
    curveCtx.stroke();

    // ── Filled shell body ──
    curveCtx.fillStyle = "rgba(59,130,246,0.07)";
    curveCtx.beginPath();
    curveCtx.moveTo(ox, wallEnd);
    curveCtx.lineTo(ox, oy + yDir * activeHeight * scale);
    for (let i = 0; i <= steps; i++) {
      const theta = (i / steps) * (Math.PI / 2);
      const inset    = activeLength * (1 - Math.cos(theta));
      const hFromEdge = activeHeight * (1 - Math.sin(theta));
      curveCtx.lineTo(ox - inset * scale, oy + yDir * hFromEdge * scale);
    }
    curveCtx.lineTo(ox - used, oy);
    curveCtx.lineTo(ox - used, wallEnd);
    curveCtx.closePath();
    curveCtx.fill();

    // ── Drag handle at θ = π/4 ──
    const ht = Math.PI / 4;
    const hInset    = activeLength * (1 - Math.cos(ht));
    const hFromEdge = activeHeight * (1 - Math.sin(ht));
    const hx = ox - hInset * scale;
    const hy = oy + yDir * hFromEdge * scale;
    curveCtx.fillStyle = "#3b82f6";
    curveCtx.strokeStyle = "white";
    curveCtx.lineWidth = 1.5;
    curveCtx.beginPath();
    curveCtx.arc(hx, hy, 6, 0, Math.PI * 2);
    curveCtx.fill();
    curveCtx.stroke();
  } else {
    // No curve — flat edge + straight wall
    curveCtx.beginPath();
    curveCtx.moveTo(ox, wallEnd);
    curveCtx.lineTo(ox, oy);
    curveCtx.stroke();

    curveCtx.strokeStyle = "rgba(59,130,246,0.5)";
    curveCtx.lineWidth = 2;
    curveCtx.beginPath();
    curveCtx.moveTo(ox, oy);
    curveCtx.lineTo(ox - used, oy);
    curveCtx.stroke();

    curveCtx.fillStyle = "rgba(59,130,246,0.07)";
    curveCtx.beginPath();
    curveCtx.moveTo(ox, oy);
    curveCtx.lineTo(ox, wallEnd);
    curveCtx.lineTo(ox - used, wallEnd);
    curveCtx.lineTo(ox - used, oy);
    curveCtx.closePath();
    curveCtx.fill();
  }

  // ── Value labels ──
  curveLengthLbl.textContent = `Length: ${activeLength.toFixed(1)} mm`;
  curveHeightLbl.textContent = `Height: ${activeHeight.toFixed(1)} mm`;
}

// ── Mouse → mm mapping (equal-scale axes) ─────────────────────────

function _canvasToMm(e) {
  const rect = curveCanvas.getBoundingClientRect();
  const cssX = e.clientX - rect.left;
  const cssY = e.clientY - rect.top;

  const cw = 152, ch = 140;
  const scale = Math.min(cw - 40, ch - 40) / MAX_CURVE_DIM;
  const used  = MAX_CURVE_DIM * scale;
  const mx = (cw - used) / 2;
  const my = (ch - used) / 2;
  const ox = mx + used;

  const isTop = _curveActiveTab === "top";
  const oy = isTop ? my : (my + used);

  const lengthMm = (ox - cssX) / scale;   // distance left from right edge (inward)
  const heightMm = isTop
    ? (cssY - oy) / scale    // top: distance down from top edge
    : (oy - cssY) / scale;   // bottom: distance up from bottom edge

  return {
    length: Math.max(0, Math.min(MAX_CURVE_DIM, lengthMm)),
    height: Math.max(0, Math.min(MAX_CURVE_DIM, heightMm)),
  };
}

curveCanvas.addEventListener("mousedown", (e) => {
  if (_curveCompiling) return;  // locked while compiling
  e.preventDefault();
  _curveDragging = true;
  _onCurveDrag(e);
});

document.addEventListener("mousemove", (e) => {
  if (!_curveDragging) return;
  _onCurveDrag(e);
});

document.addEventListener("mouseup", () => {
  if (_curveDragging) {
    _curveDragging = false;
    _sendCurveUpdate();
  }
});

function _onCurveDrag(e) {
  const mm = _canvasToMm(e);
  const len = Math.round(mm.length * 2) / 2;  // snap 0.5 mm
  const ht  = Math.round(mm.height * 2) / 2;
  if (_curveActiveTab === "top") {
    _curveLength = len;
    _curveHeight = ht;
  } else {
    _bottomCurveLength = len;
    _bottomCurveHeight = ht;
  }
  _drawCurveProfile();
}

// Double-click to reset active tab to zero
curveCanvas.addEventListener("dblclick", () => {
  if (_curveCompiling) return;
  if (_curveActiveTab === "top") {
    _curveLength = 0;
    _curveHeight = 0;
  } else {
    _bottomCurveLength = 0;
    _bottomCurveHeight = 0;
  }
  _drawCurveProfile();
  _sendCurveUpdate();
});

// ── Send curve update to backend (SCAD-only regen) ────────────────

let _curveCompiling = false;  // true while a compile is in-flight
// Track pending params if user changes during compile
let _curvePending = null;

function _showRecompiling(show) {
  if (recompileOverlay) {
    recompileOverlay.classList.toggle("active", show);
  }
  curveEditor.classList.toggle("updating", show);
  // Block pointer events on canvas while compiling
  curveCanvas.style.pointerEvents = show ? "none" : "";
  curveCanvas.style.opacity = show ? "0.5" : "";
}

async function _sendCurveUpdate() {
  // If already compiling, stash the latest params — they'll be sent
  // when the current compile finishes (only the last one matters).
  if (_curveCompiling) {
    _curvePending = {
      length: _curveLength, height: _curveHeight,
      bottomLength: _bottomCurveLength, bottomHeight: _bottomCurveHeight,
    };
    return;
  }

  _curveCompiling = true;
  _curvePending = null;
  _showRecompiling(true);

  try {
    const res = await fetch("/api/update_curve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        top_curve_length: _curveLength,
        top_curve_height: _curveHeight,
        bottom_curve_length: _bottomCurveLength,
        bottom_curve_height: _bottomCurveHeight,
      }),
    });
    if (!res.ok) {
      console.warn("Curve update failed:", await res.text());
      return;
    }
    const data = await res.json();
    if (data.model_name) {
      loadModel(`/api/model/${data.model_name}?t=${Date.now()}`);
    }
  } catch (err) {
    console.warn("Curve update error:", err);
  } finally {
    _curveCompiling = false;
    _showRecompiling(false);

    // If user changed the curve while we were compiling, fire one
    // more compile with the latest values.
    if (_curvePending) {
      _curveLength = _curvePending.length;
      _curveHeight = _curvePending.height;
      _bottomCurveLength = _curvePending.bottomLength;
      _bottomCurveHeight = _curvePending.bottomHeight;
      _curvePending = null;
      _drawCurveProfile();
      _sendCurveUpdate();
    }
  }
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
  _lastProgressPct = 0;
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

          case "pcb_layout":
            renderOutlineWithComponents(ev);
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
            // Show curve editor when model loads; use model's curve params if available
            _fetchShellHeight().then(() => {
              showCurveEditor(
                ev.top_curve_length || 0, ev.top_curve_height || 0,
                ev.bottom_curve_length || 0, ev.bottom_curve_height || 0,
              );
            });
            break;

          case "gcode_ready":
            // Store gcode result for the Ready to Print button / step guide
            window._gcodeResult = {
              pause_points: {
                ink_layer_z: ev.ink_layer_z,
                component_insert_z: ev.component_z,
                ink_layer_number: ev.ink_layer,
                component_layer_number: ev.component_layer,
                total_height: 0,  // filled from shell height
              },
              postprocess: {
                total_layers: ev.total_layers,
                ink_layer: ev.ink_layer,
                component_layer: ev.component_layer,
                stages: ev.stages,
              },
              staged_gcode: ev.staged_gcode,
            };
            // Fetch shell height to complete the data
            _fetchShellHeight().then(h => {
              if (window._gcodeResult && window._gcodeResult.pause_points) {
                window._gcodeResult.pause_points.total_height = h;
              }
            });
            _enableGcodeButtons();
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
