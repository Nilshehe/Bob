import uuid
from typing import Any, Callable
 
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langchain.messages import AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from funktioner.response_cleaner import get_last_text
from funktioner.formater import formater
from voice.tts import speak

 
APPROVAL_MODEL = "qwen3:4b"  # small model for approval agent, since it doesn't need to reason much
MAX_TURNS = 6  # cap on number of rounds before giving up and rejecting safely

# Tools can only return text to the agent, so we store the actual
# decision here. Cleared at the start of each new conversation.
_pending_decision: dict[str, Any] = {}
 
 
@tool
def approve() -> str:
    """Approve the pending action. Call this ONLY when the user has
    clearly indicated it is okay for the main AI to perform the action."""
    _pending_decision["type"] = "approve"
    return "Action approved."
 
 
@tool
def reject(message: str) -> str:
    """Reject the pending action. `message` should be a short, clear
    explanation directed to the main AI about why the action must not run,
    so it understands what to do instead. Call this ONLY when the user
    has explicitly said no / not given consent."""
    _pending_decision["type"] = "reject"
    _pending_decision["message"] = message
    return "Action rejected."
 
 
_approval_llm = ChatOllama(model=APPROVAL_MODEL)
_approval_memory = InMemorySaver()
 
_SYSTEM_PROMPT = (
    "You are representing the main AI to the user right now. The main AI "
    "wants to run a tool and needs the user's approval before proceeding. "
    "You will be provided with the tool name, the arguments, and the main "
    "AI's reasoning for why it wants to do this.\n\n"
    "Your task:\n"
    "1. Briefly explain, in your own words, what the main AI wants to do and "
    "   why — base this on the reasoning you received; do not invent new reasons — "
    "   and ask whether this is acceptable.\n"
    "2. Answer any follow-up questions from the user (e.g. \"why?\") by "
    "   leaning on the same reasoning.\n"
    "3. As soon as the user has given a CLEAR response — approval or rejection — "
    "   call the `approve` or `reject` tool. For `reject`: provide a `message` "
    "   that explains to the main AI why, based on what the user said.\n"
    "Continue the conversation in text only (without calling any tool) if the "
    "response is not yet clear."
    "Allways answer in swedish."
    "Dont over reason, keep it short and to the point."""
)
 
_approval_agent = create_agent(
    model=_approval_llm,
    system_prompt=_SYSTEM_PROMPT,
    tools=[approve, reject],
    checkpointer=_approval_memory,
)
 
 
def _stream_agent_turn(cfg: dict, msg: str) -> str:
    """Send a message to the approval agent and stream the response
    (reasoning + text) token-by-token to the terminal, same style as the
    main agent. `approve()`/`reject()` are invoked internally in the graph if
    the model calls them — no interrupt occurs for them, so `.stream()` runs
    to the end of that turn automatically.

    Returns the full text response (without reasoning) as a string for logging.
    """
    text_parts: list[str] = []
    printed_anything = False

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

        respones, node_type = get_last_text(token, block)
        formater(respones, node_type)
        if node_type == "text" and respones:
            text_parts.append(respones)
            printed_anything = True
        

 
    if printed_anything:
        print()  # ny rad när strömmen är klar
 
    return "".join(text_parts).strip()
 
 
def run_approval_conversation(
    tool_name: str,
    args: dict,
    reasoning: str,
    get_user_reply: Callable[[str], str],
    TALKING: bool = False,
) -> dict:
    """
    Run a conversation between the user and the approval agent until it calls
    `approve()` or `reject(message)`. The agent's responses are streamed to the
    terminal as they are generated.

    `get_user_reply(ai_text)`: function that obtains the next reply from the
    user (text or transcribed voice). `ai_text` is provided in case the
    caller wants to log/play it back, but it has already been printed to the
    terminal by the streaming above before `get_user_reply` is called.

    Returns {"type": "approve"} or {"type": "reject", "message": ...},
    the same format that `main.py` forwards to `resume_after_interrupt`.
    """
    thread_id = f"approval_{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": thread_id}}
    _pending_decision.clear()
 
    msg = (
        f"Tool the main AI wants to run: {tool_name}\n"
        f"Arguments: {args}\n"
        f'Main AI\'s reasoning for this:\n"""{reasoning or "(no reasoning available)"}"""\n\n'
        "Explain this to the user and ask if it is acceptable."
    )
 
    for _ in range(MAX_TURNS):
        ai_text = _stream_agent_turn(cfg, msg)
 
        if "type" in _pending_decision:
            return dict(_pending_decision)
 
        msg = get_user_reply(ai_text or "(no reply from the agent)")
        if TALKING:
            speak(ai_text)

 
    return {
        "type": "reject",
        "message": "No clear confirmation from the user after several attempts.",
    }