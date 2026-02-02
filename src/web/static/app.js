const chatEl = document.getElementById("chat");
const promptInput = document.getElementById("promptInput");
const sendBtn = document.getElementById("sendBtn");
const ttsBtn = document.getElementById("ttsBtn");
const printBtn = document.getElementById("printBtn");
const printerStatus = document.getElementById("printerStatus");
const useLlm = document.getElementById("useLlm");

let lastAssistantMessage = "";
let currentModelUrl = null;

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

  try {
    const res = await fetch("/api/prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, use_llm: useLlm ? useLlm.checked : true }),
    });

    if (!res.ok) {
      const error = await res.json();
      addMessage("assistant", error.detail || "Generation failed.");
      return;
    }

    const data = await res.json();
    const messages = data.messages || [];
    const last = messages[messages.length - 1];
    if (last && last.role === "assistant") {
      addMessage("assistant", last.content);
      lastAssistantMessage = last.content;
    }
    setPrinterStatus(data.printer_connected);
    if (data.model_url) {
      currentModelUrl = data.model_url + `?t=${Date.now()}`;
      loadModel(currentModelUrl);
    }
  } catch (err) {
    addMessage("assistant", "Network error while generating model.");
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

// Three.js scene
const viewerEl = document.getElementById("viewer");
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
  const loader = new THREE.STLLoader();
  loader.load(
    url,
    (geometry) => {
      if (currentMesh) {
        scene.remove(currentMesh);
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
      controls.update();
    },
    undefined,
    () => {
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
