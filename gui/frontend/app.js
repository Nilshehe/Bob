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


  else if (
    type === "text" ||
    type === "panel"
  ) {

    body.textContent =
      props.text || "";

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