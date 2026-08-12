import uuid
from typing import Any, Callable
 
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langchain.messages import AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
 
from tools.shared_resources import GPU_LOCK
 
APPROVAL_MODEL = "qwen3:4b"
MAX_TURNS = 6  # tak på antal varv innan vi ger upp och avvisar säkert
 
# Verktygen kan bara returnera text till agenten, så vi mellanlagrar det
# faktiska beslutet här. Rensas i början av varje ny konversation.
_pending_decision: dict[str, Any] = {}
 
 
@tool
def approve() -> str:
    """Godkänn den väntande åtgärden. Anropa detta ENDAST när användaren
    tydligt har sagt att det är okej att huvud-AI:n kör åtgärden."""
    _pending_decision["type"] = "approve"
    return "Åtgärden är godkänd."
 
 
@tool
def reject(message: str) -> str:
    """Avvisa den väntande åtgärden. `message` ska vara en kort, tydlig
    förklaring riktad till huvud-AI:n om varför åtgärden inte får köras,
    så att den förstår vad den ska göra istället. Anropa detta ENDAST när
    användaren tydligt har sagt nej / inte gett sitt godkännande."""
    _pending_decision["type"] = "reject"
    _pending_decision["message"] = message
    return "Åtgärden är avvisad."
 
 
_approval_llm = ChatOllama(model=APPROVAL_MODEL)
_approval_memory = InMemorySaver()
 
_SYSTEM_PROMPT = (
    "Du representerar huvud-AI:n gentemot användaren just nu. Huvud-AI:n vill "
    "köra ett verktyg och behöver användarens godkännande innan det får ske. "
    "Du får veta vilket verktyg, vilka argument, och huvud-AI:ns eget "
    "resonemang för varför den vill göra detta.\n\n"
    "Din uppgift:\n"
    "1. Förklara kort, med egna ord, vad huvud-AI:n vill göra och varför -- "
    "basera dig på resonemanget du fått, hitta inte på nya skäl -- och fråga "
    "om det är okej.\n"
    "2. Svara på eventuella följdfrågor från användaren (t.ex. \"varför?\") "
    "genom att luta dig mot samma resonemang.\n"
    "3. Så fort användaren gett ett TYDLIGT svar -- godkännande eller avslag "
    "-- anropa verktyget approve eller reject. Vid reject: skriv ett message "
    "som förklarar för huvud-AI:n varför, baserat på vad användaren sa.\n"
    "Fortsätt bara konversationen i textform (utan att anropa något verktyg) "
    "om svaret ännu inte är tydligt."
)
 
_approval_agent = create_agent(
    model=_approval_llm,
    system_prompt=_SYSTEM_PROMPT,
    tools=[approve, reject],
    checkpointer=_approval_memory,
)
 
 
def _stream_agent_turn(cfg: dict, msg: str) -> str:
    """Skickar ett meddelande till godkännande-agenten och streamar svaret
    (reasoning + text) token för token till terminalen, samma stil som
    huvudagenten. approve()/reject() körs som vanligt internt i grafen om
    modellen anropar dem - inget interrupt på dem, så .stream() går hela
    vägen till slutet av det varvet automatiskt.
 
    Returnerar hela textsvaret (utan reasoning) som en sträng, för loggning.
    """
    text_parts: list[str] = []
    printed_anything = False
 
    with GPU_LOCK:
        for block in _approval_agent.stream(
            {"messages": [{"role": "user", "content": msg}]},
            config=cfg,
            stream_mode=["updates", "messages"],
            version="v2",
        ):
            block_type = block.get("type")
            token_data = block.get("data")
            token = token_data[0] if isinstance(token_data, tuple) else token_data
 
            if not isinstance(token, AIMessageChunk):
                continue
 
            for b in getattr(token, "content_blocks", None) or []:
                if b.get("type") == "text" and b.get("text"):
                    text_parts.append(b["text"])
                    print(f"\033[33m{b['text']}\033[0m", end="", flush=True)
                    printed_anything = True
                elif b.get("type") == "reasoning" and b.get("reasoning"):
                    print(f"\033[92m{b['reasoning']}\033[0m", end="", flush=True)
                    printed_anything = True
 
    if printed_anything:
        print()  # ny rad när strömmen är klar
 
    return "".join(text_parts).strip()
 
 
def run_approval_conversation(
    tool_name: str,
    args: dict,
    reasoning: str,
    get_user_reply: Callable[[str], str],
) -> dict:
    """
    Kör en konversation mellan användaren och godkännande-agenten tills den
    anropar approve() eller reject(message). Agentens svar streamas ut i
    terminalen medan de genereras.
 
    get_user_reply(ai_text): funktion som hämtar nästa svar från användaren
    (text eller transkriberad röst). ai_text skickas med ifall
    anroparen vill logga/spela upp det, men det är redan skrivet till
    terminalen av streamingen ovan innan get_user_reply anropas.
 
    Returnerar {"type": "approve"} eller {"type": "reject", "message": ...},
    samma format som main.py redan skickar vidare till resume_after_interrupt.
    """
    thread_id = f"approval_{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": thread_id}}
    _pending_decision.clear()
 
    msg = (
        f"Verktyg huvud-AI:n vill köra: {tool_name}\n"
        f"Argument: {args}\n"
        f'Huvud-AI:ns resonemang för detta:\n"""{reasoning or "(inget resonemang tillgängligt)"}"""\n\n'
        "Förklara detta för användaren och fråga om det är okej."
    )
 
    for _ in range(MAX_TURNS):
        ai_text = _stream_agent_turn(cfg, msg)
 
        if "type" in _pending_decision:
            return dict(_pending_decision)
 
        msg = get_user_reply(ai_text or "(inget svar från agenten)")
 
    return {
        "type": "reject",
        "message": "Ingen tydlig bekräftelse från användaren efter flera försök.",
    }