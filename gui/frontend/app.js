// app.js — Bob GUI runtime.

//

// Backend:

//   msg.type         = command

//   msg.element_type = element type

//

// Example:

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

      "Bob GUI: could not read WebSocket message:",

      err

    );

  }

};





ws.onclose = () => {

  console.warn(

    "Bob GUI: websocket closed"

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

      case "small_mode_update":

        handleSmallModeUpdate(msg);

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





    case "bob_menu_data":

      handleBobMenuData(msg);

      break;





    case "bob_dev_result":

      handleBobDevResult(msg);

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

// Sets Bob's theme as CSS variables on :root. --holo-blue/--holo-glow

// (the old, hardcoded variables the rest of style.css already uses)

// get repointed to the same accent so EXISTING widgets also follow

// along when the theme changes, without style.css needing a rewrite.



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



  // Backward compatibility: old widgets (point 45).

  root.setProperty("--holo-blue", t.accent);

  root.setProperty("--holo-glow", _hexToRgba(t.accent, 0.35));

}





// ---------------------------------------------------------------------

// Element type

// ---------------------------------------------------------------------



function getElementType(data) {



  // New create_element messages

  if (data.element_type) {

    return data.element_type;

  }



  // Backward compatibility for sync

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

      "Bob GUI: element is missing element_type:",

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

// Settings widget (element type "config_widget") - built entirely from

// props (config.json content + optional ollama model list + optional

// has_api_key/check_result), no server-side HTML involved. Called both

// on creation (buildBody) and on update (updateElementDom) so that

// provider changes, "test model" results etc. are reflected live.

// ---------------------------------------------------------------------

const CONFIG_AUTO_EXCLUDED = new Set([

  "tools", "interupt_tools", "provider", "model", "api_key_envs",

  "agents", "tts_engine", "chatterbox_voice", "chatterbox_language",

]);



const CONFIG_PROVIDERS = {

  ollama: "Ollama (local)",

  openai: "OpenAI",

  anthropic: "Anthropic",

  google_genai: "Google (Gemini)",

  groq: "Groq",

  mistralai: "Mistral",

  deepseek: "DeepSeek",

  xai: "xAI (Grok)",

  openrouter: "OpenRouter",

};



// Chatterbox supports these language codes (voice/tts_chatterbox.py:

// SUPPORTED_LANGUAGES) - duplicated here as {code: display name} since

// the frontend can't import from the Python file.

const CHATTERBOX_LANGUAGES = {

  sv: "Swedish", en: "English", de: "German", es: "Spanish",

  fr: "French", it: "Italian", nl: "Dutch", pl: "Polish",

  pt: "Portuguese", ru: "Russian", tr: "Turkish", ar: "Arabic",

  da: "Danish", el: "Greek", fi: "Finnish", he: "Hebrew",

  hi: "Hindi", ja: "Japanese", ko: "Korean", ms: "Malay",

  no: "Norwegian", sw: "Swahili", zh: "Chinese",

};



// Remembers which sections (cards) are expanded per settings

// widget-id and section name - otherwise a SINGLE toggle click (which

// rebuilds the whole widget from scratch, see renderConfigWidget) would

// collapse all cards again every time. Only persists for the session

// (module scope, not config.json) - it's UI state, not data.

const CONFIG_SECTION_OPEN = new Map();



function isConfigSectionOpen(widgetId, title) {

  return CONFIG_SECTION_OPEN.get(widgetId + ":" + title) === true;

}



function setConfigSectionOpen(widgetId, title, open) {

  CONFIG_SECTION_OPEN.set(widgetId + ":" + title, open);

}



function renderConfigWidget(body, props, id) {

  body.innerHTML = "";

  body.classList.add("config-widget-body");



  const p = props || {};

  const config = p.config || {};

  const models = p.models || [];

  const chatterboxVoices = p.chatterbox_voices || [];

  const checkResult = p.check_result || null;

  // check_results: {main: {...}, approval: {...}, edit_ai: {...}, ...}

  // - separate from the old check_result (the main model), so each

  // agent's "test model" button shows its own result.

  const checkResults = p.check_results || {};

  const agents = config.agents || {};

  const lastSaved = p.last_saved || null;



  const root = document.createElement("div");

  root.className = "config-widget";



  const title = document.createElement("div");

  title.className = "config-title";

  const titleText = document.createElement("span");

  titleText.textContent = "BOB CONFIGURATION";

  title.appendChild(titleText);



  // "Saved" confirmation - appears right after a change (backend

  // sends last_saved = {path, ts} on every config_toggle/config_text/

  // config_number) and fades itself out via the CSS animation

  // config-toast-fade. Without this there was no way to see whether a

  // change actually went through, only that the widget got rebuilt.

  if (lastSaved && lastSaved.ts) {

    const toast = document.createElement("div");

    toast.className = "config-saved-toast";

    toast.textContent = "\u2713 Saved";

    // New animation every time, even if the text/class is the same as

    // last time - otherwise the CSS animation won't replay.

    toast.style.animation = "none";

    root.appendChild(toast);

    requestAnimationFrame(() => { toast.style.animation = ""; });

  }



  const closeBtn = document.createElement("button");

  closeBtn.className = "config-close-btn";

  closeBtn.title = "Close";

  closeBtn.textContent = "\u2715";

  closeBtn.addEventListener("click", (evt) => {

    evt.stopPropagation();

    sendEvent({ type: "html_action", element_id: id, action: "config_close" });

  });

  title.appendChild(closeBtn);



  root.appendChild(title);



  // Grouped "cards" (iOS Settings pattern), now collapsible like

  // dropdown lists - every addSection() opens a new card and all

  // rows after it (addToggle/addTextInput/manually added rows)

  // land in that card until the next addSection() call. The heading is

  // clickable and toggles whether the card (config-group) is shown.

  let currentGroup = root;



  function addSection(text) {

    const isOpen = isConfigSectionOpen(id, text);



    const section = document.createElement("div");

    section.className = "config-section config-section-toggle" + (isOpen ? " open" : "");



    const chevron = document.createElement("span");

    chevron.className = "config-section-chevron";

    chevron.textContent = "\u25B8"; // ▸, rotated to ▾ via CSS when .open



    const label = document.createElement("span");

    label.textContent = text;



    section.appendChild(chevron);

    section.appendChild(label);

    root.appendChild(section);



    currentGroup = document.createElement("div");

    currentGroup.className = "config-group";

    if (!isOpen) currentGroup.style.display = "none";

    root.appendChild(currentGroup);



    section.addEventListener("click", () => {

      const nowOpen = !section.classList.contains("open");

      section.classList.toggle("open", nowOpen);

      currentGroup.style.display = nowOpen ? "" : "none";

      setConfigSectionOpen(id, text, nowOpen);

    });



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

      // Optimistic UI: flip immediately instead of waiting for

      // the server's update_element response (which otherwise makes the

      // toggle feel sluggish as soon as the websocket round trip takes

      // more than a couple of ms). Always gets overwritten by the real

      // renderConfigWidget render once the response arrives, so it never

      // stays "actually wrong".

      const next = !toggle.classList.contains("on");

      toggle.classList.toggle("on", next);

      toggle.classList.toggle("off", !next);

      sendConfigEvent("config_toggle", { config_path: path, value: next });

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



  // Reusable provider+model picker, used both for

  // the main AI and for every sub-agent (Approval/Edit/Research/

  // Code AI) so they can run different models - and different providers.

  // agentPath = null -> the main model (config.provider/config.model,

  // top-level, backward compatible). agentPath = "agents.<key>" ->

  // sub-agent, saved under config.agents.<key>.provider/model.

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

        hint.textContent = "Can't find a local Ollama (is it running on :11434?)";

        currentGroup.appendChild(hint);

      }

    } else {

      const envPath = `api_key_envs.${currentProvider}`;

      const envValue = (config.api_key_envs && config.api_key_envs[currentProvider]) || "";

      addTextInput(envPath, ".env variable for API key", envValue, {

        placeholder: currentProvider.toUpperCase() + "_API_KEY",

      });



      const hasKeyMap = p.has_api_key_by_provider || {};

      const hasKey = agentPath

        ? Boolean(hasKeyMap[currentProvider])

        : Boolean(p.has_api_key);

      const keyHint = document.createElement("div");

      keyHint.className = "config-hint " + (hasKey ? "ok" : "missing");

      keyHint.textContent = hasKey

        ? "\u2713 Key found in .env"

        : "\u2717 No key found in .env under that name";

      currentGroup.appendChild(keyHint);



      addTextInput(modelConfigPath, "Modell", currentModel, {

        placeholder: agentPath ? (defaultModelHint || "t.ex. gpt-4o-mini") : "t.ex. gpt-4o-mini",

      });

    }



    const checkRow = document.createElement("div");

    checkRow.className = "config-row";

    const checkBtn = document.createElement("button");

    checkBtn.className = "config-check-model";

    checkBtn.textContent = "TEST IF THE MODEL EXISTS";

    checkBtn.addEventListener("click", () => {

      checkBtn.disabled = true;

      checkBtn.textContent = "TESTING...";

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



  // --- SETTINGS: auto-generated from all other top-level keys in

  // config.json - new keys show up here on their own, no

  // code change needed. Type is decided from the value's JS type: bool -> toggle,

  // number -> number field, long/multi-line string -> textarea, otherwise

  // a plain text field.

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



  // --- TTS ENGINE: only relevant/visible when TALKING is on ---

  if (config.TALKING) {

    addSection("TTS");

    const row = document.createElement("div");

    row.className = "config-row";

    const label = document.createElement("span");

    label.textContent = "Voice engine";

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



    // Voice + language are only relevant for Chatterbox - Piper has a

    // single built-in Swedish voice (voice/tts.py: MODEL = "sv_SE-nst-

    // medium.onnx") and doesn't take a choice.

    if (currentEngine === "chatterbox") {

      const voiceRow = document.createElement("div");

      voiceRow.className = "config-row";

      const voiceLabel = document.createElement("span");

      voiceLabel.textContent = "Voice";

      const voiceSelect = document.createElement("select");

      voiceSelect.className = "config-input";



      const defaultOpt = document.createElement("option");

      defaultOpt.value = "";

      defaultOpt.textContent = "(Chatterbox default voice)";

      if (!config.chatterbox_voice) defaultOpt.selected = true;

      voiceSelect.appendChild(defaultOpt);



      chatterboxVoices.forEach((name) => {

        const option = document.createElement("option");

        option.value = name;

        option.textContent = name;

        if (name === config.chatterbox_voice) option.selected = true;

        voiceSelect.appendChild(option);

      });



      voiceSelect.addEventListener("change", () => {

        sendConfigEvent("config_text", { config_path: "chatterbox_voice", value: voiceSelect.value });

      });

      voiceRow.appendChild(voiceLabel);

      voiceRow.appendChild(voiceSelect);

      currentGroup.appendChild(voiceRow);



      if (!chatterboxVoices.length) {

        const hint = document.createElement("div");

        hint.className = "config-hint missing";

        hint.textContent = "No voice files in voice/voices/ - drop a .wav (5-15 sec) there for voice cloning.";

        currentGroup.appendChild(hint);

      }



      const langRow = document.createElement("div");

      langRow.className = "config-row";

      const langLabel = document.createElement("span");

      langLabel.textContent = "Language";

      const langSelect = document.createElement("select");

      langSelect.className = "config-input";

      const currentLang = config.chatterbox_language || "sv";

      Object.entries(CHATTERBOX_LANGUAGES).forEach(([code, name]) => {

        const option = document.createElement("option");

        option.value = code;

        option.textContent = name;

        if (code === currentLang) option.selected = true;

        langSelect.appendChild(option);

      });

      langSelect.addEventListener("change", () => {

        sendConfigEvent("config_text", { config_path: "chatterbox_language", value: langSelect.value });

      });

      langRow.appendChild(langLabel);

      langRow.appendChild(langSelect);

      currentGroup.appendChild(langRow);

    }

  }



  // --- MODEL (huvud-AI:n) ---

  addSection("MODEL");

  addModelPicker(null, null, "main");



  // --- AI PER AGENT: Approval/Edit/Research/Code AI can each

  // run their own provider/model, independent of the main AI and of

  // each other. Empty field = inherits the provider's default model (see

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

    area.placeholder = "Write here...";



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

// HTML elements - actions (point 10, 48) and local camera (point 57A)

// ---------------------------------------------------------------------

// Interaction in free/template HTML goes through data-bob-action (+ optional

// data-bob-value) instead of inline JS (which gets sanitized out on the

// backend anyway, see html_sanitizer.py). Clicks are handled for most of it, but

// input/textarea/select send on "change" instead so we don't

// bombard Bob with an event per keystroke.



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

        "Bob GUI: camera requires https or localhost - not supported here."

      );

      return;

    }



    navigator.mediaDevices

      .getUserMedia({ video: true })

      .then((stream) => {

        videoEl.srcObject = stream;

      })

      .catch((err) => {

        console.warn("Bob GUI: could not start the camera:", err);

      });

  });

}





// ---------------------------------------------------------------------

// Graph (chart/graph widget) - plain canvas, no external library.

// Keeps a local point buffer per series (seeded from the backend history

// on create_element/sync, then filled live by metrics_tick) and draws

// only the points within the selected time range.

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



  // Redraw on resize of the element (the resize handle only changes

  // the wrapper's CSS size, the canvas's pixel buffer must be re-synced).

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

    ctx.fillText("Waiting for data...", 6, h / 2);

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

      // Series names for tokens always follow "tokens:<agent>".

      if (!name.startsWith("tokens:")) return;

      const agent = name.slice("tokens:".length);

      if (!(agent in (msg.tokens || {}))) return;



      const buf = g.seriesData[name] || (g.seriesData[name] = []);

      buf.push({ t, v: msg.tokens[agent] });

      if (buf.length > 2000) buf.shift();

    });

    drawGraph(id);

  });



  // Big-text widgets bound to a Token Usage variable get updated live

  // the same way, without needing a separate polling loop in the frontend.

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



  // Don't overwrite what the user is currently typing in this

  // box - otherwise the cursor gets yanked away every time Bob (or

  // another window) updates the same whiteboard.

  if (document.activeElement === area) return;



  if (area.value !== props.text) {

    area.value = props.text;

  }

}





// ---------------------------------------------------------------------

// Toggle

// ---------------------------------------------------------------------



function formatToggleValue(value) {

  return value ? "ON" : "OFF";

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

        "Could not load 3D model:",

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

// Permanent chat input

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

// Voice state: hide chat input, drive the wake circle

// =======================================================================



const voiceCircle = document.getElementById("voice-circle");

const voiceCircleLabel = document.getElementById("voice-circle-label");



// Simple smoothing (so the circle doesn't stutter between audio samples).

let smoothedLevel = 0;

function showBobOptions(event) {
  // Remove any existing options
  const existing = document.getElementById("bob-options");
  if (existing) {
    existing.remove();
  }

  // Get voice circle position and size
  const voiceCircle = document.getElementById("voice-circle");
  if (voiceCircle) {
    const rect = voiceCircle.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const radius = Math.max(rect.width, rect.height) / 2;
    const offset = radius + 20; // distance from voice circle center to oval center
    const ovalWidth = 80;
    const ovalHeight = 40;

    const container = document.createElement("div");
    container.id = "bob-options";
    container.style.position = "fixed";
    // We do not set left/top on container; we will set on each oval.
    container.style.pointerEvents = "all";
    container.style.zIndex = "60";

    const options = [
      { label: "Apps", tab: "apps" },
      { label: "Widgets", tab: "widgets" },
      { label: "Developer Mode", tab: "dev" }
    ];

    options.forEach((opt, i) => {
      const angle = (i * 2 * Math.PI / 3) - Math.PI / 2; // start at top
      const ovalCenterX = centerX + Math.cos(angle) * offset;
      const ovalCenterY = centerY + Math.sin(angle) * offset;
      const left = ovalCenterX - ovalWidth / 2;
      const top = ovalCenterY - ovalHeight / 2;

      const oval = document.createElement("div");
      oval.className = "bob-option";
      oval.textContent = opt.label;
      oval.style.position = "fixed";
      oval.style.left = left + "px";
      oval.style.top = top + "px";
      oval.style.width = ovalWidth + "px";
      oval.style.height = ovalHeight + "px";
      oval.style.background = "rgba(0, 234, 255, 0.2)";
      oval.style.border = "1px solid rgba(0, 234, 255, 0.4)";
      oval.style.borderRadius = "20px";
      oval.style.display = "flex";
      oval.style.alignItems = "center";
      oval.style.justifyContent = "center";
      oval.style.cursor = "pointer";
      oval.style.transition = "background 0.2s ease";
      oval.onclick = () => {
        // Hide the options
        container.remove();
        // Switch to the tab
        bobMenuActiveTab = opt.tab;
        loadBobMenuTab(opt.tab);
        // Open the menu if not already open
        openBobMenu();
        // Ensure the tab is active
        bobMenuTabs.querySelectorAll("button").forEach(b => b.classList.toggle("active", b.dataset.tab === opt.tab));
      };
      oval.onmouseover = () => {
        oval.style.background = "rgba(0, 234, 255, 0.3)";
      };
      oval.onmouseout = () => {
        oval.style.background = "rgba(0, 234, 255, 0.2)";
      };
      container.appendChild(oval);
    });

    document.body.appendChild(container);

    // Hide options when clicking outside
    function hideOnOutsideClick(event) {
      if (!container.contains(event.target)) {
        container.remove();
        document.removeEventListener("click", hideOnOutsideClick);
      }
    }
    // Use setTimeout to avoid hiding on the same click
    setTimeout(() => {
      document.addEventListener("click", hideOnOutsideClick);
    }, 0);
  } else {
    // Fallback: use event position and display flex row (original behavior)
    const container = document.createElement("div");
    container.id = "bob-options";
    container.style.position = "fixed";
    container.style.left = event.clientX + "px";
    container.style.top = event.clientY + "px";
    container.style.display = "flex";
    container.style.gap = "10px";
    container.style.zIndex = "60";
    container.style.pointerEvents = "all";

    // Create the three oval options
    const options = [
      { label: "Apps", tab: "apps" },
      { label: "Widgets", tab: "widgets" },
      { label: "Developer Mode", tab: "dev" }
    ];

    options.forEach(opt => {
      const oval = document.createElement("div");
      oval.className = "bob-option";
      oval.textContent = opt.label;
      oval.style.width = "80px";
      oval.style.height = "40px";
      oval.style.background = "rgba(0, 234, 255, 0.2)";
      oval.style.border = "1px solid rgba(0, 234, 255, 0.4)";
      oval.style.borderRadius = "20px";
      oval.style.display = "flex";
      oval.style.alignItems = "center";
      oval.style.justifyContent = "center";
      oval.style.cursor = "pointer";
      oval.style.transition = "background 0.2s ease";
      oval.onclick = () => {
        // Hide the options
        container.remove();
        // Switch to the tab
        bobMenuActiveTab = opt.tab;
        loadBobMenuTab(opt.tab);
        // Open the menu if not already open
        openBobMenu();
        // Ensure the tab is active
        bobMenuTabs.querySelectorAll("button").forEach(b => b.classList.toggle("active", b.dataset.tab === opt.tab));
      };
      oval.onmouseover = () => {
        oval.style.background = "rgba(0, 234, 255, 0.3)";
      };
      oval.onmouseout = () => {
        oval.style.background = "rgba(0, 234, 255, 0.2)";
      };
      container.appendChild(oval);
    });

    document.body.appendChild(container);

    // Hide options when clicking outside
    function hideOnOutsideClick(event) {
      if (!container.contains(event.target)) {
        container.remove();
        document.removeEventListener("click", hideOnOutsideClick);
      }
    }
    // Use setTimeout to avoid hiding on the same click
    setTimeout(() => {
      document.addEventListener("click", hideOnOutsideClick);
    }, 0);
  }
}


function handleVoiceState(msg) {

  const voiceModeOn = !!msg.mode;



  chatBar.classList.toggle("hidden", voiceModeOn);



  // Bob Circle is ALWAYS visible now (ROADMAP #5) - no longer hidden

  // just because Voice Mode is off. In text mode it just shows "idle"

  // (idle-breathing); in Voice Mode the awake/listening classes take over.

  voiceCircle.classList.remove("hidden");

  voiceCircle.classList.toggle("idle", !voiceModeOn || (!msg.awake && !msg.listening));

  voiceCircle.classList.toggle("awake", voiceModeOn && !!msg.awake);

  voiceCircle.classList.toggle("listening", voiceModeOn && !!msg.listening);



  voiceCircleLabel.textContent = !voiceModeOn

    ? "idle"

    : msg.awake ? "listening..." :

      msg.listening ? "waiting for \u201eBob\u201d..." :

      "idle";



  if (voiceModeOn && typeof msg.level === "number") {

    // Raw RMS level is often small (e.g. 0.001-0.3) - scale up and clamp

    // fast between 0 and 1 so the CSS transform becomes clear.

    const scaled = Math.min(1, msg.level * 12);

    smoothedLevel += (scaled - smoothedLevel) * 0.35;



    const scale = 1 + smoothedLevel * 0.6;

    const glow = 20 + smoothedLevel * 60;



    voiceCircle.style.setProperty("--voice-scale", scale.toFixed(3));

    voiceCircle.style.setProperty("--voice-glow", glow.toFixed(0) + "px");

  }

}





// =======================================================================

// Bob Circle menu: Apps / Widgets / Developer Mode (ROADMAP #5)

// =======================================================================



const bobMenu = document.getElementById("bob-menu");

const bobMenuTabs = document.getElementById("bob-menu-tabs");

const bobMenuBody = document.getElementById("bob-menu-body");



let bobMenuOpen = false;

let bobMenuActiveTab = "apps";

let bobDevTools = [];



function openBobMenu() {

  bobMenuOpen = true;

  bobMenu.classList.add("open");

  loadBobMenuTab(bobMenuActiveTab);

}



function closeBobMenu() {

  bobMenuOpen = false;

  bobMenu.classList.remove("open");

}



function toggleBobMenu() {

  if (bobMenuOpen) {

    closeBobMenu();

  } else {

    openBobMenu();

  }

}



voiceCircle.addEventListener("click", (e) => {
    e.stopPropagation();
    showBobOptions(e);
});


document.addEventListener("click", (e) => {

  if (bobMenuOpen && !bobMenu.contains(e.target) && e.target !== voiceCircle) {

    closeBobMenu();

  }

});



bobMenuTabs.addEventListener("click", (e) => {

  const btn = e.target.closest("button[data-tab]");

  if (!btn) return;

  bobMenuTabs.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));

  bobMenuActiveTab = btn.dataset.tab;

  loadBobMenuTab(bobMenuActiveTab);

});



function loadBobMenuTab(tab) {

  bobMenuBody.innerHTML = "<div class=\"bob-menu-empty\">loading...</div>";

  if (tab === "apps") {

    sendEvent({ type: "bob_menu_action", action: "list_apps" });

  } else if (tab === "widgets") {

    sendEvent({ type: "bob_menu_action", action: "list_widgets" });

  } else if (tab === "dev") {

    sendEvent({ type: "bob_menu_action", action: "list_dev_tools" });

  }

}



function handleBobMenuData(msg) {

  if (!bobMenuOpen || msg.tab !== bobMenuActiveTab) {

    return;

  }

  if (msg.tab === "apps") {

    renderBobApps(msg.apps || []);

  } else if (msg.tab === "widgets") {

    renderBobWidgets(msg.widgets || []);

  } else if (msg.tab === "dev") {

    bobDevTools = msg.tools || [];

    renderBobDevList();

  }

}



function renderBobApps(apps) {

  if (!apps.length) {

    bobMenuBody.innerHTML = "<div class=\"bob-menu-empty\">No apps available.</div>";

    return;

  }

  bobMenuBody.innerHTML = apps.map((a) => `

    <div class="bob-menu-row">

      <span>${a.label}</span>

      <button type="button" data-app="${a.id}">Open</button>

    </div>

  `).join("");

  bobMenuBody.querySelectorAll("button[data-app]").forEach((btn) => {

    btn.addEventListener("click", () => {

      sendEvent({ type: "bob_menu_action", action: "open_app", app_id: btn.dataset.app });

      closeBobMenu();

    });

  });

}



function renderBobWidgets(widgets) {
    let html = `
      <div class="bob-menu-row">
        <button type="button" data-action="add_widget">Add widget</button>
      </div>
    `;

    if (widgets.length) {
        html += widgets.map(w => `
          <div class="bob-menu-row">
            <span>${w.label || w.type} <span style="opacity:.5">(${w.element_id})</span></span>
            <button type="button" data-remove="${w.element_id}">Remove</button>
          </div>
        `).join("");
    } else {
        html += `<div class="bob-menu-empty">No widgets open at the moment.</div>`;
    }

    bobMenuBody.innerHTML = html;

    // Handle add widget button
    const addButton = bobMenuBody.querySelector('button[data-action="add_widget"]');
    if (addButton) {
        addButton.addEventListener('click', () => {
            sendEvent({ type: 'bob_menu_action', action: 'add_widget' });
            loadBobMenuTab('widgets');
        });
    }

    // Handle remove buttons
    bobMenuBody.querySelectorAll('button[data-remove]').forEach(btn => {
        btn.addEventListener('click', () => {
            sendEvent({ type: 'bob_menu_action', action: 'remove_widget', element_id: btn.dataset.remove });
            loadBobMenuTab('widgets');
        });
    });
}
function renderBobDevList() {

  if (!bobDevTools.length) {

    bobMenuBody.innerHTML = "<div class=\"bob-menu-empty\">No tools found.</div>";

    return;

  }

  const options = bobDevTools.map((t) => `<option value="${t.name}">${t.name}</option>`).join("");

  bobMenuBody.innerHTML = `

    <select id="bob-dev-tool-select">${options}</select>

    <div id="bob-dev-tool-desc"></div>

    <textarea id="bob-dev-args-input" placeholder='{"key": "value"}'>{}</textarea>

    <button type="button" id="bob-dev-run-btn">Run</button>

    <div id="bob-dev-result"></div>

  `;

  const select = document.getElementById("bob-dev-tool-select");

  const desc = document.getElementById("bob-dev-tool-desc");

  const runBtn = document.getElementById("bob-dev-run-btn");



  function syncDesc() {

    const t = bobDevTools.find((x) => x.name === select.value);

    desc.textContent = t ? t.description : "";

  }

  select.addEventListener("change", syncDesc);

  syncDesc();



  runBtn.addEventListener("click", () => {

    let args = {};

    try {

      args = JSON.parse(document.getElementById("bob-dev-args-input").value || "{}");

    } catch (err) {

      document.getElementById("bob-dev-result").textContent = "Ogiltig JSON: " + err.message;

      return;

    }

    document.getElementById("bob-dev-result").textContent = "running...";

    sendEvent({ type: "bob_menu_action", action: "run_dev_tool", tool_name: select.value, args });

  });

}



function handleBobDevResult(msg) {

  const resultEl = document.getElementById("bob-dev-result");

  if (!resultEl) {

    return;

  }

  resultEl.textContent = msg.ok

    ? JSON.stringify(msg.result, null, 2)

    : "Fel: " + msg.error;

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



// Panel's state (visibility, position, size, filter) is now owned by backend

// - partly so Bob can control it via his GUI tools, partly so it survives

// restart exactly like windows/elements. This flag prevents our

// own "change" event on a checkbox from being sent back to the server when it

// was really just us applying an incoming state message.

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

// Live-response widget: which windows it should be shown in

// ---------------------------------------------------------------------



const showAllWindowsCb = document.getElementById("show-all-windows");

const streamWindowList = document.getElementById("stream-window-list");



let knownWindows = [];     // [{window_id, title, ...}, ...] - from backend

let selectedWindows = [];  // window_ids that are selected; empty list = all windows



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

      (w.window_id === windowId ? " (this window)" : "")

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

// Live-response widget: hide completely (panel + tab) and show again

// ---------------------------------------------------------------------

// Differs from the regular toggle tab: that tab should always be clickable

// to bring the panel back. This button hides

// OTHER tab as well, so there is no way back in the UI except

// the keyboard shortcut Ctrl+Shift+L (or if Bob himself sets it back)

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

// Live-response widget: move (drag header) and change size (handle

// bottom right). Same pattern as makeDraggable/makeResizable for

// regular GUI elements, but customized for the panel because it is not

// an element in the "elements" registry and is controlled via

// stream_panel_updated/stream_panel_state instead of

// element_moved/element_resized.

// ---------------------------------------------------------------------



(function makeStreamPanelDraggable() {

  let sx, sy, ox, oy;

  let dragging = false;



  streamHeader.addEventListener("mousedown", (e) => {

    // Let clicks on buttons in the header (✓/✔) work as usual

    // instead of starting a drag.

    if (e.target.closest("button")) {

      return;

    }



    dragging = true;

    streamHeader.classList.add("dragging");



    sx = e.clientX;

    sy = e.clientY;

    ox = streamPanel.offsetLeft;

    oy = streamPanel.offsetTop;



    // The panel is CSS-positioned with "right" by default. Switch to

    // left/top control so it can actually be dragged freely.

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



  // Approval AI's text is also token-streamed.

  // Treat it as plain text so that each token does not become a new line.

  const isTextStream =

    nodeType === "text" ||

    nodeType === "reasoning" ||

    nodeType === "approval_text";



  // Turn marker: sent by backend for each new AI turn.

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



  // Text/reasoning/Approval AI streams token by token.

  // Glue tokens together on the same line.

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





