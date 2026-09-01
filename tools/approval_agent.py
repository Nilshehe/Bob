import uuid
from typing import Any, Callable

import gui.backend.gui_server as gui_server

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langchain.messages import AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver

from funktioner.response_cleaner import get_last_text
from funktioner.formater import formater
from voice.tts import speak


APPROVAL_MODEL = "qwen3:4b"
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

_approval_llm = ChatOllama(
    model=APPROVAL_MODEL,
)

_approval_memory = InMemorySaver()


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


_approval_agent = create_agent(
    model=_approval_llm,
    system_prompt=_SYSTEM_PROMPT,
    tools=[approve, reject],
    checkpointer=_approval_memory,
)


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

def _stream_agent_turn(
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
    """

    text_parts: list[str] = []
    printed_anything = False

    for block in _approval_agent.stream(
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
    get_user_reply: Callable[[str], str],
    TALKING: bool = False,
) -> dict:
    """
    Run Approval AI until it calls approve() or reject().

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

        ai_text = _stream_agent_turn(
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
        # Now it is actually appropriate to listen to the user.
        # ---------------------------------------------------------------

        msg = get_user_reply(
            ai_text or "(no reply from the agent)"
        )

        if TALKING and ai_text:
            try:
                speak(ai_text)
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