import asyncio
import uuid
from typing import Any, Callable

import gui.backend.gui_server as gui_server

from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain.messages import AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver

import config_manager
from funktioner.response_cleaner import get_last_text
from funktioner.formater import formater
from voice.tts import speak


APPROVAL_MODEL = "qwen3:4b"  # default om inget annat är valt i settings (agents.approval)
MAX_TURNS = 6


# ---------------------------------------------------------------------------
# Approval state
# ---------------------------------------------------------------------------

_pending_decision: dict[str, Any] = {}


@tool
def approve() -> str:
    """Approve the pending action.

    Call this ONLY when the user has clearly indicated that the action
    is acceptable.
    """
    _pending_decision.clear()
    _pending_decision["type"] = "approve"
    return "Action approved."


@tool
def reject(message: str) -> str:
    """Reject the pending action.

    Call this ONLY when the user has explicitly rejected the action
    or clearly indicated that it should not happen.
    """
    _pending_decision.clear()
    _pending_decision["type"] = "reject"
    _pending_decision["message"] = message
    return "Action rejected."


# ---------------------------------------------------------------------------
# Approval AI
# ---------------------------------------------------------------------------
# Byggs om via reload_approval_agent() (anropas från main.py:s
# reload_agent() när "Apply & Restart" trycks i settings-widgeten) så
# ett providerbyte för Approval AI (config.json: agents.approval) slår
# igenom utan att hela Bob behöver startas om.

_approval_llm = None
_approval_agent = None
_approval_memory = InMemorySaver()


def _build_approval_agent():
    settings = config_manager.get_agent_settings("approval", APPROVAL_MODEL)
    llm = config_manager.make_chat_model(
        settings["provider"],
        settings["model"],
        temperature=0.3,
    )
    return llm, create_agent(
        model=llm,
        system_prompt=_SYSTEM_PROMPT,
        tools=[approve, reject],
        checkpointer=_approval_memory,
    )


def reload_approval_agent():
    """Bygger om Approval AI:s modell/agent från config.json (kallas
    från main.py:s reload_agent())."""
    global _approval_llm, _approval_agent
    _approval_llm, _approval_agent = _build_approval_agent()
    return {"ok": True}


_SYSTEM_PROMPT = (
    "You are representing the main AI to the user right now. "
    "The main AI wants to run a tool and needs the user's approval "
    "before proceeding.\n\n"

    "You will be provided with:\n"
    "- the tool name\n"
    "- the tool arguments\n"
    "- the main AI's reasoning\n\n"

    "Your task:\n"
    "1. Briefly explain in your own words what the main AI wants to do "
    "and why. Base this on the reasoning you received. Do not invent "
    "new reasons.\n"

    "2. Ask the user whether this is acceptable.\n"

    "3. Answer follow-up questions from the user using the same reasoning.\n"

    "4. As soon as the user gives a CLEAR approval, call the `approve` tool.\n"

    "5. As soon as the user clearly rejects the action, call the `reject` "
    "tool and provide a short message explaining why.\n"

    "6. Do NOT call approve or reject when the user's answer is ambiguous.\n"

    "7. Once you have called approve or reject, do not continue the "
    "conversation. The decision will be handled by the main AI.\n\n"

    "Always answer in Swedish.\n"
    "Do not over-reason. Keep responses short and to the point."
)


_approval_agent = None  # byggs av reload_approval_agent() nedan, direkt vid import

reload_approval_agent()


# ---------------------------------------------------------------------------
# GUI streaming
# ---------------------------------------------------------------------------

def _broadcast_approval_stream(
    node_type: str,
    content: str,
) -> None:
    """Send Approval AI output to the GUI live-feed."""

    if not content:
        return

    try:
        gui_server.broadcast_agent_stream({
            "type": "agent_stream",
            "node_type": f"approval_{node_type}",
            "content": content,
        })
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Stream one Approval AI turn
# ---------------------------------------------------------------------------

async def _stream_agent_turn(
    cfg: dict,
    msg: str,
) -> str:
    """
    Send one message to Approval AI.

    Everything that Approval AI produces is streamed to the terminal
    and GUI, including tool calls.

    IMPORTANT:
    approve()/reject() are normal tools, not interrupts. Therefore we
    explicitly monitor _pending_decision and stop processing the current
    turn as soon as a decision has been made.

    Uses astream() (not the blocking stream()) so this never freezes
    the main asyncio event loop - a blocking call here previously meant
    the bridge loop couldn't process incoming websocket messages (the
    user's GUI reply) while Approval AI was "thinking".
    """

    text_parts: list[str] = []
    printed_anything = False

    async for block in _approval_agent.astream(
        {
            "messages": [
                {
                    "role": "user",
                    "content": msg,
                }
            ]
        },
        config=cfg,
        stream_mode=["updates", "messages"],
        version="v2",
    ):

        block_type = block.get("type")
        token_data = block.get("data")

        # ---------------------------------------------------------------
        # IMPORTANT:
        # Tool execution happens through graph updates.
        #
        # approve()/reject() modify _pending_decision.
        #
        # We MUST check this before simply ignoring non-AIMessageChunk
        # blocks.
        # ---------------------------------------------------------------

        if block_type == "updates":

            if _pending_decision.get("type"):
                break

            continue

        # ---------------------------------------------------------------
        # Extract streamed token
        # ---------------------------------------------------------------

        token = (
            token_data[0]
            if isinstance(token_data, tuple)
            else token_data
        )

        if not isinstance(token, AIMessageChunk):
            continue

        response, node_type = get_last_text(
            token,
            block,
        )

        if not response:
            continue

        # ---------------------------------------------------------------
        # Keep the existing terminal output
        # ---------------------------------------------------------------

        formater(
            response,
            node_type,
        )

        # ---------------------------------------------------------------
        # Keep Approval AI visible in GUI
        #
        # This intentionally includes:
        # - approval_text
        # - approval_reasoning
        # - approval_tool_call_chunk
        # - approval_interrupt
        #
        # Tool calls are NOT hidden.
        # ---------------------------------------------------------------

        _broadcast_approval_stream(
            node_type,
            response,
        )

        # ---------------------------------------------------------------
        # Store normal text
        # ---------------------------------------------------------------

        if node_type == "text":
            text_parts.append(response)
            printed_anything = True

        # ---------------------------------------------------------------
        # A decision may have been executed during the same stream.
        #
        # Stop immediately. Do not allow Approval AI to generate another
        # assistant turn after approve/reject.
        # ---------------------------------------------------------------

        if _pending_decision.get("type"):
            break

    if printed_anything:
        print()

    return "".join(text_parts).strip()


# ---------------------------------------------------------------------------
# Public approval conversation
# ---------------------------------------------------------------------------

def run_approval_conversation(
    tool_name: str,
    args: dict,
    reasoning: str,
    get_user_reply: Callable[[str], Any],
    TALKING: bool = False,
) -> dict:
    """
    Run Approval AI until it calls approve() or reject().

    NOTE: this is a synchronous wrapper kept for backwards
    compatibility (main.py's interupt_identifier() used to call this
    directly, blocking). It now just drives the async implementation.
    Prefer arun_approval_conversation() from async code.
    """
    return asyncio.run(
        arun_approval_conversation(tool_name, args, reasoning, get_user_reply, TALKING)
    )


async def arun_approval_conversation(
    tool_name: str,
    args: dict,
    reasoning: str,
    get_user_reply: Callable[[str], Any],
    TALKING: bool = False,
) -> dict:
    """
    Run Approval AI until it calls approve() or reject().

    `get_user_reply(ai_text)` may be a plain function OR a coroutine
    function - both are supported, since the GUI-aware version needs
    to `await` the event queue (see main.py:_get_user_reply) while the
    voice-mode version blocks on the microphone in a thread.

    Returns:

        {"type": "approve"}

    or:

        {
            "type": "reject",
            "message": "..."
        }
    """

    thread_id = (
        f"approval_{uuid.uuid4().hex[:8]}"
    )

    cfg = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    # Make absolutely sure a previous approval cannot leak into
    # this conversation.
    _pending_decision.clear()

    msg = (
        f"Tool the main AI wants to run: {tool_name}\n"
        f"Arguments: {args}\n"
        f'Main AI\'s reasoning for this:\n'
        f'"""{reasoning or "(no reasoning available)"}"""\n\n'
        "Explain this to the user and ask if it is acceptable."
    )

    for _ in range(MAX_TURNS):

        # ---------------------------------------------------------------
        # Let Approval AI respond.
        # ---------------------------------------------------------------

        ai_text = await _stream_agent_turn(
            cfg,
            msg,
        )

        # ---------------------------------------------------------------
        # CRITICAL:
        # Check the decision immediately after the stream.
        #
        # If approve() or reject() ran, return to main.py NOW.
        #
        # DO NOT call get_user_reply().
        # ---------------------------------------------------------------

        decision_type = _pending_decision.get("type")

        if decision_type == "approve":
            return {
                "type": "approve",
            }

        if decision_type == "reject":
            return {
                "type": "reject",
                "message": _pending_decision.get(
                    "message",
                    "The user rejected the action.",
                ),
            }

        # ---------------------------------------------------------------
        # No decision yet.
        #
        # Now it is actually appropriate to listen to the user. This
        # awaits the event queue (or the mic thread, in voice mode) -
        # it does NOT block the event loop, so a GUI reply sent while
        # we're waiting here is picked up correctly (see main.py's
        # async _get_user_reply, which pulls the next "user_message"
        # event that arrives after the question was asked and removes
        # it from the queue so it isn't processed twice).
        # ---------------------------------------------------------------

        reply = get_user_reply(
            ai_text or "(no reply from the agent)"
        )
        msg = await reply if asyncio.iscoroutine(reply) else reply

        if TALKING and ai_text:
            try:
                await speak(ai_text)
            except Exception:
                pass

    # -------------------------------------------------------------------
    # Safety fallback
    # -------------------------------------------------------------------

    return {
        "type": "reject",
        "message": (
            "No clear confirmation from the user after "
            "several attempts."
        ),
    }