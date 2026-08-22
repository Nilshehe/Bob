// app.js — Bob GUI runtime.
// Tar emot JSON-kommandon över WebSocket och bygger GUI-element rent
// dynamiskt i DOM:en. Ingen sida behöver någonsin byggas om — varje
// elementtyp nedan skapas enbart utifrån data som skickas från backend.

const params = new URLSearchParams(location.search);
const windowId = params.get("window_id") || "main";
const canvas = document.getElementById("canvas");

const elements = {}; // element_id -> { dom, type }
const three = {};    // element_id -> { scene, camera, renderer, model }

const ws = new WebSocket(`ws://${location.host}/ws/${windowId}`);
ws.onmessage = (evt) => handleMessage(JSON.parse(evt.data));
ws.onclose = () => console.warn("Bob GUI: websocket stängd");

function handleMessage(msg) {
  switch (msg.type) {
    case "sync":
      Object.entries(msg.elements || {}).forEach(([id, data]) => createElementDom(id, data));
      break;
    case "create_element":
      createElementDom(msg.element_id, msg);
      break;
    case "remove_element":
      removeElementDom(msg.element_id);
      break;
    case "move_element":
      moveElementDom(msg.element_id, msg.x, msg.y);
      break;
    case "update_element":
      updateElementDom(msg.element_id, msg);
      break;
  }
}

function sendEvent(payload) {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
}

function createElementDom(id, data) {
  if (elements[id]) removeElementDom(id);
  if (data.visible === false) return;

  const el = document.createElement("div");
  el.className = `gui-element type-${data.type}`;
  el.style.left = (data.x || 0) + "px";
  el.style.top = (data.y || 0) + "px";
  el.style.width = (data.w || 200) + "px";
  el.style.height = (data.h || 80) + "px";
  el.dataset.id = id;

  const header = document.createElement("div");
  header.className = "header";
  header.textContent = data.label || data.type;
  el.appendChild(header);

  const body = document.createElement("div");
  body.className = "body";
  el.appendChild(body);

  buildBody(data.type, body, data.props || {}, id);

  const handle = document.createElement("div");
  handle.className = "resize-handle";
  el.appendChild(handle);

  makeDraggable(el, header, id);
  makeResizable(el, handle, id);

  canvas.appendChild(el);
  elements[id] = { dom: el, type: data.type };

  if (data.type === "3d") init3D(id, body, data.props || {});
}

function buildBody(type, body, props, id) {
  if (type === "status") {
    body.innerHTML = `<span class="dot"></span>${props.text || "OK"}`;
  } else if (type === "input") {
    const input = document.createElement("input");
    input.placeholder = props.placeholder || "";
    body.appendChild(input);
  } else if (type === "button") {
    body.textContent = props.text || "";
    body.parentElement.addEventListener("click", () =>
      sendEvent({ type: "element_clicked", element_id: id })
    );
  } else if (type === "text" || type === "panel") {
    body.textContent = props.text || "";
  }
  // type "3d" fylls i av init3D()
}

function removeElementDom(id) {
  const e = elements[id];
  if (!e) return;
  e.dom.remove();
  delete elements[id];
  delete three[id];
}

function moveElementDom(id, x, y) {
  const e = elements[id];
  if (!e) return;
  e.dom.style.left = x + "px";
  e.dom.style.top = y + "px";
}

function updateElementDom(id, fields) {
  const e = elements[id];
  if (!e) return;
  if (fields.visible === false) { removeElementDom(id); return; }
  if (fields.w) e.dom.style.width = fields.w + "px";
  if (fields.h) e.dom.style.height = fields.h + "px";
  if (fields.label !== undefined) e.dom.querySelector(".header").textContent = fields.label;
  if (fields.props && e.type === "3d") apply3DProps(id, fields.props);
}

// ---- drag & resize (ren JS, inga beroenden) ----
function makeDraggable(el, handleEl, id) {
  let sx, sy, ox, oy, dragging = false;
  handleEl.addEventListener("mousedown", (e) => {
    dragging = true; el.classList.add("dragging");
    sx = e.clientX; sy = e.clientY;
    ox = el.offsetLeft; oy = el.offsetTop;
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    el.style.left = (ox + (e.clientX - sx)) + "px";
    el.style.top = (oy + (e.clientY - sy)) + "px";
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false; el.classList.remove("dragging");
    sendEvent({ type: "element_moved", element_id: id, x: el.offsetLeft, y: el.offsetTop });
  });
}

function makeResizable(el, handle, id) {
  let sx, sy, sw, sh, resizing = false;
  handle.addEventListener("mousedown", (e) => {
    resizing = true; sx = e.clientX; sy = e.clientY;
    sw = el.offsetWidth; sh = el.offsetHeight;
    e.stopPropagation(); e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!resizing) return;
    el.style.width = Math.max(60, sw + (e.clientX - sx)) + "px";
    el.style.height = Math.max(40, sh + (e.clientY - sy)) + "px";
  });
  window.addEventListener("mouseup", () => {
    if (!resizing) return;
    resizing = false;
    sendEvent({ type: "element_resized", element_id: id, w: el.offsetWidth, h: el.offsetHeight });
  });
}

// ---- 3D / hologram-rendering ----
function init3D(id, body, props) {
  const w = body.clientWidth || 380, h = body.clientHeight || 380;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 1000);
  camera.position.set(0, 0, 5);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setSize(w, h);
  body.innerHTML = "";
  body.appendChild(renderer.domElement);

  scene.add(new THREE.PointLight(0x00eaff, 2, 20).translateOnAxis(new THREE.Vector3(0.4, 0.4, 0.8), 5));
  scene.add(new THREE.AmbientLight(0x00eaff, 0.3));

  three[id] = { scene, camera, renderer, model: null };

  if (props.model_path) loadModel(id, props.model_path, props);

  (function animate() {
    requestAnimationFrame(animate);
    const ctx = three[id];
    if (!ctx) return;
    if (ctx.model) ctx.model.rotation.y += 0.004;
    ctx.renderer.render(ctx.scene, ctx.camera);
  })();
}

function loadModel(id, path, props) {
  const ctx = three[id];
  if (!ctx) return;
  const loader = new THREE.GLTFLoader();
  loader.load(
    path,
    (gltf) => {
      const model = gltf.scene;
      applyHologramMaterial(model, props);
      ctx.scene.add(model);
      ctx.model = model;
      const [x, y, z] = props.position3d || [0, 0, 0];
      model.position.set(x, y, z);
      if (props.scale) model.scale.setScalar(props.scale);
    },
    undefined,
    (err) => console.error("Kunde inte ladda 3D-modell:", err)
  );
}

function applyHologramMaterial(model, props) {
  const color = new THREE.Color(props.color || "#00eaff");
  model.traverse((child) => {
    if (child.isMesh) {
      child.material = new THREE.MeshStandardMaterial({
        color, emissive: color, emissiveIntensity: 0.6,
        wireframe: props.wireframe !== false,
        transparent: true, opacity: props.opacity ?? 0.85,
      });
    }
  });
}

function apply3DProps(id, props) {
  const ctx = three[id];
  if (!ctx || !ctx.model) return;
  if (props.color || props.wireframe !== undefined || props.opacity !== undefined) {
    applyHologramMaterial(ctx.model, props);
  }
  if (props.position3d) ctx.model.position.set(...props.position3d);
  if (props.scale) ctx.model.scale.setScalar(props.scale);
}
