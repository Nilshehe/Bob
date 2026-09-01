"""
html_sanitizer.py
Saniterar HTML som Bob skriver själv via create_html/update_html innan
den läggs in i DOM:en (GUI-specen punkt 31-32).

Blockerar <script>/<iframe>/<object>/<embed>/<style>/<link>/<meta>/<form>,
alla on*-eventattribut och javascript:/vbscript:-URLer. Interaktion ska
gå via data-bob-action/data-bob-value, inte inline-JS (punkt 32, 48).

Undantag: html_components.py's egna, betrodda mallar (t.ex. "browser")
körs INTE genom den här sanitizern - de är byggda av oss med escapade
värden, inte fri text från Bob.

Kraschar aldrig (punkt 49) - fångar interna fel och returnerar ett
felmeddelande i widgeten istället för att välta hela GUI:t.
"""
from html.parser import HTMLParser

ALLOWED_TAGS = {
    "div", "span", "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr",
    "button", "input", "textarea", "select", "option", "label",
    "table", "thead", "tbody", "tr", "td", "th",
    "img", "video", "audio", "source", "canvas",
    "ul", "ol", "li", "a", "strong", "em", "b", "i", "small", "code", "pre",
}

BLOCKED_TAGS = {"script", "iframe", "object", "embed", "style", "link", "meta", "base", "form"}

GLOBAL_ATTRS = {
    "class", "id", "style", "title", "data-bob-action", "data-bob-value",
}

TAG_ATTRS = {
    "img": {"src", "alt", "width", "height"},
    "video": {"src", "controls", "autoplay", "loop", "muted", "poster", "width", "height"},
    "audio": {"src", "controls", "autoplay", "loop", "muted"},
    "source": {"src", "type"},
    "input": {"type", "placeholder", "value", "checked", "disabled", "min", "max", "step"},
    "textarea": {"placeholder", "rows", "cols", "disabled"},
    "select": {"disabled"},
    "option": {"value", "selected"},
    "a": {"href", "target", "rel"},
    "table": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

# Bara ett litet, säkert urval CSS-egenskaper tillåts i style="" - inga
# url()/expression()-tricks, inget position:fixed som kan täcka skärmen.
ALLOWED_STYLE_PROPS = {
    "color", "background", "background-color", "border", "border-radius",
    "padding", "margin", "font-size", "font-weight", "font-style",
    "text-align", "display", "gap", "flex", "flex-direction",
    "align-items", "justify-content", "width", "height", "max-width",
    "max-height", "opacity", "overflow", "white-space", "line-height",
}


def _clean_style(style_value: str) -> str:
    out = []
    for decl in style_value.split(";"):
        if ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if prop not in ALLOWED_STYLE_PROPS:
            continue
        if "url(" in value.lower() or "expression(" in value.lower():
            continue
        out.append(f"{prop}: {value}")
    return "; ".join(out)


def _is_safe_url(value: str) -> bool:
    v = (value or "").strip().lower()
    return not (v.startswith("javascript:") or v.startswith("vbscript:"))


def _escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace('"', "&quot;")
        .replace("<", "&lt;").replace(">", "&gt;")
    )


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip_depth = 0  # inuti en blockerad tagg, t.ex. <script>...</script>

    def handle_starttag(self, tag, attrs):
        self._emit(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self._emit(tag, attrs, self_closing=True)

    def _emit(self, tag, attrs, self_closing):
        tag = tag.lower()

        if tag in BLOCKED_TAGS:
            self.skip_depth += 1
            return

        if self.skip_depth:
            return

        if tag not in ALLOWED_TAGS:
            # Okänd/ej vitlistad tagg: hoppa bara över taggen själv,
            # skriv aldrig ut den osanerad.
            return

        allowed = GLOBAL_ATTRS | TAG_ATTRS.get(tag, set())
        cleaned = []

        for name, value in attrs:
            name = (name or "").lower()
            value = value or ""

            if name.startswith("on"):
                continue  # onclick m.fl. - interaktion går via data-bob-action

            if name not in allowed:
                continue

            if name == "style":
                value = _clean_style(value)
                if not value:
                    continue

            if name in ("src", "href"):
                if not _is_safe_url(value):
                    continue
                if not (
                    value.startswith("http://")
                    or value.startswith("https://")
                    or value.startswith("/")
                    or value.startswith("data:image/")
                ):
                    continue

            cleaned.append(f'{name}="{_escape_attr(value)}"')

        attr_str = (" " + " ".join(cleaned)) if cleaned else ""
        self.out.append(f"<{tag}{attr_str}{' /' if self_closing else ''}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in BLOCKED_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag in ALLOWED_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.skip_depth:
            return
        self.out.append(_escape_text(data))


def sanitize_html(html: str) -> str:
    """Saneras godtycklig HTML från Bob. Kraschar aldrig - fångar interna
    parserfel och returnerar ett tydligt fel i widgeten istället (punkt 49)."""
    if not html:
        return ""
    try:
        parser = _Sanitizer()
        parser.feed(html)
        parser.close()
        return "".join(parser.out)
    except Exception as exc:
        return f'<div class="html-widget-error">HTML-fel: {_escape_text(str(exc))}</div>'
