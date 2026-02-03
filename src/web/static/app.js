const chatEl = document.getElementById("chat");
const promptInput = document.getElementById("promptInput");
const sendBtn = document.getElementById("sendBtn");
const ttsBtn = document.getElementById("ttsBtn");
const printBtn = document.getElementById("printBtn");
const printerStatus = document.getElementById("printerStatus");
const useLlm = document.getElementById("useLlm");

// View panels and tabs
const viewerEl = document.getElementById("viewer");
const debugView = document.getElementById("debugView");
const masksView = document.getElementById("masksView");
const debugImage = document.getElementById("debugImage");
const positiveImage = document.getElementById("positiveImage");
const negativeImage = document.getElementById("negativeImage");
const tabBtns = document.querySelectorAll(".tab-btn");

let lastAssistantMessage = "";
let currentModelUrl = null;
let availableModels = null;  // {top: url, bottom: url}

// Tab switching
tabBtns.forEach(btn => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.view;
    
    // Update active tab
    tabBtns.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    
    // Update active panel
    viewerEl.classList.toggle("active", view === "3d");
    debugView.classList.toggle("active", view === "debug");
    masksView.classList.toggle("active", view === "masks");
    
    // Trigger resize to fix Three.js canvas when switching to 3D view
    if (view === "3d") {
      setTimeout(() => window.dispatchEvent(new Event('resize')), 50);
    }
  });
});

function addMessage(role, content) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.textContent = content;
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function setPrinterStatus(connected) {
  printerStatus.textContent = connected ? "Printer connected" : "Not connected";
  printerStatus.classList.toggle("connected", connected);
  printerStatus.classList.toggle("disconnected", !connected);
}

// Model selector for top/bottom shells
function updateModelSelector(models) {
  let selector = document.getElementById("modelSelector");
  
  // Create selector if it doesn't exist
  if (!selector) {
    const container = document.createElement("div");
    container.id = "modelSelectorContainer";
    container.className = "model-selector-container";
    container.innerHTML = `
      <label>View: </label>
      <select id="modelSelector">
        <option value="top">Top Shell</option>
        <option value="bottom">Bottom Shell (with traces)</option>
      </select>
    `;
    
    // Insert before the viewer element (inside the right panel)
    const viewerPanel = document.getElementById("viewer");
    viewerPanel.parentNode.insertBefore(container, viewerPanel);
    
    selector = document.getElementById("modelSelector");
    selector.addEventListener("change", (e) => {
      const modelType = e.target.value;
      if (availableModels && availableModels[modelType]) {
        currentModelUrl = availableModels[modelType] + `?t=${Date.now()}`;
        loadModel(currentModelUrl);
      } else {
        const names = {top: 'Top shell', bottom: 'Bottom shell', hatch: 'Battery hatch'};
        addMessage("assistant", `${names[modelType] || modelType} STL not available. Check OpenSCAD rendering.`);
      }
    });
  }
  
  // Update options based on what's available
  selector.innerHTML = "";
  if (models.top) {
    selector.innerHTML += `<option value="top">Top Shell</option>`;
  } else {
    selector.innerHTML += `<option value="top" disabled>Top Shell (not rendered)</option>`;
  }
  if (models.bottom) {
    selector.innerHTML += `<option value="bottom">Bottom Shell (with traces)</option>`;
  } else {
    selector.innerHTML += `<option value="bottom" disabled>Bottom Shell (rendering...)</option>`;
  }
  if (models.hatch) {
    selector.innerHTML += `<option value="hatch">Battery Hatch</option>`;
  }
  
  document.getElementById("modelSelectorContainer").style.display = "flex";
}

function hideModelSelector() {
  const container = document.getElementById("modelSelectorContainer");
  if (container) {
    container.style.display = "none";
  }
}

function loadDebugImages(debugImages) {
  if (!debugImages) {
    console.log("No debug images in response");
    return;
  }
  
  console.log("Loading debug images:", debugImages);
  const timestamp = Date.now();
  if (debugImages.debug) {
    console.log("Setting debug image src:", debugImages.debug);
    debugImage.src = debugImages.debug + `?t=${timestamp}`;
    debugImage.onload = () => console.log("Debug image loaded");
    debugImage.onerror = (e) => console.error("Debug image failed to load:", e);
  }
  if (debugImages.positive) {
    positiveImage.src = debugImages.positive + `?t=${timestamp}`;
  }
  if (debugImages.negative) {
    negativeImage.src = debugImages.negative + `?t=${timestamp}`;
  }
}

async function refreshPrinterStatus() {
  const res = await fetch("/api/printer/status");
  if (!res.ok) return;
  const data = await res.json();
  setPrinterStatus(!!data.connected);
}

sendBtn.addEventListener("click", async () => {
  const message = promptInput.value.trim();
  if (!message) return;

  addMessage("user", message);
  promptInput.value = "";
  sendBtn.disabled = true;

  // Add status message
  const statusDiv = document.createElement("div");
  statusDiv.className = "message assistant";
  statusDiv.textContent = "Generating design... (this may take a minute)";
  chatEl.appendChild(statusDiv);

  try {
    // Use AbortController with 5 minute timeout for OpenSCAD rendering
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 300000); // 5 minutes

    console.log("Sending request to /api/prompt...");
    const res = await fetch("/api/prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, use_llm: useLlm ? useLlm.checked : true }),
      signal: controller.signal,
    });

    clearTimeout(timeoutId);
    console.log("Response received:", res.status, res.statusText);

    // Remove status message
    if (statusDiv.parentNode) statusDiv.remove();

    if (!res.ok) {
      console.log("Response not OK, reading error...");
      const errorText = await res.text();
      console.log("Error text:", errorText);
      let errorDetail = "Generation failed.";
      try {
        const error = JSON.parse(errorText);
        errorDetail = error.detail || errorDetail;
      } catch (e) {
        errorDetail = errorText || errorDetail;
      }
      addMessage("assistant", errorDetail);
      return;
    }

    console.log("Reading response body...");
    const responseText = await res.text();
    console.log("Response text:", responseText.substring(0, 500));
    
    let data;
    try {
      data = JSON.parse(responseText);
    } catch (parseErr) {
      console.error("JSON parse error:", parseErr);
      addMessage("assistant", "Error parsing server response");
      return;
    }
    
    console.log("Parsed data:", data);
    const messages = data.messages || [];
    const last = messages[messages.length - 1];
    if (last && last.role === "assistant") {
      addMessage("assistant", last.content);
      lastAssistantMessage = last.content;
    }
    setPrinterStatus(data.printer_connected);
    
    // Load debug images if available
    if (data.debug_images) {
      console.log("Loading debug images:", data.debug_images);
      loadDebugImages(data.debug_images);
    }
    
    // Handle multi-part models (top/bottom shells)
    if (data.models) {
      console.log("Multiple models available:", data.models);
      availableModels = data.models;
      updateModelSelector(data.models);
      
      // Load top shell by default, or bottom if top not available
      const defaultModel = data.models.top || data.models.bottom;
      if (defaultModel) {
        currentModelUrl = defaultModel + `?t=${Date.now()}`;
        loadModel(currentModelUrl);
      }
    } else if (data.model_url) {
      console.log("Loading model from:", data.model_url);
      availableModels = null;
      hideModelSelector();
      currentModelUrl = data.model_url + `?t=${Date.now()}`;
      loadModel(currentModelUrl);
    } else {
      console.log("No model_url in response");
    }
  } catch (err) {
    console.error("Caught exception:", err);
    console.error("Exception stack:", err.stack);
    if (statusDiv.parentNode) statusDiv.remove();
    if (err.name === 'AbortError') {
      addMessage("assistant", "Request timed out. The model may still be generating.");
    } else {
      addMessage("assistant", `Error: ${err.name} - ${err.message}`);
    }
  } finally {
    sendBtn.disabled = false;
  }
});

ttsBtn.addEventListener("click", () => {
  if (!lastAssistantMessage) return;
  const utterance = new SpeechSynthesisUtterance(lastAssistantMessage);
  speechSynthesis.speak(utterance);
});

printBtn.addEventListener("click", async () => {
  if (!currentModelUrl) {
    addMessage("assistant", "Generate a model before printing.");
    return;
  }
  const res = await fetch("/api/print", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_url: currentModelUrl }),
  });

  if (!res.ok) {
    const error = await res.json();
    addMessage("assistant", error.detail || "Print request failed.");
    return;
  }

  const data = await res.json();
  addMessage("assistant", `Print queued: ${data.job_id}`);
});

// Three.js scene (viewerEl already defined at top)
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b1120);

const camera = new THREE.PerspectiveCamera(45, viewerEl.clientWidth / viewerEl.clientHeight, 0.1, 2000);
camera.position.set(0, -140, 120);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(viewerEl.clientWidth, viewerEl.clientHeight);
viewerEl.appendChild(renderer.domElement);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.rotateSpeed = 0.6;

const ambient = new THREE.AmbientLight(0xffffff, 0.6);
scene.add(ambient);

const dir = new THREE.DirectionalLight(0xffffff, 0.9);
dir.position.set(200, -100, 200);
scene.add(dir);

let currentMesh = null;

function loadModel(url) {
  console.log("loadModel called with:", url);
  
  // Switch to 3D view tab when loading a new model
  tabBtns.forEach(b => b.classList.remove("active"));
  document.querySelector('[data-view="3d"]').classList.add("active");
  viewerEl.classList.add("active");
  debugView.classList.remove("active");
  masksView.classList.remove("active");
  
  const loader = new THREE.STLLoader();
  loader.load(
    url,
    (geometry) => {
      console.log("STL loaded, updating scene...");
      if (currentMesh) {
        scene.remove(currentMesh);
        if (currentMesh.geometry) currentMesh.geometry.dispose();
        if (currentMesh.material) currentMesh.material.dispose();
      }
      const material = new THREE.MeshStandardMaterial({ color: 0x93c5fd, metalness: 0.1, roughness: 0.5 });
      const mesh = new THREE.Mesh(geometry, material);
      geometry.computeBoundingBox();
      geometry.center();
      mesh.rotation.x = Math.PI / 2;
      scene.add(mesh);
      currentMesh = mesh;

      const box = new THREE.Box3().setFromObject(mesh);
      const size = new THREE.Vector3();
      box.getSize(size);
      const maxDim = Math.max(size.x, size.y, size.z);
      const distance = maxDim * 1.7;
      camera.position.set(0, -distance, distance * 0.7);
      controls.target.set(0, 0, 0);
      controls.update();
      
      // Force render update
      renderer.render(scene, camera);
      console.log("Model updated, size:", size);
    },
    (progress) => {
      console.log("Loading progress:", progress.loaded, "/", progress.total);
    },
    (error) => {
      console.error("STL load error:", error);
      addMessage("assistant", "Failed to load STL preview.");
    }
  );
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

animate();

window.addEventListener("resize", () => {
  const { clientWidth, clientHeight } = viewerEl;
  camera.aspect = clientWidth / clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(clientWidth, clientHeight);
});

const zoomIn = document.getElementById("zoomIn");
const zoomOut = document.getElementById("zoomOut");

zoomIn.addEventListener("click", () => {
  controls.dollyIn(1.2);
  controls.update();
});

zoomOut.addEventListener("click", () => {
  controls.dollyOut(1.2);
  controls.update();
});

refreshPrinterStatus();
