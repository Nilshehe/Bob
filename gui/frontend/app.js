// app.js — Bob GUI runtime.
//
// Backend:
//   msg.type         = kommando
//   msg.element_type = elementtyp
//
// Exempel:
// {
//   "type": "create_element",
//   "element_id": "button_abc123",
//   "element_type": "button",
//   ...
// }

const params = new URLSearchParams(location.search);

const windowId =
  params.get("window_id") || "main";

const canvas =
  document.getElementById("canvas");

const elements = {};
const three = {};


// ---------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------

const ws =
  new WebSocket(
    `ws://${location.host}/ws/${windowId}`
  );


ws.onmessage = (evt) => {
  try {
    handleMessage(
      JSON.parse(evt.data)
    );
  } catch (err) {
    console.error(
      "Bob GUI: kunde inte läsa WebSocket-meddelande:",
      err
    );
  }
};


ws.onclose = () => {
  console.warn(
    "Bob GUI: websocket stängd"
  );
};


// ---------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------

function handleMessage(msg) {
  switch (msg.type) {

    case "voice_state":
      handleVoiceState(msg);
      break;


    case "agent_stream":
      handleAgentStream(msg);
      break;

    case "agent_monitor_update":
      handleAgentMonitorUpdate(msg);
      break;


    case "stream_panel_state":
      applyStreamPanelState(msg);
      break;


    case "stream_panel_clear":
      if (streamBody) {
        streamBody.innerHTML = "";
      }
      break;


    case "sync":
      Object.entries(
        msg.elements || {}
      ).forEach(
        ([id, data]) => {
          createElementDom(
            id,
            data
          );
        }
      );
      break;


    case "create_element":
      createElementDom(
        msg.element_id,
        msg
      );
      break;


    case "remove_element":
      removeElementDom(
        msg.element_id
      );
      break;


    case "move_element":
      moveElementDom(
        msg.element_id,
        msg.x,
        msg.y
      );
      break;


    case "update_element":
      updateElementDom(
        msg.element_id,
        msg
      );
      break;
  }
}


function sendEvent(payload) {
  if (
    ws.readyState ===
    WebSocket.OPEN
  ) {
    ws.send(
      JSON.stringify(payload)
    );
  }
}


// ---------------------------------------------------------------------
// Element type
// ---------------------------------------------------------------------

function getElementType(data) {

  // Nya create_element-meddelanden
  if (data.element_type) {
    return data.element_type;
  }

  // Bakåtkompatibilitet för sync
  return data.type;
}


// ---------------------------------------------------------------------
// Create element
// ---------------------------------------------------------------------

function createElementDom(
  id,
  data
) {
  if (elements[id]) {
    removeElementDom(id);
  }


  if (data.visible === false) {
    return;
  }


  const elementType =
    getElementType(data);


  if (!elementType) {
    console.warn(
      "Bob GUI: element saknar element_type:",
      data
    );

    return;
  }


  const el =
    document.createElement("div");


  el.className =
    `gui-element type-${elementType}`;


  el.style.left =
    (data.x || 0) + "px";


  el.style.top =
    (data.y || 0) + "px";


  el.style.width =
    (data.w || 200) + "px";


  el.style.height =
    (data.h || 80) + "px";


  el.dataset.id = id;


  // Header
  const header =
    document.createElement("div");


  header.className =
    "header";


  header.textContent =
    data.label ||
    elementType;


  el.appendChild(
    header
  );


  // Body
  const body =
    document.createElement("div");


  body.className =
    "body";


  el.appendChild(
    body
  );


  buildBody(
    elementType,
    body,
    data.props || {},
    id
  );


  // Resize handle
  const handle =
    document.createElement("div");


  handle.className =
    "resize-handle";


  el.appendChild(
    handle
  );


  // Drag + resize
  makeDraggable(
    el,
    header,
    id
  );


  makeResizable(
    el,
    handle,
    id
  );


  // Add
  canvas.appendChild(
    el
  );


  // Save
  elements[id] = {
    dom: el,
    type: elementType,
  };


  // 3D
  if (
    elementType === "3d"
  ) {
    init3D(
      id,
      body,
      data.props || {}
    );
  }
}


// ---------------------------------------------------------------------
// Element body
// ---------------------------------------------------------------------

function buildBody(
  type,
  body,
  props,
  id
) {

  if (type === "status") {

    body.innerHTML =
      `<span class="dot"></span>${props.text || "OK"}`;

  }


  else if (type === "input") {

    const input =
      document.createElement("input");

    input.placeholder =
      props.placeholder || "";

    body.appendChild(
      input
    );

  }


  else if (type === "button") {

    body.textContent =
      props.text || "";

    body.parentElement.addEventListener(
      "click",
      () => {
        sendEvent({
          type: "element_clicked",
          element_id: id,
        });
      }
    );

  }


  else if (type === "toggle") {

    const dot =
      document.createElement("span");

    dot.className = "dot";

    const text =
      document.createElement("span");

    text.className = "toggle-text";

    text.textContent =
      formatToggleValue(props.value);

    body.appendChild(dot);
    body.appendChild(text);

    setToggleDotState(dot, props.value);

    body.parentElement.addEventListener(
      "click",
      () => {
        sendEvent({
          type: "element_clicked",
          element_id: id,
        });
      }
    );

  }


  else if (
    type === "text" ||
    type === "panel"
  ) {

    body.textContent =
      props.text || "";

  }


  else if (type === "progress") {

    const bar =
      document.createElement("div");

    bar.className =
      "progress-bar";

    const fill =
      document.createElement("div");

    fill.className =
      "progress-fill";

    fill.style.width =
      (props.value || 0) + "%";

    bar.appendChild(fill);
    body.appendChild(bar);

  }


  else if (type === "agent_monitor") {

    const monitor =
      document.createElement("div");

    monitor.className =
      "agent-monitor";

    monitor.dataset.agent =
      props.agent || "";

    monitor.dataset.status =
      String(props.status || "IDLE").toLowerCase();

    monitor.innerHTML = `
      <div class="agent-monitor-status">
        <span class="agent-monitor-dot"></span>
        <span class="agent-monitor-status-text">
          ${props.status || "IDLE"}
        </span>
      </div>
      <div class="agent-monitor-activity">
        ${props.activity || "Waiting..."}
      </div>
      <div class="agent-monitor-progress">
        <div class="agent-monitor-progress-fill"></div>
      </div>
      <div class="agent-monitor-footer">
        <span class="agent-monitor-step">
          ${props.step ? "STEP " + props.step : ""}
        </span>
        <span class="agent-monitor-tool">
          ${props.tool || ""}
        </span>
        <span class="agent-monitor-job">
          ${props.job_id ? "JOB " + props.job_id : ""}
        </span>
      </div>
      <div class="agent-monitor-log"></div>
    `;

    body.appendChild(monitor);

    const fill =
      monitor.querySelector(
        ".agent-monitor-progress-fill"
      );

    fill.style.width =
      `${props.progress || 0}%`;

  }
}


// ---------------------------------------------------------------------
// Toggle
// ---------------------------------------------------------------------

function formatToggleValue(value) {
  return value ? "PÅ" : "AV";
}


function setToggleDotState(dotEl, value) {
  dotEl.classList.toggle(
    "on",
    !!value
  );
}


function applyProgressProps(id, props) {
  const e =
    elements[id];

  if (!e) {
    return;
  }

  const fill =
    e.dom.querySelector(
      ".progress-fill"
    );

  if (!fill) {
    return;
  }

  if (props.value === undefined) {
    return;
  }

  fill.style.width =
    props.value + "%";
}


function applyAgentMonitorProps(id, props) {
  const e =
    elements[id];

  if (!e) {
    return;
  }

  const monitor =
    e.dom.querySelector(
      ".agent-monitor"
    );

  if (!monitor) {
    return;
  }

  const status =
    monitor.querySelector(
      ".agent-monitor-status-text"
    );

  const activity =
    monitor.querySelector(
      ".agent-monitor-activity"
    );

  const fill =
    monitor.querySelector(
      ".agent-monitor-progress-fill"
    );

  const tool =
    monitor.querySelector(
      ".agent-monitor-tool"
    );

  const job =
    monitor.querySelector(
      ".agent-monitor-job"
    );

  const step =
    monitor.querySelector(
      ".agent-monitor-step"
    );

  if (props.status !== undefined && status) {
    status.textContent =
      props.status;
    monitor.dataset.status =
      String(props.status).toLowerCase();
  }

  if (props.activity !== undefined && activity) {
    activity.textContent =
      props.activity;
  }

  if (props.progress !== undefined && fill) {
    fill.style.width =
      `${Math.max(0, Math.min(100, props.progress))}%`;
  }

  if (props.tool !== undefined && tool) {
    tool.textContent =
      props.tool;
  }

  if (props.step !== undefined && step) {
    step.textContent =
      props.step ? `STEP ${props.step}` : "";
  }

  if (props.job_id !== undefined && job) {
    job.textContent =
      props.job_id
        ? `JOB ${props.job_id}`
        : "";
  }

  if (props.activity !== undefined) {
    const log =
      monitor.querySelector(
        ".agent-monitor-log"
      );

    if (log && props.activity) {
      const entry =
        document.createElement("div");

      entry.className =
        "agent-monitor-log-entry";

      const time =
        new Date().toLocaleTimeString(
          [],
          { hour: "2-digit", minute: "2-digit", second: "2-digit" }
        );

      const stepPrefix =
        props.step ? `#${props.step} ` : "";

      entry.innerHTML =
        `<span class="agent-monitor-log-time">${time}</span>` +
        `<span class="agent-monitor-log-text"></span>`;

      entry.querySelector(
        ".agent-monitor-log-text"
      ).textContent = stepPrefix + props.activity;

      log.appendChild(entry);

      while (log.children.length > 12) {
        log.removeChild(log.firstChild);
      }

      log.scrollTop = log.scrollHeight;
    }
  }
}


function applyToggleProps(id, props) {
  const e =
    elements[id];

  if (!e) {
    return;
  }

  const body =
    e.dom.querySelector(
      ".body"
    );

  if (!body) {
    return;
  }

  const dot =
    body.querySelector(
      ".dot"
    );

  const text =
    body.querySelector(
      ".toggle-text"
    );

  if (props.value === undefined) {
    return;
  }

  if (dot) {
    setToggleDotState(
      dot,
      props.value
    );
  }

  if (text) {
    text.textContent =
      formatToggleValue(
        props.value
      );
  }

  if (
    fields.props &&
    e.type === "agent_monitor"
  ) {
    applyAgentMonitorProps(
      id,
      fields.props
    );
  }
}


// ---------------------------------------------------------------------
// Remove
// ---------------------------------------------------------------------

function removeElementDom(id) {
  const e =
    elements[id];

  if (!e) {
    return;
  }

  e.dom.remove();

  delete elements[id];
  delete three[id];
}


// ---------------------------------------------------------------------
// Move
// ---------------------------------------------------------------------

function moveElementDom(
  id,
  x,
  y
) {
  const e =
    elements[id];

  if (!e) {
    return;
  }

  e.dom.style.left =
    x + "px";

  e.dom.style.top =
    y + "px";
}


// ---------------------------------------------------------------------
// Update
// ---------------------------------------------------------------------

function updateElementDom(
  id,
  fields
) {
  const e =
    elements[id];

  if (!e) {
    return;
  }


  if (
    fields.visible === false
  ) {
    removeElementDom(id);
    return;
  }


  if (
    fields.w !== undefined
  ) {
    e.dom.style.width =
      fields.w + "px";
  }


  if (
    fields.h !== undefined
  ) {
    e.dom.style.height =
      fields.h + "px";
  }


  if (
    fields.label !== undefined
  ) {
    const header =
      e.dom.querySelector(
        ".header"
      );

    if (header) {
      header.textContent =
        fields.label;
    }
  }


  if (
    fields.props &&
    e.type === "3d"
  ) {
    apply3DProps(
      id,
      fields.props
    );
  }


  if (
    fields.props &&
    e.type === "toggle"
  ) {
    applyToggleProps(
      id,
      fields.props
    );
  }


  if (
    fields.props &&
    e.type === "progress"
  ) {
    applyProgressProps(
      id,
      fields.props
    );
  }
}


// ---------------------------------------------------------------------
// Drag
// ---------------------------------------------------------------------

function makeDraggable(
  el,
  handleEl,
  id
) {
  let sx;
  let sy;
  let ox;
  let oy;

  let dragging = false;


  handleEl.addEventListener(
    "mousedown",
    (e) => {

      dragging = true;

      el.classList.add(
        "dragging"
      );

      sx = e.clientX;
      sy = e.clientY;

      ox = el.offsetLeft;
      oy = el.offsetTop;

      e.preventDefault();
    }
  );


  window.addEventListener(
    "mousemove",
    (e) => {

      if (!dragging) {
        return;
      }

      el.style.left =
        (
          ox +
          e.clientX -
          sx
        ) + "px";


      el.style.top =
        (
          oy +
          e.clientY -
          sy
        ) + "px";
    }
  );


  window.addEventListener(
    "mouseup",
    () => {

      if (!dragging) {
        return;
      }

      dragging = false;

      el.classList.remove(
        "dragging"
      );


      sendEvent({
        type: "element_moved",
        element_id: id,
        x: el.offsetLeft,
        y: el.offsetTop,
      });
    }
  );
}


// ---------------------------------------------------------------------
// Resize
// ---------------------------------------------------------------------

function makeResizable(
  el,
  handle,
  id
) {
  let sx;
  let sy;
  let sw;
  let sh;

  let resizing = false;


  handle.addEventListener(
    "mousedown",
    (e) => {

      resizing = true;

      sx = e.clientX;
      sy = e.clientY;

      sw = el.offsetWidth;
      sh = el.offsetHeight;

      e.stopPropagation();
      e.preventDefault();
    }
  );


  window.addEventListener(
    "mousemove",
    (e) => {

      if (!resizing) {
        return;
      }

      el.style.width =
        Math.max(
          60,
          sw +
          e.clientX -
          sx
        ) + "px";


      el.style.height =
        Math.max(
          40,
          sh +
          e.clientY -
          sy
        ) + "px";
    }
  );


  window.addEventListener(
    "mouseup",
    () => {

      if (!resizing) {
        return;
      }

      resizing = false;


      sendEvent({
        type: "element_resized",
        element_id: id,
        w: el.offsetWidth,
        h: el.offsetHeight,
      });
    }
  );
}


// ---------------------------------------------------------------------
// 3D
// ---------------------------------------------------------------------

function init3D(
  id,
  body,
  props
) {
  const w =
    body.clientWidth || 380;

  const h =
    body.clientHeight || 380;


  const scene =
    new THREE.Scene();


  const camera =
    new THREE.PerspectiveCamera(
      45,
      w / h,
      0.1,
      1000
    );


  camera.position.set(
    0,
    0,
    5
  );


  const renderer =
    new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
    });


  renderer.setSize(
    w,
    h
  );


  body.innerHTML = "";


  body.appendChild(
    renderer.domElement
  );


  const pointLight =
    new THREE.PointLight(
      0x00eaff,
      2,
      20
    );


  pointLight.position.set(
    2,
    2,
    4
  );


  scene.add(
    pointLight
  );


  scene.add(
    new THREE.AmbientLight(
      0x00eaff,
      0.3
    )
  );


  three[id] = {
    scene,
    camera,
    renderer,
    model: null,
  };


  if (
    props.model_path
  ) {
    loadModel(
      id,
      props.model_path,
      props
    );
  }


  (function animate() {

    requestAnimationFrame(
      animate
    );


    const ctx =
      three[id];


    if (!ctx) {
      return;
    }


    if (ctx.model) {
      ctx.model.rotation.y +=
        0.004;
    }


    ctx.renderer.render(
      ctx.scene,
      ctx.camera
    );

  })();
}


// ---------------------------------------------------------------------
// Load model
// ---------------------------------------------------------------------

function loadModel(
  id,
  path,
  props
) {
  const ctx =
    three[id];

  if (!ctx) {
    return;
  }


  const loader =
    new THREE.GLTFLoader();


  loader.load(

    path,

    (gltf) => {

      const model =
        gltf.scene;


      applyHologramMaterial(
        model,
        props
      );


      ctx.scene.add(
        model
      );


      ctx.model =
        model;


      const [
        x,
        y,
        z
      ] =
        props.position3d ||
        [0, 0, 0];


      model.position.set(
        x,
        y,
        z
      );


      if (props.scale) {
        model.scale.setScalar(
          props.scale
        );
      }
    },


    undefined,


    (err) => {
      console.error(
        "Kunde inte ladda 3D-modell:",
        err
      );
    }
  );
}


// ---------------------------------------------------------------------
// Hologram material
// ---------------------------------------------------------------------

function applyHologramMaterial(
  model,
  props
) {
  const color =
    new THREE.Color(
      props.color ||
      "#00eaff"
    );


  model.traverse(
    (child) => {

      if (!child.isMesh) {
        return;
      }


      child.material =
        new THREE.MeshStandardMaterial({
          color,
          emissive: color,
          emissiveIntensity: 0.6,

          wireframe:
            props.wireframe !== false,

          transparent: true,

          opacity:
            props.opacity ??
            0.85,
        });
    }
  );
}

function handleAgentMonitorUpdate(msg) {
  Object.entries(elements).forEach(
    ([id, element]) => {
      if (element.type !== "agent_monitor") {
        return;
      }

      const monitor =
        element.dom.querySelector(
          ".agent-monitor"
        );

      if (!monitor) {
        return;
      }

      if (
        monitor.dataset.agent &&
        monitor.dataset.agent !== msg.agent
      ) {
        return;
      }

      applyAgentMonitorProps(
        id,
        {
          status: msg.status,
          activity: msg.activity,
          progress: msg.progress,
          tool: msg.tool,
          job_id: msg.job_id,
          step: msg.step,
        }
      );
    }
  );
}


// ---------------------------------------------------------------------
// Update 3D
// ---------------------------------------------------------------------

function apply3DProps(
  id,
  props
) {
  const ctx =
    three[id];


  if (
    !ctx ||
    !ctx.model
  ) {
    return;
  }


  if (
    props.color ||
    props.wireframe !== undefined ||
    props.opacity !== undefined
  ) {
    applyHologramMaterial(
      ctx.model,
      props
    );
  }


  if (
    props.position3d
  ) {
    ctx.model.position.set(
      ...props.position3d
    );
  }


  if (props.scale) {
    ctx.model.scale.setScalar(
      props.scale
    );
  }
}

// =======================================================================
// Permanent chatt-input
// =======================================================================

const chatBar = document.getElementById("chat-bar");
const chatInput = document.getElementById("chat-input");
const chatSend = document.getElementById("chat-send");

function sendChatMessage() {
  const text = chatInput.value.trim();

  if (!text) {
    return;
  }

  sendEvent({
    type: "user_chat_message",
    content: text,
  });

  chatInput.value = "";
}

chatSend.addEventListener("click", sendChatMessage);

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    sendChatMessage();
  }
});


// =======================================================================
// Voice state: göm chatt-input, driv väckningscirkeln
// =======================================================================

const voiceCircle = document.getElementById("voice-circle");
const voiceCircleLabel = document.getElementById("voice-circle-label");

// Enkel jämnande (så cirkeln inte hackar mellan varje ljudsampel).
let smoothedLevel = 0;

function handleVoiceState(msg) {
  const voiceModeOn = !!msg.mode;

  chatBar.classList.toggle("hidden", voiceModeOn);
  voiceCircle.classList.toggle("hidden", !voiceModeOn);

  if (!voiceModeOn) {
    return;
  }

  voiceCircle.classList.toggle("awake", !!msg.awake);
  voiceCircle.classList.toggle("listening", !!msg.listening);

  voiceCircleLabel.textContent =
    msg.awake ? "lyssnar..." :
    msg.listening ? "väntar på \u201eBob\u201d..." :
    "vilar";

  if (typeof msg.level === "number") {
    // Rå RMS-nivå är ofta liten (t.ex. 0.001-0.3) - skala upp och klämm
    // fast mellan 0 och 1 så CSS-transformen blir tydlig.
    const scaled = Math.min(1, msg.level * 12);
    smoothedLevel += (scaled - smoothedLevel) * 0.35;

    const scale = 1 + smoothedLevel * 0.6;
    const glow = 20 + smoothedLevel * 60;

    voiceCircle.style.setProperty("--voice-scale", scale.toFixed(3));
    voiceCircle.style.setProperty("--voice-glow", glow.toFixed(0) + "px");
  }
}


// =======================================================================
// Live-svarswidget
// =======================================================================

const streamPanel = document.getElementById("stream-panel");
const streamBody = document.getElementById("stream-body");
const streamSettingsBtn = document.getElementById("stream-settings-btn");
const streamSettings = document.getElementById("stream-settings");
const streamClearBtn = document.getElementById("stream-clear-btn");

const STREAM_TYPES = ["text", "reasoning", "tool_call_chunk", "interrupt"];

// Panelens state (synlighet, position, storlek, filter) ägs av backend nu
// - dels så Bob kan styra den via sina GUI-verktyg, dels så den överlever
// omstart precis som fönster/element. Den här flaggan förhindrar att vårt
// eget "change"-event på en checkbox skickas tillbaka till servern när det
// egentligen bara var vi som applicerade ett inkommande state-meddelande.
let applyingRemotePanelState = false;

function applyStreamPanelState(s) {
  applyingRemotePanelState = true;

  streamPanel.classList.toggle("hidden", s.visible === false);

  if (typeof s.w === "number") {
    streamPanel.style.width = s.w + "px";
  }

  if (typeof s.h === "number") {
    streamPanel.style.maxHeight = "none";
    streamPanel.style.height = s.h + "px";
  }

  if (typeof s.x === "number" && typeof s.y === "number") {
    streamPanel.style.left = s.x + "px";
    streamPanel.style.top = s.y + "px";
    streamPanel.style.right = "auto";
  }

  if (s.filters) {
    STREAM_TYPES.forEach((t) => {
      const cb = document.getElementById(`show-${t}`);
      if (cb && typeof s.filters[t] === "boolean") {
        cb.checked = s.filters[t];
      }
    });
  }

  applyingRemotePanelState = false;
}

function sendStreamPanelFilters() {
  const filters = {};

  STREAM_TYPES.forEach((t) => {
    const cb = document.getElementById(`show-${t}`);
    filters[t] = cb ? cb.checked : true;
  });

  sendEvent({
    type: "stream_panel_updated",
    filters,
  });
}

function streamTypeEnabled(nodeType) {
  const cb = document.getElementById(`show-${nodeType}`);
  return cb ? cb.checked : true;
}

STREAM_TYPES.forEach((t) => {
  const cb = document.getElementById(`show-${t}`);
  if (cb) {
    cb.addEventListener("change", () => {
      if (applyingRemotePanelState) {
        return;
      }
      sendStreamPanelFilters();
    });
  }
});

streamSettingsBtn.addEventListener("click", () => {
  streamSettings.classList.toggle("open");
});

streamClearBtn.addEventListener("click", () => {
  streamBody.innerHTML = "";
});

let currentStreamRun = null;

function handleAgentStream(msg) {
  const nodeType = msg.node_type;
  const content = msg.content;

  // Turmarkör: skickas av backend inför varje ny AI-tur (oavsett om den
  // triggas av chatt, röst eller en bakgrundsjobb-notis). Visas alltid,
  // filtreras inte bort av inställningarna.
  if (nodeType === "turn") {
    currentStreamRun = null;

    const divider = document.createElement("div");
    divider.className = "stream-line stream-turn";

    streamBody.appendChild(divider);
    streamBody.scrollTop = streamBody.scrollHeight;
    return;
  }

  if (!content || !streamTypeEnabled(nodeType)) {
    return;
  }

  // Text/reasoning strömmar token för token - vi vill klistra ihop dem i
  // samma rad istället för att skapa en ny rad per token.
  if (
    (nodeType === "text" || nodeType === "reasoning") &&
    currentStreamRun &&
    currentStreamRun.dataset.nodeType === nodeType
  ) {
    currentStreamRun.textContent += content;
  } else {
    const line = document.createElement("div");

    line.className = `stream-line stream-${nodeType}`;
    line.dataset.nodeType = nodeType;
    line.textContent = content;

    streamBody.appendChild(line);

    if (nodeType === "text" || nodeType === "reasoning") {
      currentStreamRun = line;
    } else {
      currentStreamRun = null;
    }
  }

  streamBody.scrollTop = streamBody.scrollHeight;
}
