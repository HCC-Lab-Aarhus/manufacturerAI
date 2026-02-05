const chatEl = document.getElementById("chat");
const promptInput = document.getElementById("promptInput");
const sendBtn = document.getElementById("sendBtn");
const ttsBtn = document.getElementById("ttsBtn");
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

// Model selector elements
const modelSelectorContainer = document.getElementById("modelSelectorContainer");
const modelSelector = document.getElementById("modelSelector");
const downloadBtn = document.getElementById("downloadBtn");
let currentView = "3d";
let availableMasks = { positive: null, negative: null };

// Tab switching
tabBtns.forEach(btn => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.view;
    currentView = view;
    
    // Update active tab
    tabBtns.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    
    // Update active panel
    viewerEl.classList.toggle("active", view === "3d");
    debugView.classList.toggle("active", view === "debug");
    masksView.classList.toggle("active", view === "masks");
    
    // Update model selector visibility and content
    if (view === "3d" && availableModels) {
      updateModelSelector(availableModels);
      modelSelectorContainer.classList.add("visible");
      downloadBtn.classList.remove("disabled");
      downloadBtn.title = "Download STL file";
    } else if (view === "masks") {
      updateMasksSelector();
      modelSelectorContainer.classList.add("visible");
      // Enable download for masks if images are loaded
      if (positiveImage.src || negativeImage.src) {
        downloadBtn.classList.remove("disabled");
        downloadBtn.title = "Download mask image";
      }
    } else {
      modelSelectorContainer.classList.remove("visible");
    }
    
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

// Model selector functions
function updateModelSelector(models) {
  // Clear and populate the selector for 3D models
  modelSelector.innerHTML = "";
  
  if (models.top) {
    modelSelector.innerHTML += `<option value="top">Top Shell</option>`;
  }
  if (models.bottom) {
    modelSelector.innerHTML += `<option value="bottom">Bottom Shell</option>`;
  }
  if (models.hatch) {
    modelSelector.innerHTML += `<option value="hatch">Battery Hatch</option>`;
  }
  if (models.combined) {
    modelSelector.innerHTML += `<option value="combined">Print Plate (All Parts)</option>`;
  }
  
  // Enable download button
  downloadBtn.classList.remove("disabled");
  downloadBtn.title = "Download STL file";
  
  // Show selector if on 3D view
  if (currentView === "3d") {
    modelSelectorContainer.classList.add("visible");
  }
}

function updateMasksSelector() {
  modelSelector.innerHTML = `
    <option value="positive">Positive (Conductive)</option>
    <option value="negative">Negative (Insulating)</option>
  `;
  // Show positive by default
  showMaskImage("positive");
}

function showMaskImage(type) {
  if (type === "positive") {
    positiveImage.classList.add("active");
    negativeImage.classList.remove("active");
  } else {
    positiveImage.classList.remove("active");
    negativeImage.classList.add("active");
  }
}

// Handle model selector change
modelSelector.addEventListener("change", (e) => {
  const value = e.target.value;
  
  if (currentView === "3d") {
    if (availableModels && availableModels[value]) {
      currentModelUrl = availableModels[value] + `?t=${Date.now()}`;
      loadModel(currentModelUrl);
    } else {
      const names = {top: 'Top shell', bottom: 'Bottom shell', hatch: 'Battery hatch', combined: 'Print plate'};
      addMessage("assistant", `${names[value] || value} STL not available.`);
    }
  } else if (currentView === "masks") {
    showMaskImage(value);
  }
});

// Handle download button click
downloadBtn.addEventListener("click", () => {
  if (downloadBtn.classList.contains("disabled")) return;
  
  if (currentView === "3d") {
    const modelType = modelSelector.value || "combined";
    window.location.href = `/api/model/download?type=${modelType}`;
  } else if (currentView === "masks") {
    const maskType = modelSelector.value || "positive";
    // Download mask image
    const img = maskType === "positive" ? positiveImage : negativeImage;
    if (img.src) {
      const link = document.createElement("a");
      link.href = img.src;
      link.download = `mask_${maskType}.png`;
      link.click();
    }
  }
});

function hideModelSelector() {
  modelSelectorContainer.classList.remove("visible");
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

// Send message on Enter key press
promptInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendBtn.click();
  }
});

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

// Speech-to-text (dictation) functionality
const micBtn = document.getElementById("micBtn");
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

if (SpeechRecognition) {
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
} else {
  // Browser doesn't support speech recognition
  micBtn.style.display = "none";
  console.warn("Speech recognition not supported in this browser");
}

// Three.js scene (viewerEl already defined at top)
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b1120);

const camera = new THREE.PerspectiveCamera(45, viewerEl.clientWidth / viewerEl.clientHeight, 0.1, 2000);
const initialCameraPosition = { x: 0, y: -140, z: 120 };
camera.position.set(initialCameraPosition.x, initialCameraPosition.y, initialCameraPosition.z);

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

// Track current zoom levels and positions for images
let debugZoom = 1;
let masksZoom = 1;

// Track drag state for each image
const imageDragStates = {
  debug: { translateX: 0, translateY: 0 },
  positive: { translateX: 0, translateY: 0 },
  negative: { translateX: 0, translateY: 0 }
};

function applyImageTransform(img, zoom, dragState) {
  img.style.transform = `scale(${zoom}) translate(${dragState.translateX / zoom}px, ${dragState.translateY / zoom}px)`;
  img.style.transformOrigin = "center center";
}

function applyImageZoom(view, zoom) {
  if (view === "debug") {
    applyImageTransform(debugImage, zoom, imageDragStates.debug);
  } else if (view === "masks") {
    applyImageTransform(positiveImage, zoom, imageDragStates.positive);
    applyImageTransform(negativeImage, zoom, imageDragStates.negative);
  }
}

// 3D view zoom buttons
zoomIn.addEventListener("click", () => {
  const direction = new THREE.Vector3();
  camera.getWorldDirection(direction);
  camera.position.addScaledVector(direction, 20);
  controls.update();
});

zoomOut.addEventListener("click", () => {
  const direction = new THREE.Vector3();
  camera.getWorldDirection(direction);
  camera.position.addScaledVector(direction, -20);
  controls.update();
});

// Reset view buttons
document.getElementById("reset3DView").addEventListener("click", () => {
  camera.position.set(initialCameraPosition.x, initialCameraPosition.y, initialCameraPosition.z);
  controls.target.set(0, 0, 0);
  controls.update();
});

document.getElementById("resetDebugView").addEventListener("click", () => {
  debugZoom = 1;
  imageDragStates.debug.translateX = 0;
  imageDragStates.debug.translateY = 0;
  applyImageTransform(debugImage, debugZoom, imageDragStates.debug);
});

document.getElementById("resetMasksView").addEventListener("click", () => {
  masksZoom = 1;
  imageDragStates.positive.translateX = 0;
  imageDragStates.positive.translateY = 0;
  imageDragStates.negative.translateX = 0;
  imageDragStates.negative.translateY = 0;
  applyImageTransform(positiveImage, masksZoom, imageDragStates.positive);
  applyImageTransform(negativeImage, masksZoom, imageDragStates.negative);
});

// Debug view zoom buttons
debugView.querySelectorAll(".zoom-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    if (btn.dataset.action === "in") {
      debugZoom = Math.min(debugZoom * 1.2, 5);
    } else {
      debugZoom = Math.max(debugZoom / 1.2, 0.2);
    }
    applyImageZoom("debug", debugZoom);
  });
});

// Masks view zoom buttons
masksView.querySelectorAll(".zoom-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    if (btn.dataset.action === "in") {
      masksZoom = Math.min(masksZoom * 1.2, 5);
    } else {
      masksZoom = Math.max(masksZoom / 1.2, 0.2);
    }
    applyImageZoom("masks", masksZoom);
  });
});

// Wheel/trackpad zoom for debug view
debugView.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (e.deltaY < 0) {
    debugZoom = Math.min(debugZoom * 1.1, 5);
  } else {
    debugZoom = Math.max(debugZoom / 1.1, 0.2);
  }
  applyImageZoom("debug", debugZoom);
}, { passive: false });

// Wheel/trackpad zoom for masks view
masksView.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (e.deltaY < 0) {
    masksZoom = Math.min(masksZoom * 1.1, 5);
  } else {
    masksZoom = Math.max(masksZoom / 1.1, 0.2);
  }
  applyImageZoom("masks", masksZoom);
}, { passive: false });

// Drag functionality for images
function setupImageDrag(img, stateKey, viewType) {
  const dragState = imageDragStates[stateKey];
  let isDragging = false;
  let startX = 0;
  let startY = 0;
  
  img.addEventListener("mousedown", (e) => {
    e.preventDefault();
    isDragging = true;
    startX = e.clientX - dragState.translateX;
    startY = e.clientY - dragState.translateY;
    img.style.cursor = "grabbing";
  });
  
  document.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    dragState.translateX = e.clientX - startX;
    dragState.translateY = e.clientY - startY;
    const zoom = viewType === "debug" ? debugZoom : masksZoom;
    applyImageTransform(img, zoom, dragState);
  });
  
  document.addEventListener("mouseup", () => {
    if (isDragging) {
      isDragging = false;
      img.style.cursor = "grab";
    }
  });
  
  // Reset position on double click
  img.addEventListener("dblclick", () => {
    dragState.translateX = 0;
    dragState.translateY = 0;
    const zoom = viewType === "debug" ? debugZoom : masksZoom;
    applyImageTransform(img, zoom, dragState);
  });
}

// Setup drag for debug image
setupImageDrag(debugImage, "debug", "debug");

// Setup drag for mask images
setupImageDrag(positiveImage, "positive", "masks");
setupImageDrag(negativeImage, "negative", "masks");

// Panel resizer functionality
const resizer = document.getElementById("resizer");
const appContainer = document.querySelector(".app");
const leftPanel = document.querySelector(".panel.left");

let isResizing = false;

resizer.addEventListener("mousedown", (e) => {
  isResizing = true;
  resizer.classList.add("dragging");
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";
});

document.addEventListener("mousemove", (e) => {
  if (!isResizing) return;
  
  const containerRect = appContainer.getBoundingClientRect();
  const newLeftWidth = e.clientX - containerRect.left;
  const containerWidth = containerRect.width;
  const resizerWidth = 6;
  
  // Constrain between 200px and 60% of container width
  const minWidth = 200;
  const maxWidth = containerWidth * 0.6;
  const clampedWidth = Math.max(minWidth, Math.min(maxWidth, newLeftWidth));
  
  const leftFr = clampedWidth;
  const rightFr = containerWidth - clampedWidth - resizerWidth;
  
  appContainer.style.gridTemplateColumns = `${leftFr}px ${resizerWidth}px ${rightFr}px`;
  
  // Trigger resize event for Three.js canvas
  window.dispatchEvent(new Event('resize'));
});

document.addEventListener("mouseup", () => {
  if (isResizing) {
    isResizing = false;
    resizer.classList.remove("dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }
});
