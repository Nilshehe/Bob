from langchain_ollama import ChatOllama
from langchain.agents.middleware import HumanInTheLoopMiddleware, AgentMiddleware
from langchain.agents import create_agent
from langchain.messages import AIMessageChunk, ToolMessage
from funktioner.response_cleaner import get_last_text
from funktioner.formater import formater
from langgraph.types import Command
from typing import Any
from voice.live_stt import stt_main
from voice.wake_word import wait_for_wake_word
from tools.code_ai import register_notify_callback
from tools.research_ai import register_notify_callback as register_research_notify_callback
from tools.edit_ai import register_notify_callback as register_edit_notify_callback
from tools.approval_agent import run_approval_conversation
from voice.tts import speak
import asyncio
import sys
import queue
import threading
from funktioner.queue import event_queue
from funktioner import metrics
from gui.backend.registry import ToolRegistry
from gui.backend.main_gui import launch_gui
import gui.backend.gui_server as gui_server
from langchain_core.tools import tool
from voice.state import register_state_callback
from config_manager import load_config, get_enabled_tools,set_config_value

event_loop_instance = None

def _on_code_job_done(job_id: str, result: str) -> None:
    if event_loop_instance is None:
        return
    asyncio.run_coroutine_threadsafe(
        event_queue.put({
            "type": "code_ai_finished",
            "job_id": job_id,
            "result": result
        }),
        event_loop_instance
    )

def _on_research_job_done(job_id: str, result: str) -> None:
    if event_loop_instance is None:
        return
    asyncio.run_coroutine_threadsafe(
        event_queue.put({
            "type": "research_ai_finished",
            "job_id": job_id,
            "result": result
        }),
        event_loop_instance
    )

def _on_edit_job_done(job_id: str, result: str) -> None:
    if event_loop_instance is None:
        return
    asyncio.run_coroutine_threadsafe(
        event_queue.put({
            "type": "edit_ai_finished",
            "job_id": job_id,
            "result": result
        }),
        event_loop_instance
    )

register_notify_callback(_on_code_job_done)
register_research_notify_callback(_on_research_job_done)
register_edit_notify_callback(_on_edit_job_done)



# Satt av _set_voice_mode varje gång Voice Mode växlar (oavsett om det
# är Bob själv, toggle-knappen i GUI:t eller ett annat verktygsanrop som
# gör det). input_loop nollställer den precis innan den börjar vänta på
# text/wake word och pollar den under tiden, så att en pågående väntan
# kan avbrytas direkt istället för att sitta fast tills nästa
# textrad/wake word råkar komma in.
_voice_mode_changed = threading.Event()


def _broadcast_voice_state(**fields):
    """Skickar röstläges-status (på/av, vaken, lyssnar, ljudnivå) till alla
    öppna GUI-fönster, så den permanenta text-inputen kan gömmas och
    väckningscirkeln kan animeras i realtid."""
    try:
        gui_server.manager.broadcast({"type": "voice_state", **fields})
    except Exception:
        pass
    
def _voice_state_to_gui(**fields):
    _broadcast_voice_state(**fields)


register_state_callback(_voice_state_to_gui)


def _broadcast_agent_stream(node_type, content):
    """Speglar samma svarsström som formater() skriver ut i terminalen till
    GUI:ts live-svarswidget (text/reasoning/tool_call_chunk/interrupt).
    Skickas bara till de fönster som är valda i svarswidgetens
    fönster-filter (tom lista = alla fönster)."""
    if not content:
        return
    try:
        gui_server.broadcast_agent_stream({
            "type": "agent_stream",
            "node_type": node_type,
            "content": content,
        })
    except Exception:
        pass


def _emit(response, node_type):
    """Skriver ut i terminalen (som förut) och speglar samtidigt till
    GUI:ts live-svarswidget."""
    formater(response, node_type)
    _broadcast_agent_stream(node_type, response)

from langgraph.checkpoint.memory import InMemorySaver
memory_saver = InMemorySaver()


app_config = load_config()

#set talking and voice mode
VOICE_MODE = bool(app_config.get("VOICE_MODE", False))
TALKING = bool(app_config.get("TALKING", False))

#get gui information tool
from gui.backend.bob_integration import gui_system_prompt
@tool
def get_gui_information():
    """run this tool to get to know how to manage the GUI"""
    return gui_system_prompt()

from tools.ddgs_tool import web_search
from tools.sok_visible import search_visible_webpage, download_file, move_file, get_clickable_elements, click_on_page, type_into_page, scroll_page, click_and_download, get_page_text, open_browser
from tools.code_ai import code_ai, code_ai_status
from tools.research_ai import research_ai, research_ai_status
from tools.quit import shutdown_ai
from tools.model3d_tools import get_tools as get_model3d_tools
from tools.model3d_complex_shapes import get_complex_tools as get_model3d_complex_tools
from tools.edit_ai import edit_ai, edit_ai_status, apply_edit_files, list_apply_backups, restore_from_backup
from gui.backend.bob_integration import get_langchain_tools
from tools.clipboard import copy_to_clipboard
gui_tools = get_langchain_tools()

AVAILABLE_TOOLS = {
    "copy_to_clipboard": copy_to_clipboard,

    "web_search": web_search,

    "search_visible_webpage": search_visible_webpage,
    "download_file": download_file,
    "move_file": move_file,
    "get_clickable_elements": get_clickable_elements,
    "click_on_page": click_on_page,
    "type_into_page": type_into_page,
    "scroll_page": scroll_page,
    "click_and_download": click_and_download,
    "get_page_text": get_page_text,
    "open_browser": open_browser,

    "code_ai": code_ai,
    "code_ai_status": code_ai_status,

    "research_ai": research_ai,
    "research_ai_status": research_ai_status,

    "shutdown_ai": shutdown_ai,

    "edit_ai": edit_ai,
    "edit_ai_status": edit_ai_status,
    "apply_edit_files": apply_edit_files,
    "list_apply_backups": list_apply_backups,
    "restore_from_backup": restore_from_backup,
}
tools = get_enabled_tools()

def get_enabled_tools():
    config = load_config()

    enabled = []

    for name, tool_instance in AVAILABLE_TOOLS.items():
        if config.get("tools", {}).get(name, False):
            enabled.append(tool_instance)

    enabled.extend(gui_tools)

    return enabled

def get_interrupt_config():
    config = load_config()

    return {
        name: bool(enabled)
        for name, enabled in config.get(
            "interupt_tools",
            {}
        ).items()
    }

#gui variabler
def _set_voice_mode(state: bool):
    global VOICE_MODE

    VOICE_MODE = bool(state)

    set_config_value(
        "VOICE_MODE",
        VOICE_MODE,
    )

    _voice_mode_changed.set()

    _broadcast_voice_state(
        mode=VOICE_MODE,
        awake=False,
        listening=False,
        level=0.0,
    )

    return f"VOICE MODE is now {'on' if VOICE_MODE else 'off'}"

def _get_voice_mode():
    return VOICE_MODE

ToolRegistry.variable(
    "Voice Mode",
    "if voice is on or off.",
    readable=True,
    writable=True,
    getter=_get_voice_mode,
    setter=_set_voice_mode
)

def _set_talking(state: bool):
    global TALKING

    TALKING = bool(state)

    set_config_value(
        "TALKING",
        TALKING,
    )

    return f"TALKING is now {'on' if TALKING else 'off'}"

def _get_talking():
    return TALKING

ToolRegistry.variable(
    "Talking",
    "if Bob speaks its replies out loud (TTS) or not.",
    readable=True,
    writable=True,
    getter=_get_talking,
    setter=_set_talking
)

system_prompt = app_config.get(
    "system_prompt"
)

config = {"configurable": {"thread_id": "some_id"}}

def make_agent() -> Any:
    config = load_config()

    model = ChatOllama(
        model=config["model"],
        temperature=config.get("temperature", 0.7),
        reasoning=True,
        num_ctx=config.get("num_ctx", 8192),
        num_predict=config.get("num_predict", 8192),
    )

    enabled_tools = get_enabled_tools()

    agent = create_agent(
        model=model,
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on=get_interrupt_config()
            )
        ],
        system_prompt=config.get(
            "system_prompt",
            "You are a helpful assistant."
        ),
        tools=enabled_tools,
        checkpointer=memory_saver,
    )

    return agent

agent = make_agent()

def reload_agent():
    global agent

    agent = make_agent()

    return {
        "ok": True,
        "message": "Agent reloaded from config.json.",
    }

async def ask(msg: str, agent: Any, user_id: str = "user"):
    async for block in agent.astream(
        {
            "messages": [{"role": "user", "content": msg}],
            "plan": []
        },
        config=config,
        stream_mode=["updates", "messages"],
        version="v2"
    ):
        block_type = block.get("type")
        token_data = block.get("data")
        if isinstance(token_data, tuple):
            token = token_data[0]
        else:
            token = token_data
        if isinstance(token, AIMessageChunk):
            metrics.record_llm_usage("main", getattr(token, "usage_metadata", None))
            yield token, block
        elif block_type == "updates":
            if "__interrupt__" in block["data"]:
                yield token_data, block

async def main(msg, userid):
    async for token_data, block in ask(msg, agent, userid):
        if token_data:
            yield token_data, block

def _get_last_ai_reasoning(cfg) -> str:
    try:
        state = agent.get_state(cfg)
        messages = state.values.get("messages", [])
    except Exception:
        return ""
    for msg in reversed(messages):
        blocks = getattr(msg, "content_blocks", None)
        if not blocks:
            continue
        reasoning_parts = [b.get("reasoning", "") for b in blocks if b.get("type") == "reasoning"]
        if reasoning_parts:
            return "".join(reasoning_parts).strip()
    return ""

def _get_user_reply(voice_mode: bool):
    def _inner(ai_text: str) -> str:
        if voice_mode:
            print("\n(listening for your reply...)")
            reply = stt_main()
            print(f"You said: {reply}")
            return reply
        return input("\nYour reply: ")
    return _inner

def interupt_identifier(chunk, voice_mode: bool = None):
    if voice_mode is None:
        voice_mode = VOICE_MODE
    if "__interrupt__" in chunk["data"]:
        interrupt = chunk["data"]["__interrupt__"]
        if isinstance(interrupt, tuple):
            interrupt = interrupt[0]
        req = interrupt.value['action_requests'][0]
        tool_name = req['name']
        args = req.get("args") or req.get("arguments") or {}
        reasoning = _get_last_ai_reasoning(config)
        _broadcast_agent_stream("interrupt", f"{tool_name}({args})")
        decision = run_approval_conversation(tool_name, args, reasoning, _get_user_reply(voice_mode), TALKING)
        if decision["type"] == "reject":
            print(f"\033[31mRejected: {decision['message']}\033[0m")
            _broadcast_agent_stream("interrupt", f"Rejected: {decision['message']}")
        else:
            print("\033[32mApproved.\033[0m")
            _broadcast_agent_stream("interrupt", "Approved.")
        for token_data, block in resume_after_interrupt(agent, config, decision):
            response, node_type = get_last_text(token_data, block)
            if node_type == "interrupt":
                interupt_identifier(block, voice_mode)
            else:
                _emit(response, node_type)

def resume_after_interrupt(agent, config, decision):
    for block in agent.stream(
        Command(resume={"decisions": [decision]}),
        config=config,
        stream_mode=["updates", "messages"],
        version="v2"
    ):
        token_data = block.get("data")
        token = token_data[0] if isinstance(token_data, tuple) else token_data
        if isinstance(token, AIMessageChunk):
            metrics.record_llm_usage("main", getattr(token, "usage_metadata", None))
            yield token, block
        elif block["type"] == "updates":
            if "__interrupt__" in block["data"]:
                yield token_data, block
        else:
            continue

def _read_line_cancelable(prompt: str, stop_event: threading.Event, poll_interval: float = 0.25):
    _ensure_stdin_reader()
    print(prompt, end="", flush=True)
    while True:
        if stop_event.is_set():
            print()  # ny rad så prompten inte hänger kvar mitt i raden
            return None
        try:
            return _stdin_queue.get(timeout=poll_interval)
        except queue.Empty:
            continue


_stdin_queue: "queue.Queue[str]" = queue.Queue()
_stdin_reader_started = False
_stdin_reader_lock = threading.Lock()


def _stdin_reader_loop():
    while True:
        line = sys.stdin.readline()
        if line == "":
            return
        _stdin_queue.put(line.rstrip("\n"))


def _ensure_stdin_reader():
    global _stdin_reader_started
    with _stdin_reader_lock:
        if _stdin_reader_started:
            return
        _stdin_reader_started = True
    threading.Thread(target=_stdin_reader_loop, daemon=True).start()

async def input_loop(input_enabled):
    global VOICE_MODE
    while True:
        await input_enabled.wait()

        # Nollställ precis innan vi börjar vänta, så att den bara fångar
        # lägesbyten som händer UNDER den här väntan (inte gamla,
        # redan hanterade byten).
        _voice_mode_changed.clear()

        if VOICE_MODE:
            print("\nWaiting for wake word...")
            wake_detected = await asyncio.to_thread(
                wait_for_wake_word,
                stop_event=_voice_mode_changed,
            )
            if not wake_detected:
                continue
            print("Wake word detected.")
            _broadcast_voice_state(mode=True, awake=True, listening=False, level=0.0)
            try:
                user_input = await asyncio.to_thread(
                    stt_main,
                    level_callback=lambda lvl: _broadcast_voice_state(
                        mode=True, awake=True, listening=True, level=lvl
                    ),
                )
            except Exception as exc:
                print(f"\033[31mRöstinspelningen kraschade: {exc}\033[0m")
                _broadcast_voice_state(mode=True, awake=False, listening=False, level=0.0)
                continue
            print(f"User input: {user_input}")
            _broadcast_voice_state(mode=True, awake=False, listening=False, level=0.0)
            if not user_input:
                continue
        else:
            user_input = await asyncio.to_thread(
                _read_line_cancelable, "\nask me anything: ", _voice_mode_changed
            )
            if user_input is None:
                # Voice Mode aktiverades medan vi väntade på en textrad
                # (eller stdin stängdes/EOF) - loopa om direkt istället
                # för att sitta fast tills en textrad faktiskt skrivs in,
                # så bytet till röstläge slår igenom med en gång.
                continue
        input_enabled.clear()
        await event_queue.put({
            "type": "user_message",
            "content": user_input
        })

async def event_loop(input_enabled):
    while True:
        event = await event_queue.get()
        if event["type"] == "restart_agent":
            reload_agent()

            gui_server.manager.broadcast({
                "type": "agent_reloaded"
            })

            continue
        if event["type"] == "user_message":
            WORDS = []
            _broadcast_agent_stream("turn", "\u2022")
            async for token_data, block in main(event["content"], "user123"):
                response, node_type = get_last_text(token_data, block)
                if node_type == "interrupt":
                    interupt_identifier(block)
                else:
                    _emit(response, node_type)
                    if node_type == "text" and response:
                        WORDS.append(response)
            if TALKING:
                words_to_speak = " ".join(WORDS)
                if words_to_speak:
                    await asyncio.to_thread(speak, words_to_speak)
            input_enabled.set()
        elif event["type"] == "code_ai_finished":
            WORDS = []
            _broadcast_agent_stream("turn", "\u2022")
            async for token_data, block in main(
                f"THIS IS AN AUTOMATIC MESSAGE: async job with job id {event['job_id']} is finished. Result: {event['result']}",
                "user123"
            ):
                response, node_type = get_last_text(token_data, block)
                if node_type == "interrupt":
                    interupt_identifier(block)
                else:
                    _emit(response, node_type)
                    if node_type == "text" and response:
                        WORDS.append(response)
            if TALKING:
                words_to_speak = " ".join(WORDS)
                if words_to_speak:
                    await asyncio.to_thread(speak, words_to_speak)
            input_enabled.set()
        elif event["type"] == "research_ai_finished":
            WORDS = []
            _broadcast_agent_stream("turn", "\u2022")
            async for token_data, block in main(
                f"THIS IS AN AUTOMATIC MESSAGE: research job with id {event['job_id']} is finished. Result: {event['result']}",
                "user123"
            ):
                response, node_type = get_last_text(token_data, block)
                if node_type == "interrupt":
                    interupt_identifier(block)
                else:
                    _emit(response, node_type)
                    if node_type == "text" and response:
                        WORDS.append(response)
            if TALKING:
                words_to_speak = " ".join(WORDS)
                if words_to_speak:
                    await asyncio.to_thread(speak, words_to_speak)
            input_enabled.set()
        elif event["type"] == "edit_ai_finished":
            WORDS = []
            _broadcast_agent_stream("turn", "\u2022")
            async for token_data, block in main(
                f"THIS IS AN AUTOMATIC MESSAGE: edit job with id {event['job_id']} is finished. Result: {event['result']}",
                "user123"
            ):
                response, node_type = get_last_text(token_data, block)
                if node_type == "interrupt":
                    interupt_identifier(block)
                else:
                    _emit(response, node_type)
                    if node_type == "text" and response:
                        WORDS.append(response)
            if TALKING:
                words_to_speak = " ".join(WORDS)
                if words_to_speak:
                    await asyncio.to_thread(speak, words_to_speak)
            input_enabled.set()

async def app():
    global event_loop_instance

    event_loop_instance = asyncio.get_running_loop()

    # Låt GUI-servern (som körs i sin egen tråd/loop) veta vilken loop
    # den ska skicka trådsäkra chattmeddelanden till.
    gui_server.register_bridge_loop(event_loop_instance)

    metrics.start_ticker()

    input_enabled = asyncio.Event()
    input_enabled.set()

    await asyncio.gather(
        input_loop(input_enabled),
        event_loop(input_enabled),
    )



def run_bob():
    asyncio.run(app())

if __name__ == "__main__":
    bob_thread = threading.Thread(target=run_bob, daemon=True)
    bob_thread.start()
    launch_gui()