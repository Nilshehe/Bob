from langchain_ollama import ChatOllama
from langchain.agents.middleware import HumanInTheLoopMiddleware, AgentMiddleware
from langchain.agents import create_agent
from langchain.messages import AIMessageChunk, ToolMessage
from funktioner.response_cleaner import get_last_text
from langgraph.types import Command
from typing import Any
import os
from voice.live_stt import stt_main
from voice.wake_word import wait_for_wake_word
from tools.code_ai import register_notify_callback
from tools.research_ai import register_notify_callback as register_research_notify_callback
from tools.edit_ai import register_notify_callback as register_edit_notify_callback
from tools.approval_agent import run_approval_conversation, arun_approval_conversation
from voice.tts import speak
import asyncio
import threading
from funktioner.queue import event_queue
from funktioner import metrics
from funktioner.io_utils import broadcast_voice_state as _broadcast_voice_state, broadcast_agent_stream as _broadcast_agent_stream, emit as _emit, read_line_cancelable as _read_line_cancelable
from gui.backend.registry import ToolRegistry
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


# _broadcast_voice_state, _broadcast_agent_stream och _emit flyttade till
# funktioner/io_utils.py (importerade ovan) - main.py håller bara
# orkestreringen (event-loop, agent-anrop, avbrott).
register_state_callback(_broadcast_voice_state)

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
    provider = config.get("provider", "ollama")

    import config_manager

    if provider == "ollama":
        model = config_manager.make_chat_model(
            provider,
            config["model"],
            temperature=config.get("temperature", 0.7),
            reasoning=True,
            num_ctx=config.get("num_ctx", 8192),
            num_predict=config.get("num_predict", 8192),
        )
    else:
        # API-provider (openai/anthropic/... - se config_manager.API_PROVIDERS).
        # Kräver att motsvarande langchain-integrationspaket är
        # installerat (t.ex. langchain-openai). Nyckeln hämtas från den
        # .env-variabel som är inställd för providern i settings-
        # widgeten (config_manager.get_api_key_env_name), inte från
        # providerns "vanliga" env-variabelnamn - så du kan döpa .env-
        # variabeln fritt.
        model = config_manager.make_chat_model(
            provider,
            config["model"],
            temperature=config.get("temperature", 0.7),
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

    # Approval/Edit/Research/Code AI bygger sin egen LLM+agent en gång
    # vid import (så de kan köra en annan provider/modell än
    # huvud-AI:n - se config_manager.get_agent_settings). "Apply &
    # Restart" i settings-widgeten ska räkna om alla, inte bara
    # huvud-AI:n, annars kräver ett providerbyte för en underagent en
    # full processomstart.
    reloaded = ["main"]
    for modname, reload_fn in (
        ("tools.approval_agent", "reload_approval_agent"),
        ("tools.edit_ai", "reload_edit_agent"),
        ("tools.research_ai", "reload_research_agent"),
        ("tools.code_ai", "reload_code_agent"),
    ):
        try:
            import importlib

            mod = importlib.import_module(modname)
            getattr(mod, reload_fn)()
            reloaded.append(modname.rsplit(".", 1)[-1])
        except Exception as exc:
            print(f"\033[33mKunde inte ladda om {modname}: {exc}\033[0m")

    return {
        "ok": True,
        "message": f"Reloaded from config.json: {', '.join(reloaded)}.",
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
    try:
        async for token_data, block in ask(msg, agent, userid):
            if token_data:
                yield token_data, block
    except Exception as exc:
        print(f"\033[31mLLM-anropet misslyckades: {exc}\033[0m")
        _emit("Kunde inte kontakta Ollama just nu.", "text")

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
    """Returnerar en async funktion som hämtar användarens svar under
    en Approval AI-konversation.

    Röstläge: spelar in från mikrofonen i en bakgrundstråd (blockerar
    inte event-loopen).

    Textläge (terminal ELLER GUI): väntar på nästa "user_message"-
    händelse på event_queue och tar bort den från kön när den hittas -
    annars skulle den råka processas igen som ett nytt vanligt
    meddelande efteråt. Tidigare användes en blockerande input()-
    inläsning här, vilket frös HELA asyncio-loopen (inklusive
    websocket-bryggan) medan Approval AI väntade på svar - ett svar
    skrivet i GUI:t skickas via run_coroutine_threadsafe till just den
    loopen, så det kunde aldrig komma fram förrän input() returnerade.
    Den bugg är fixad genom att aldrig blockera loopen: vi `await`:ar
    event_queue istället för att läsa stdin direkt, och läser samtidigt
    in en terminalrad (om VOICE_MODE inte är på) som en likvärdig källa
    - vilket som kommer in först vinner.

    Andra händelsetyper som råkar komma in medan vi väntar (t.ex. att
    en bakgrundsjobb blir klar) läggs tillbaka på kön oförändrade så de
    inte går förlorade.
    """
    async def _inner(ai_text: str, input_enabled: asyncio.Event = None) -> str:
        if voice_mode:
            print("\n(listening for your reply...)")
            reply = await asyncio.to_thread(stt_main)
            print(f"You said: {reply}")
            return reply

        print("\nYour reply (terminal or GUI): ", end="", flush=True)

        # Låt terminalens input_loop få skriva in svaret i kön också -
        # den pausar annars sig själv tills input_enabled sätts igen.
        # GUI-svar kräver ingen sådan flagga, de postar till kön direkt
        # när användaren skickar dem oavsett input_enabled.
        if input_enabled is not None:
            input_enabled.set()

        held_back = []
        try:
            while True:
                event = await event_queue.get()

                if event.get("type") == "user_message":
                    return event.get("content", "")

                # Inte ett svar från användaren (t.ex. en bakgrundsjobb-
                # notis) - lägg tillbaka den så event_loop fortfarande
                # processar den som vanligt när vi är klara.
                held_back.append(event)
        finally:
            if input_enabled is not None:
                input_enabled.clear()
            for ev in held_back:
                await event_queue.put(ev)

    return _inner

async def interupt_identifier(chunk, voice_mode: bool = None, input_enabled: asyncio.Event = None):
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

        get_reply = _get_user_reply(voice_mode)

        async def get_reply_bound(ai_text):
            return await get_reply(ai_text, input_enabled)

        decision = await arun_approval_conversation(
            tool_name, args, reasoning, get_reply_bound, TALKING
        )
        if decision["type"] == "reject":
            print(f"\033[31mRejected: {decision['message']}\033[0m")
            _broadcast_agent_stream("interrupt", f"Rejected: {decision['message']}")
        else:
            print("\033[32mApproved.\033[0m")
            _broadcast_agent_stream("interrupt", "Approved.")
        async for token_data, block in resume_after_interrupt(agent, config, decision):
            response, node_type = get_last_text(token_data, block)
            if node_type == "interrupt":
                await interupt_identifier(block, voice_mode, input_enabled)
            else:
                _emit(response, node_type)

async def resume_after_interrupt(agent, config, decision):
    async for block in agent.astream(
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

# _read_line_cancelable (och stdin-läsningen den bygger på) flyttad till
# funktioner/io_utils.py (importerad ovan).

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
                    await interupt_identifier(block, input_enabled=input_enabled)
                else:
                    _emit(response, node_type)
                    if node_type == "text" and response:
                        WORDS.append(response)
            if TALKING:
                words_to_speak = " ".join(WORDS)
                if words_to_speak:
                    await speak(words_to_speak)
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
                    await interupt_identifier(block, input_enabled=input_enabled)
                else:
                    _emit(response, node_type)
                    if node_type == "text" and response:
                        WORDS.append(response)
            if TALKING:
                words_to_speak = " ".join(WORDS)
                if words_to_speak:
                    await speak(words_to_speak)
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
                    await interupt_identifier(block, input_enabled=input_enabled)
                else:
                    _emit(response, node_type)
                    if node_type == "text" and response:
                        WORDS.append(response)
            if TALKING:
                words_to_speak = " ".join(WORDS)
                if words_to_speak:
                    await speak(words_to_speak)
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
                    await interupt_identifier(block, input_enabled=input_enabled)
                else:
                    _emit(response, node_type)
                    if node_type == "text" and response:
                        WORDS.append(response)
            if TALKING:
                words_to_speak = " ".join(WORDS)
                if words_to_speak:
                    await speak(words_to_speak)
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
    from gui.backend.main_gui import launch_gui

    launch_gui()
    bob_thread.join()