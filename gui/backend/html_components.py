"""
html_components.py
Färdiga HTML-mallar Bob kan välja via create_html_component() istället
för att skriva all HTML själv (GUI-specen punkt 7-8, 55-58).

Varje mall är en funktion props -> (html, default_w, default_h). Mallarna
byggs av oss med escapade värden - de körs INTE genom html_sanitizer.py
(det är bara till för fri text Bob skriver själv via create_html).
Enda undantaget som får använda <iframe> är "browser", eftersom den är en
betrodd systemmall och inte fri HTML (punkt 58).

Bob är inte begränsad till de här mallarna (punkt 9, 46) - create_html
finns kvar för allt som inte har en färdig mall.
"""
from html import escape as _esc

COMPONENTS = {}


def component(name):
    def deco(fn):
        COMPONENTS[name] = fn
        return fn
    return deco


def _raw_class(props: dict) -> str:
    return " raw" if props.get("raw") else ""


@component("text")
def _text(props):
    return f'<div class="html-c html-c-text">{_esc(str(props.get("text", "")))}</div>', 200, 80


@component("panel")
def _panel(props):
    return f'<div class="html-c html-c-panel">{_esc(str(props.get("text", "")))}</div>', 240, 140


@component("status")
def _status(props):
    text = _esc(str(props.get("status", "OK")))
    return f'<div class="html-c html-c-status"><span class="dot"></span>{text}</div>', 200, 60


@component("button")
def _button(props):
    action = _esc(str(props.get("action", "click")))
    label = _esc(str(props.get("text", "")))
    return (
        f'<button class="html-c html-c-button" data-bob-action="{action}">{label}</button>',
        160, 60,
    )


@component("input")
def _input(props):
    action = _esc(str(props.get("action", "input_changed")))
    placeholder = _esc(str(props.get("placeholder", "")))
    return (
        f'<input class="html-c html-c-input" placeholder="{placeholder}" '
        f'data-bob-action="{action}">',
        220, 60,
    )


@component("toggle")
def _toggle(props):
    value = bool(props.get("value"))
    variable = _esc(str(props.get("variable", "")))
    state_class = "on" if value else "off"
    return (
        f'<div class="html-c html-c-toggle {state_class}" data-bob-action="toggle" '
        f'data-bob-value="{variable}">'
        f'<span class="dot"></span><span class="toggle-text">{"ON" if value else "OFF"}</span>'
        f'</div>',
        160, 60,
    )


@component("progress")
def _progress(props):
    value = max(0, min(100, int(props.get("value", 0) or 0)))
    return (
        f'<div class="html-c html-c-progress">'
        f'<div class="html-c-progress-fill" style="width:{value}%"></div></div>',
        200, 40,
    )


@component("image")
def _image(props):
    src = _esc(str(props.get("src", "")))
    fit = props.get("fit", "cover")
    if fit not in ("cover", "contain", "fill"):
        fit = "cover"
    alt = _esc(str(props.get("alt", "")))
    return (
        f'<img class="html-c html-c-image{_raw_class(props)}" src="{src}" '
        f'alt="{alt}" style="object-fit:{fit}">',
        320, 220,
    )


@component("video")
def _video(props):
    src = _esc(str(props.get("src", "")))
    autoplay = "autoplay" if props.get("autoplay") else ""
    loop = "loop" if props.get("loop") else ""
    muted = "muted" if props.get("muted", True) else ""
    controls = "controls" if props.get("controls", True) else ""
    return (
        f'<video class="html-c html-c-video{_raw_class(props)}" src="{src}" '
        f'{autoplay} {loop} {muted} {controls}></video>',
        360, 220,
    )


@component("camera_feed")
def _camera_feed(props):
    source = props.get("source", "local")
    if source == "local":
        # Frontend begär getUserMedia() och binder strömmen till den här
        # videotaggen själv (app.js: applyCameraFeeds) - kräver
        # https/localhost, det är ett webbläsarkrav vi inte kan runda.
        body = (
            f'<video class="html-c html-c-camera{_raw_class(props)}" '
            f'data-bob-camera="local" autoplay muted playsinline></video>'
        )
    else:
        url = _esc(str(props.get("url", "")))
        body = (
            f'<img class="html-c html-c-camera{_raw_class(props)}" '
            f'data-bob-camera="stream" src="{url}">'
        )
    return body, 320, 240


@component("browser")
def _browser(props):
    # UNDANTAG: enda mallen som får använda <iframe> - betrodd systemkod,
    # inte fri HTML från Bob (se modul-docstringen och spec punkt 58).
    # Obs: många sajter blockerar inbäddning via X-Frame-Options/CSP: det
    # går inte att tillförlitligt detektera från JS, så vi visar bara en
    # hjälptext under iframen istället för att låtsas kunna upptäcka det.
    url = _esc(str(props.get("url", "about:blank")))
    show_bar = props.get("show_address_bar", False)
    bar = (
        f'<div class="html-c-browser-bar">'
        f'<input class="html-c-browser-url" value="{url}" data-bob-action="browser_navigate">'
        f'</div>'
        if show_bar else ""
    )
    return (
        f'<div class="html-c html-c-browser">{bar}'
        f'<iframe class="html-c-browser-frame" src="{url}" '
        f'sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>'
        f'<div class="html-c-browser-hint">Visar inte sidan? Vissa sajter tillåter inte '
        f'inbäddning (X-Frame-Options/CSP).</div>'
        f'</div>',
        640, 420,
    )

@component("config_widget")
def _config_widget(props):
    config = props.get("config", {})

    tools = config.get("tools", {})
    interrupt_tools = config.get("interupt_tools", {})

    rows = []

    rows.append(
        '<div class="config-title">BOB CONFIGURATION</div>'
    )

    rows.append(
        '<div class="config-section">TOOLS</div>'
    )

    for name, value in tools.items():
        rows.append(
            f'''
            <div class="config-row">
                <span>{_esc(name)}</span>
                <div
                    class="config-toggle {'on' if value else 'off'}"
                    data-bob-config="tools.{_esc(name)}"
                    data-bob-action="config_toggle"
                >
                    <span class="config-toggle-knob"></span>
                </div>
            </div>
            '''
        )

    rows.append(
        '<div class="config-section">APPROVAL</div>'
    )

    for name, value in interrupt_tools.items():
        rows.append(
            f'''
            <div class="config-row">
                <span>{_esc(name)}</span>
                <div
                    class="config-toggle {'on' if value else 'off'}"
                    data-bob-config="interupt_tools.{_esc(name)}"
                    data-bob-action="config_toggle"
                >
                    <span class="config-toggle-knob"></span>
                </div>
            </div>
            '''
        )

    rows.append(
        '<div class="config-section">FEATURES</div>'
    )

    for name in ("TALKING", "VOICE_MODE"):
        value = bool(config.get(name, False))

        rows.append(
            f'''
            <div class="config-row">
                <span>{_esc(name)}</span>
                <div
                    class="config-toggle {'on' if value else 'off'}"
                    data-bob-config="{_esc(name)}"
                    data-bob-action="config_toggle"
                >
                    <span class="config-toggle-knob"></span>
                </div>
            </div>
            '''
        )

    rows.append(
        '''
        <button
            class="config-restart"
            data-bob-action="config_restart"
        >
            APPLY &amp; RESTART
        </button>
        '''
    )

    html = (
        '<div class="html-c html-c-config-widget">'
        + "".join(rows)
        + "</div>"
    )

    return html, 420, max(500, 60 + len(rows) * 42)
