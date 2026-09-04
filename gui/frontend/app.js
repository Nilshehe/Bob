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


    case "metrics_tick":
      handleMetricsTick(msg);
      break;


    case "windows_list":
      handleWindowsList(msg);
      break;


    case "stream_panel_clear":
      if (streamBody) {
        streamBody.innerHTML = "";
      }
      break;


    case "theme_state":
      applyThemeState(msg);
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
// Theme (GUI-specen punkt 24-30, 45, 59-60)
// ---------------------------------------------------------------------
// Sätter Bobs tema som CSS-variabler på :root. --holo-blue/--holo-glow
// (de gamla, hårdkodade variablerna som resten av style.css redan
// använder) pekas om till samma accent så att BEFINTLIGA widgets också
// följer med när temat byts, utan att style.css behövde skrivas om.

function _hexToRgba(hex, alpha) {
  const h = (hex || "#00eaff").replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const r = parseInt(full.substring(0, 2), 16) || 0;
  const g = parseInt(full.substring(2, 4), 16) || 0;
  const b = parseInt(full.substring(4, 6), 16) || 0;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function applyThemeState(t) {
  const root = document.documentElement.style;

  root.setProperty("--bob-accent", t.accent);
  root.setProperty("--bob-accent-hue", (t.accent_hue ?? 189) + "deg");
  root.setProperty("--bob-background", t.background);
  root.setProperty("--bob-surface", t.surface);
  root.setProperty("--bob-text", t.text);
  root.setProperty("--bob-muted", t.muted);
  root.setProperty("--bob-error", t.error);
  root.setProperty("--bob-warning", t.warning);
  root.setProperty("--bob-success", t.success);

  // Bakåtkompatibilitet: gamla widgets (punkt 45).
  root.setProperty("--holo-blue", t.accent);
  root.setProperty("--holo-glow", _hexToRgba(t.accent, 0.35));
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
    id,
    data
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
// Settings-widget (element-typen "config_widget") - byggs helt från
// props (config.json-innehåll + ev. ollama-modellista + ev.
// has_api_key/check_result), ingen server-HTML inblandad. Anropas både
// vid skapande (buildBody) och vid uppdatering (updateElementDom) så
// att provider-byte, "testa modell"-resultat osv. speglas live.
// ---------------------------------------------------------------------
const CONFIG_AUTO_EXCLUDED = new Set([
  "tools", "interupt_tools", "provider", "model", "api_key_envs",
  "agents", "tts_engine",
]);

const CONFIG_PROVIDERS = {
  ollama: "Ollama (lokalt)",
  openai: "OpenAI",
  anthropic: "Anthropic",
  google_genai: "Google (Gemini)",
  groq: "Groq",
  mistralai: "Mistral",
  deepseek: "DeepSeek",
  xai: "xAI (Grok)",
  openrouter: "OpenRouter",
};

function renderConfigWidget(body, props, id) {
  body.innerHTML = "";
  body.classList.add("config-widget-body");

  const p = props || {};
  const config = p.config || {};
  const models = p.models || [];
  const checkResult = p.check_result || null;
  // check_results: {main: {...}, approval: {...}, edit_ai: {...}, ...}
  // - separat från gamla check_result (main-modellen), så varje
  // agents "testa modell"-knapp visar sitt eget resultat.
  const checkResults = p.check_results || {};
  const agents = config.agents || {};

  const root = document.createElement("div");
  root.className = "config-widget";

  const title = document.createElement("div");
  title.className = "config-title";
  const titleText = document.createElement("span");
  titleText.textContent = "BOB CONFIGURATION";
  title.appendChild(titleText);

  const closeBtn = document.createElement("button");
  closeBtn.className = "config-close-btn";
  closeBtn.title = "Stäng";
  closeBtn.textContent = "\u2715";
  closeBtn.addEventListener("click", (evt) => {
    evt.stopPropagation();
    sendEvent({ type: "html_action", element_id: id, action: "config_close" });
  });
  title.appendChild(closeBtn);

  root.appendChild(title);

  // Grupperade "kort" (iOS Settings-mönster) - varje addSection()
  // öppnar ett nytt kort och alla rader efter den (addToggle/
  // addTextInput/manuellt tillagda rader) landar i det kortet tills
  // nästa addSection() anropas.
  let currentGroup = root;

  function addSection(text) {
    const section = document.createElement("div");
    section.className = "config-section";
    section.textContent = text;
    root.appendChild(section);

    currentGroup = document.createElement("div");
    currentGroup.className = "config-group";
    root.appendChild(currentGroup);

    return currentGroup;
  }

  function addSubgroupTitle(text) {
    const t = document.createElement("div");
    t.className = "config-subgroup-title";
    t.textContent = text;
    currentGroup.appendChild(t);
  }

  function sendConfigEvent(action, extra) {
    sendEvent(Object.assign(
      { type: "html_action", element_id: id, action },
      extra || {}
    ));
  }

  function addToggle(path, label, value) {
    const row = document.createElement("div");
    row.className = "config-row";

    const text = document.createElement("span");
    text.textContent = label;

    const toggle = document.createElement("div");
    toggle.className = "config-toggle " + (value ? "on" : "off");

    const knob = document.createElement("span");
    knob.className = "config-toggle-knob";
    toggle.appendChild(knob);

    toggle.addEventListener("click", () => {
      sendConfigEvent("config_toggle", { config_path: path, value: !value });
    });

    row.appendChild(text);
    row.appendChild(toggle);
    currentGroup.appendChild(row);
  }

  function addTextInput(path, label, value, opts) {
    opts = opts || {};
    const row = document.createElement("div");
    row.className = "config-row" + (opts.block ? " config-row-block" : "");

    const text = document.createElement("span");
    text.textContent = label;
    row.appendChild(text);

    const input = document.createElement(opts.textarea ? "textarea" : "input");
    input.className = "config-input" + (opts.textarea ? " config-textarea" : "");
    if (!opts.textarea) input.type = opts.number ? "number" : "text";
    if (opts.number) input.step = "any";
    if (opts.placeholder) input.placeholder = opts.placeholder;
    input.value = value == null ? "" : value;

    input.addEventListener("change", () => {
      sendConfigEvent(opts.number ? "config_number" : "config_text", {
        config_path: path,
        value: input.value,
      });
    });

    row.appendChild(input);
    currentGroup.appendChild(row);
  }

  // Återanvändbar provider+modell-väljare, används både för
  // huvud-AI:n och för varje underagent (Approval/Edit/Research/
  // Code AI) så de kan köra olika modeller - och olika providers.
  // agentPath = null -> huvudmodellen (config.provider/config.model,
  // toppnivå, bakåtkompatibelt). agentPath = "agents.<key>" ->
  // underagent, sparas under config.agents.<key>.provider/model.
  function addModelPicker(agentPath, agentCfg, resultKey, defaultModelHint) {
    const currentProvider = (agentCfg && agentCfg.provider) || config.provider || "ollama";
    const providerConfigPath = agentPath ? `${agentPath}.provider` : "provider";
    const modelConfigPath = agentPath ? `${agentPath}.model` : "model";

    const providerRow = document.createElement("div");
    providerRow.className = "config-row";
    const providerLabel = document.createElement("span");
    providerLabel.textContent = "Provider";
    const providerSelect = document.createElement("select");
    providerSelect.className = "config-input";

    Object.entries(CONFIG_PROVIDERS).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      if (value === currentProvider) option.selected = true;
      providerSelect.appendChild(option);
    });

    providerSelect.addEventListener("change", () => {
      sendConfigEvent("config_provider", {
        config_path: providerConfigPath,
        value: providerSelect.value,
      });
    });

    providerRow.appendChild(providerLabel);
    providerRow.appendChild(providerSelect);
    currentGroup.appendChild(providerRow);

    const currentModel = (agentCfg && agentCfg.model) || "";

    if (currentProvider === "ollama") {
      if (models.length) {
        const row = document.createElement("div");
        row.className = "config-row";
        const label = document.createElement("span");
        label.textContent = "Modell";
        const select = document.createElement("select");
        select.className = "config-input";

        if (agentPath) {
          const blank = document.createElement("option");
          blank.value = "";
          blank.textContent = "(standard: " + (defaultModelHint || config.model || "-") + ")";
          if (!currentModel) blank.selected = true;
          select.appendChild(blank);
        }

        models.forEach((m) => {
          const option = document.createElement("option");
          option.value = m;
          option.textContent = m;
          if (m === currentModel) option.selected = true;
          select.appendChild(option);
        });

        select.addEventListener("change", () => {
          sendConfigEvent("config_model", {
            config_path: modelConfigPath,
            model: select.value,
            value: select.value,
          });
        });

        row.appendChild(label);
        row.appendChild(select);
        currentGroup.appendChild(row);
      } else {
        const hint = document.createElement("div");
        hint.className = "config-hint missing";
        hint.textContent = "Hittar ingen lokal Ollama (körs den på :11434?)";
        currentGroup.appendChild(hint);
      }
    } else {
      const envPath = `api_key_envs.${currentProvider}`;
      const envValue = (config.api_key_envs && config.api_key_envs[currentProvider]) || "";
      addTextInput(envPath, ".env-variabel för API-nyckel", envValue, {
        placeholder: currentProvider.toUpperCase() + "_API_KEY",
      });

      const hasKeyMap = p.has_api_key_by_provider || {};
      const hasKey = agentPath
        ? Boolean(hasKeyMap[currentProvider])
        : Boolean(p.has_api_key);
      const keyHint = document.createElement("div");
      keyHint.className = "config-hint " + (hasKey ? "ok" : "missing");
      keyHint.textContent = hasKey
        ? "\u2713 Nyckel hittad i .env"
        : "\u2717 Ingen nyckel hittad i .env under det namnet";
      currentGroup.appendChild(keyHint);

      addTextInput(modelConfigPath, "Modell", currentModel, {
        placeholder: agentPath ? (defaultModelHint || "t.ex. gpt-4o-mini") : "t.ex. gpt-4o-mini",
      });
    }

    const checkRow = document.createElement("div");
    checkRow.className = "config-row";
    const checkBtn = document.createElement("button");
    checkBtn.className = "config-check-model";
    checkBtn.textContent = "TESTA OM MODELLEN FINNS";
    checkBtn.addEventListener("click", () => {
      checkBtn.disabled = true;
      checkBtn.textContent = "TESTAR...";
      sendConfigEvent("config_check_model", { agent: resultKey });
    });
    checkRow.appendChild(checkBtn);
    currentGroup.appendChild(checkRow);

    const result = checkResults[resultKey] || (resultKey === "main" ? checkResult : null);
    if (
      result &&
      result.provider === currentProvider &&
      result.model === (currentModel || config.model)
    ) {
      const resultEl = document.createElement("div");
      resultEl.className = "config-hint " + (result.ok ? "ok" : "missing");
      resultEl.textContent = (result.ok ? "\u2713 " : "\u2717 ") + result.message;
      currentGroup.appendChild(resultEl);
    }
  }

  // --- TOOLS ---
  addSection("TOOLS");
  Object.entries(config.tools || {}).forEach(([name, value]) => {
    addToggle(`tools.${name}`, name, Boolean(value));
  });

  // --- APPROVAL (interrupt-on-tool) ---
  addSection("APPROVAL");
  Object.entries(config.interupt_tools || {}).forEach(([name, value]) => {
    addToggle(`interupt_tools.${name}`, name, Boolean(value));
  });

  // --- SETTINGS: auto-genererad från alla övriga toppnivå-nycklar i
  // config.json - nya nycklar dyker upp här av sig själva, ingen
  // kodändring behövs. Typ avgörs från värdets JS-typ: bool -> toggle,
  // number -> nummerfält, lång/flerradig sträng -> textarea, annars
  // ett vanligt textfält.
  addSection("SETTINGS");
  Object.entries(config).forEach(([key, value]) => {
    if (CONFIG_AUTO_EXCLUDED.has(key)) return;

    if (typeof value === "boolean") {
      addToggle(key, key, value);
    } else if (typeof value === "number") {
      addTextInput(key, key, value, { number: true });
    } else {
      const text = value == null ? "" : String(value);
      const block = text.length > 60 || text.includes("\n");
      addTextInput(key, key, text, { textarea: block, block });
    }
  });

  // --- TTS ENGINE: bara relevant/synlig när TALKING är på ---
  if (config.TALKING) {
    addSection("TTS");
    const row = document.createElement("div");
    row.className = "config-row";
    const label = document.createElement("span");
    label.textContent = "Röstmotor";
    const select = document.createElement("select");
    select.className = "config-input";
    const ttsEngines = { piper: "Piper", chatterbox: "Chatterbox (multilingual)" };
    const currentEngine = config.tts_engine || "piper";
    Object.entries(ttsEngines).forEach(([value, lbl]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = lbl;
      if (value === currentEngine) option.selected = true;
      select.appendChild(option);
    });
    select.addEventListener("change", () => {
      sendConfigEvent("config_text", { config_path: "tts_engine", value: select.value });
    });
    row.appendChild(label);
    row.appendChild(select);
    currentGroup.appendChild(row);
  }

  // --- MODEL (huvud-AI:n) ---
  addSection("MODEL");
  addModelPicker(null, null, "main");

  // --- AI PER AGENT: Approval/Edit/Research/Code AI kan var och en
  // köra en egen provider/modell, oberoende av huvud-AI:n och av
  // varandra. Tomt fält = ärver providerns default-modell (se
  // config_manager.get_agent_model).
  addSection("AI PER AGENT");
  addSubgroupTitle("Approval AI");
  addModelPicker("agents.approval", agents.approval, "approval", "qwen3:4b");
  addSubgroupTitle("Edit AI");
  addModelPicker("agents.edit_ai", agents.edit_ai, "edit_ai", "qwen3:4b");
  addSubgroupTitle("Research AI");
  addModelPicker("agents.research_ai", agents.research_ai, "research_ai", "qwen3:4b");
  addSubgroupTitle("Code AI");
  addModelPicker("agents.code_ai", agents.code_ai, "code_ai", "qwen3:4b");

  // --- RESTART ---
  const restart = document.createElement("button");
  restart.className = "config-restart";
  restart.textContent = "APPLY & RESTART";
  restart.addEventListener("click", () => {
    sendConfigEvent("config_restart");
  });
  root.appendChild(restart);

  body.appendChild(root);
}


// ---------------------------------------------------------------------
// Element body
// ---------------------------------------------------------------------

function buildBody(
  type,
  body,
  props,
  id,
  data
) {

  if (type === "html") {

    body.classList.add("html-widget-body");

    body.innerHTML =
      (data && data.html) || "";

    wireHtmlActions(body, id);
    applyCameraFeeds(body);

  }


  else if (type === "status") {

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
  else if (type === "config_widget") {
    renderConfigWidget(body, props, id);
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


  else if (type === "graph") {

    body.appendChild(buildGraphDom(id, props));

  }


  else if (type === "big_text") {

    body.parentElement.dataset.variable = props.variable || "";

    const big =
      document.createElement("div");

    big.className = "big-text-value";
    big.textContent = props.text ?? "";

    body.appendChild(big);

  }


  else if (type === "whiteboard") {

    const area =
      document.createElement("textarea");

    area.className = "whiteboard-area";
    area.value = props.text || "";
    area.placeholder = "Skriv här...";

    let debounceTimer = null;

    area.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        sendEvent({
          type: "element_text_changed",
          element_id: id,
          text: area.value,
        });
      }, 400);
    });

    body.appendChild(area);

  }
}


// ---------------------------------------------------------------------
// HTML-element - actions (punkt 10, 48) och lokal kamera (punkt 57A)
// ---------------------------------------------------------------------
// Interaktion i fri/mall-HTML går via data-bob-action (+ valfritt
// data-bob-value) istället för inline-JS (som saneras bort i backend
// ändå, se html_sanitizer.py). Klick hanteras för det mesta, men
// input/textarea/select skickar på "change" istället så man inte
// bombarderar Bob med ett event per tangenttryck.

function wireHtmlActions(container, id) {
  container.addEventListener("click", (evt) => {
    const target = evt.target.closest("[data-bob-action]");
    if (!target || !container.contains(target)) return;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

    sendEvent({
      type: "html_action",
      element_id: id,
      action: target.dataset.bobAction,
      value: target.dataset.bobValue ?? null,
    });
  });

  container.addEventListener("change", (evt) => {
    const target = evt.target.closest("[data-bob-action]");
    if (!target || !container.contains(target)) return;
    if (!["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

    const value =
      target.type === "checkbox" ? target.checked : target.value;

    sendEvent({
      type: "html_action",
      element_id: id,
      action: target.dataset.bobAction,
      value,
    });
  });
}

function applyCameraFeeds(container) {
  container.querySelectorAll('[data-bob-camera="local"]').forEach((videoEl) => {
    if (videoEl.dataset.bobCameraBound) return;
    videoEl.dataset.bobCameraBound = "1";

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      console.warn(
        "Bob GUI: kamera kräver https eller localhost - stöds inte här."
      );
      return;
    }

    navigator.mediaDevices
      .getUserMedia({ video: true })
      .then((stream) => {
        videoEl.srcObject = stream;
      })
      .catch((err) => {
        console.warn("Bob GUI: kunde inte starta kameran:", err);
      });
  });
}


// ---------------------------------------------------------------------
// Graf (diagram/graf-widget) - ren canvas, inget externt bibliotek.
// Håller en lokal punktbuffert per serie (seedad från backend-historiken
// vid create_element/sync, sedan påfylld live av metrics_tick) och ritar
// bara punkterna inom det valda tidsintervallet.
// ---------------------------------------------------------------------

const GRAPH_INTERVALS = [
  { label: "1 min", seconds: 60 },
  { label: "5 min", seconds: 300 },
  { label: "15 min", seconds: 900 },
  { label: "1 h", seconds: 3600 },
  { label: "Allt", seconds: null },
];

const graphs = {}; // id -> { seriesData: {name: [{t,v},...]}, intervalSeconds, canvas, select }

function buildGraphDom(id, props) {
  const wrap = document.createElement("div");
  wrap.className = "graph-wrap";

  const controls = document.createElement("div");
  controls.className = "graph-controls";

  const select = document.createElement("select");
  GRAPH_INTERVALS.forEach((opt) => {
    const o = document.createElement("option");
    o.value = String(opt.seconds);
    o.textContent = opt.label;
    select.appendChild(o);
  });
  select.value = String(props.interval_s || 300);

  controls.appendChild(select);
  wrap.appendChild(controls);

  const canvas = document.createElement("canvas");
  canvas.className = "graph-canvas";
  wrap.appendChild(canvas);

  const seriesData = {};
  (props.series || []).forEach((name) => {
    seriesData[name] = ((props.history || {})[name] || []).map((p) => ({ t: p.t, v: Number(p.v) || 0 }));
  });

  graphs[id] = {
    seriesData,
    seriesNames: props.series || [],
    intervalSeconds: select.value === "null" ? null : Number(select.value),
    canvas,
  };

  select.addEventListener("change", () => {
    graphs[id].intervalSeconds = select.value === "null" ? null : Number(select.value);
    drawGraph(id);
  });

  // Rita om vid storleksändring av elementet (resize-handtaget ändrar
  // bara CSS-storlek på wrappern, canvasens pixelbuffert måste synkas).
  requestAnimationFrame(() => drawGraph(id));

  if (window.ResizeObserver) {
    const ro = new ResizeObserver(() => drawGraph(id));
    ro.observe(wrap);
  }

  return wrap;
}

const GRAPH_COLORS = ["#00eaff", "#ff5fd1", "#ffd166", "#7dff8f"];

function drawGraph(id) {
  const g = graphs[id];
  if (!g || !g.canvas || !g.canvas.isConnected) {
    return;
  }

  const canvas = g.canvas;
  const rect = canvas.parentElement.getBoundingClientRect();
  const w = Math.max(20, rect.width);
  const h = Math.max(20, rect.height - 28);

  canvas.width = w;
  canvas.height = h;

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, w, h);

  const now = Date.now() / 1000;
  const since = g.intervalSeconds ? now - g.intervalSeconds : 0;

  let minV = Infinity;
  let maxV = -Infinity;
  const visible = {};

  g.seriesNames.forEach((name) => {
    const pts = (g.seriesData[name] || []).filter((p) => p.t >= since);
    visible[name] = pts;
    pts.forEach((p) => {
      if (p.v < minV) minV = p.v;
      if (p.v > maxV) maxV = p.v;
    });
  });

  if (!isFinite(minV) || !isFinite(maxV)) {
    ctx.fillStyle = "rgba(0, 234, 255, 0.5)";
    ctx.font = "11px monospace";
    ctx.fillText("Väntar på data...", 6, h / 2);
    return;
  }

  if (minV === maxV) {
    minV -= 1;
    maxV += 1;
  }

  const tMin = since || Math.min(...g.seriesNames.flatMap((n) => visible[n].map((p) => p.t)), now - 60);
  const tMax = now;

  const x = (t) => ((t - tMin) / Math.max(1, tMax - tMin)) * (w - 8) + 4;
  const y = (v) => h - 4 - ((v - minV) / (maxV - minV)) * (h - 8);

  g.seriesNames.forEach((name, i) => {
    const pts = visible[name];
    if (pts.length === 0) return;

    ctx.strokeStyle = GRAPH_COLORS[i % GRAPH_COLORS.length];
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    pts.forEach((p, idx) => {
      const px = x(p.t);
      const py = y(p.v);
      if (idx === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.stroke();
  });
}

function applyGraphProps(id, props) {
  const g = graphs[id];
  if (!g) return;

  if (props.history) {
    Object.entries(props.history).forEach(([name, pts]) => {
      g.seriesData[name] = (pts || []).map((p) => ({ t: p.t, v: Number(p.v) || 0 }));
    });
  }

  drawGraph(id);
}

function handleMetricsTick(msg) {
  const t = msg.t || Date.now() / 1000;

  Object.entries(graphs).forEach(([id, g]) => {
    g.seriesNames.forEach((name) => {
      // Serienamnen för tokens följer alltid "tokens:<agent>".
      if (!name.startsWith("tokens:")) return;
      const agent = name.slice("tokens:".length);
      if (!(agent in (msg.tokens || {}))) return;

      const buf = g.seriesData[name] || (g.seriesData[name] = []);
      buf.push({ t, v: msg.tokens[agent] });
      if (buf.length > 2000) buf.shift();
    });
    drawGraph(id);
  });

  // Stortext-widgets bundna till en Token Usage-variabel uppdateras live
  // samma väg, utan att behöva en separat polling-loop i frontend.
  Object.entries(elements).forEach(([id, el]) => {
    if (el.type !== "big_text") return;
    const varName = el.dom.dataset.variable;
    if (!varName || !varName.startsWith("Token Usage: ")) return;
    const agent = varName.slice("Token Usage: ".length);
    if (!(agent in (msg.tokens || {}))) return;
    const valueEl = el.dom.querySelector(".big-text-value");
    if (valueEl) valueEl.textContent = String(msg.tokens[agent]);
  });
}


function applyBigTextProps(id, props) {
  const e = elements[id];
  if (!e) return;

  if (props.variable !== undefined) {
    e.dom.dataset.variable = props.variable || "";
  }

  if (props.text !== undefined) {
    const valueEl = e.dom.querySelector(".big-text-value");
    if (valueEl) valueEl.textContent = props.text;
  }
}


function applyWhiteboardProps(id, props) {
  const e = elements[id];
  if (!e || props.text === undefined) return;

  const area = e.dom.querySelector(".whiteboard-area");
  if (!area) return;

  // Skriv inte över det användaren just nu håller på att skriva i den
  // här rutan - annars rycks markören undan varje gång Bob (eller ett
  // annat fönster) uppdaterar samma whiteboard.
  if (document.activeElement === area) return;

  if (area.value !== props.text) {
    area.value = props.text;
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
  delete graphs[id];
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


  if (
    fields.props &&
    e.type === "graph"
  ) {
    applyGraphProps(id, fields.props);
  }


  if (
    fields.props &&
    e.type === "big_text"
  ) {
    applyBigTextProps(id, fields.props);
  }


  if (
    fields.props &&
    e.type === "whiteboard"
  ) {
    applyWhiteboardProps(id, fields.props);
  }


  if (
    fields.props &&
    e.type === "config_widget"
  ) {
    const body = e.dom.querySelector(".body");
    if (body) {
      renderConfigWidget(body, fields.props, id);
    }
  }


  if (
    fields.html !== undefined &&
    e.type === "html"
  ) {
    const body =
      e.dom.querySelector(".body");

    if (body) {
      body.innerHTML = fields.html;
      wireHtmlActions(body, id);
      applyCameraFeeds(body);
    }
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
const streamHeader = document.getElementById("stream-header");
const streamSettingsBtn = document.getElementById("stream-settings-btn");
const streamHideBtn = document.getElementById("stream-hide-btn");
const streamSettings = document.getElementById("stream-settings");
const streamClearBtn = document.getElementById("stream-clear-btn");
const streamToggleTab = document.getElementById("stream-toggle-tab");
const streamResizeHandle = document.getElementById("stream-resize-handle");

let streamPanelVisible = true;
let streamTabHidden = false;

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

  if (typeof s.visible === "boolean") {
    streamPanelVisible = s.visible;
    streamToggleTab.classList.toggle("active", streamPanelVisible);
  }

  if (typeof s.tab_hidden === "boolean") {
    streamTabHidden = s.tab_hidden;
    streamToggleTab.classList.toggle("fully-hidden", streamTabHidden);
  }

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

  if (Array.isArray(s.windows)) {
    selectedWindows = s.windows;
    showAllWindowsCb.checked = selectedWindows.length === 0;
    renderWindowCheckboxes();
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

// ---------------------------------------------------------------------
// Live-svarswidget: vilka fönster den ska visas i
// ---------------------------------------------------------------------

const showAllWindowsCb = document.getElementById("show-all-windows");
const streamWindowList = document.getElementById("stream-window-list");

let knownWindows = [];     // [{window_id, title, ...}, ...] - från backend
let selectedWindows = [];  // window_id:n som är valda; tom lista = alla fönster

function renderWindowCheckboxes() {
  streamWindowList.innerHTML = "";

  knownWindows.forEach((w) => {
    const label = document.createElement("label");
    const cb = document.createElement("input");

    cb.type = "checkbox";
    cb.dataset.windowId = w.window_id;
    cb.checked = selectedWindows.length === 0 || selectedWindows.includes(w.window_id);

    cb.addEventListener("change", () => {
      if (applyingRemotePanelState) {
        return;
      }
      onWindowCheckboxChanged();
    });

    label.appendChild(cb);
    label.appendChild(document.createTextNode(
      " " + (w.title || w.window_id) +
      (w.window_id === windowId ? " (det här fönstret)" : "")
    ));

    streamWindowList.appendChild(label);
  });

  streamWindowList.classList.toggle("hidden", showAllWindowsCb.checked);
}

function onWindowCheckboxChanged() {
  const checked = Array.from(
    streamWindowList.querySelectorAll("input[type=checkbox]")
  )
    .filter((cb) => cb.checked)
    .map((cb) => cb.dataset.windowId);

  selectedWindows = checked;

  sendEvent({
    type: "stream_panel_updated",
    windows: checked,
  });
}

showAllWindowsCb.addEventListener("change", () => {
  if (applyingRemotePanelState) {
    return;
  }

  streamWindowList.classList.toggle("hidden", showAllWindowsCb.checked);

  if (showAllWindowsCb.checked) {
    selectedWindows = [];
    sendEvent({
      type: "stream_panel_updated",
      windows: [],
    });
  } else {
    onWindowCheckboxChanged();
  }
});

function handleWindowsList(msg) {
  knownWindows = msg.windows || [];
  renderWindowCheckboxes();
}

streamSettingsBtn.addEventListener("click", () => {
  streamSettings.classList.toggle("open");
});

streamToggleTab.addEventListener("click", () => {
  streamPanelVisible = !streamPanelVisible;
  streamPanel.classList.toggle("hidden", !streamPanelVisible);
  streamToggleTab.classList.toggle("active", streamPanelVisible);
  sendEvent({
    type: "stream_panel_updated",
    visible: streamPanelVisible,
  });
});

streamClearBtn.addEventListener("click", () => {
  streamBody.innerHTML = "";
});

// ---------------------------------------------------------------------
// Live-svarswidget: gömma helt (panel + flik) och ta fram igen
// ---------------------------------------------------------------------
// Skiljer sig från den vanliga toggle-fliken: den fliken ska alltid gå
// att klicka på för att ta fram panelen igen. Den här knappen gömmer
// ANDRA fliken också, så det finns ingen väg tillbaka i UI:t förutom
// snabbkommandot Ctrl+Shift+L (eller att Bob själv sätter tillbaka det
// via set_stream_panel).

function fullyHideStreamPanel() {
  streamPanelVisible = false;
  streamTabHidden = true;

  streamPanel.classList.add("hidden");
  streamToggleTab.classList.remove("active");
  streamToggleTab.classList.add("fully-hidden");

  sendEvent({
    type: "stream_panel_updated",
    visible: false,
    tab_hidden: true,
  });
}

function unhideStreamPanel() {
  streamPanelVisible = true;
  streamTabHidden = false;

  streamPanel.classList.remove("hidden");
  streamToggleTab.classList.remove("fully-hidden");
  streamToggleTab.classList.add("active");

  sendEvent({
    type: "stream_panel_updated",
    visible: true,
    tab_hidden: false,
  });
}

streamHideBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  fullyHideStreamPanel();
});

window.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "l") {
    e.preventDefault();
    unhideStreamPanel();
  }
});

// ---------------------------------------------------------------------
// Live-svarswidget: flytta (dra i headern) och ändra storlek (handtag
// nere till höger). Samma mönster som makeDraggable/makeResizable för
// vanliga gui-element, men skräddarsytt för panelen eftersom den inte
// är ett element i "elements"-registret och styrs via
// stream_panel_updated/stream_panel_state istället för
// element_moved/element_resized.
// ---------------------------------------------------------------------

(function makeStreamPanelDraggable() {
  let sx, sy, ox, oy;
  let dragging = false;

  streamHeader.addEventListener("mousedown", (e) => {
    // Låt klick på knapparna i headern (⚙/✕) fungera som vanligt
    // istället för att starta en drag.
    if (e.target.closest("button")) {
      return;
    }

    dragging = true;
    streamHeader.classList.add("dragging");

    sx = e.clientX;
    sy = e.clientY;
    ox = streamPanel.offsetLeft;
    oy = streamPanel.offsetTop;

    // Panelen är CSS-positionerad med "right" som standard. Byt till
    // left/top-styrning så den faktiskt kan dras fritt.
    streamPanel.style.right = "auto";

    e.preventDefault();
  });

  window.addEventListener("mousemove", (e) => {
    if (!dragging) {
      return;
    }

    streamPanel.style.left = (ox + e.clientX - sx) + "px";
    streamPanel.style.top = (oy + e.clientY - sy) + "px";
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) {
      return;
    }

    dragging = false;
    streamHeader.classList.remove("dragging");

    sendEvent({
      type: "stream_panel_updated",
      x: streamPanel.offsetLeft,
      y: streamPanel.offsetTop,
    });
  });
})();

(function makeStreamPanelResizable() {
  let sx, sy, sw, sh;
  let resizing = false;

  streamResizeHandle.addEventListener("mousedown", (e) => {
    resizing = true;

    sx = e.clientX;
    sy = e.clientY;
    sw = streamPanel.offsetWidth;
    sh = streamPanel.offsetHeight;

    e.stopPropagation();
    e.preventDefault();
  });

  window.addEventListener("mousemove", (e) => {
    if (!resizing) {
      return;
    }

    streamPanel.style.width = Math.max(220, sw + e.clientX - sx) + "px";
    streamPanel.style.maxHeight = "none";
    streamPanel.style.height = Math.max(120, sh + e.clientY - sy) + "px";
  });

  window.addEventListener("mouseup", () => {
    if (!resizing) {
      return;
    }

    resizing = false;

    sendEvent({
      type: "stream_panel_updated",
      w: streamPanel.offsetWidth,
      h: streamPanel.offsetHeight,
    });
  });
})();

let currentStreamRun = null;

function handleAgentStream(msg) {
  const nodeType = msg.node_type;
  const content = msg.content;

  // Approval AI:s text är också token-streamad.
  // Behandla den som vanlig text så att varje token inte blir en ny rad.
  const isTextStream =
    nodeType === "text" ||
    nodeType === "reasoning" ||
    nodeType === "approval_text";

  // Turmarkör: skickas av backend inför varje ny AI-tur.
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

  // Text/reasoning/Approval AI strömmar token för token.
  // Klistra ihop tokens i samma rad.
  if (
    isTextStream &&
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

    if (isTextStream) {
      currentStreamRun = line;
    } else {
      currentStreamRun = null;
    }
  }

  streamBody.scrollTop = streamBody.scrollHeight;
}
