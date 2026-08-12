from langchain_ollama import ChatOllama
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents import create_agent
from langchain.messages import AIMessageChunk, ToolMessage
from funktioner.response_cleaner import get_last_text
from funktioner.formater import formater
from langgraph.types import Command
from typing import Any
from voice.live_stt import stt_main
from voice.wake_word import wait_for_wake_word
import queue
from tools.shared_resources import GPU_LOCK
from tools.code_ai import register_notify_callback
from tools.approval_agent import run_approval_conversation

# sätts av chatloop() beroende på om voice mode eller text mode valdes
VOICE_MODE = False


#job done notifications
_notifications: "queue.Queue[tuple[str, str]]" = queue.Queue()
def _on_job_done(job_id: str, result: str) -> None:
    _notifications.put((job_id, result))

register_notify_callback(_on_job_done)

def process_pending_notifications():
    """Anropa i toppen av while-loopen i chatloop(), innan input()."""
    while not _notifications.empty():
        job_id, result = _notifications.get_nowait()
        for token_data, block in main(f"THIS IS A AUTIOMATIC MESSAGE: async job with job id {job_id} is finished", "user123"):
            response, node_type = get_last_text(token_data, block)
            if node_type == "interrupt":
                interupt_identifier(block)
            else:
                formater(response, node_type)


#memory
from langgraph.checkpoint.memory import InMemorySaver
memory_saver = InMemorySaver()

#llm
llm = ChatOllama(model = "Qwen3:4b", reasoning = True)

#tools
from tools.ddgs_tool import web_search
from tools.sok_visible import search_visible_webpage, download_file, move_file, get_clickable_elements, click_on_page, type_into_page, scroll_page, click_and_download, get_page_text, open_browser
from tools.code_ai import code_ai, code_ai_status
from tools.research_ai import research_ai, research_ai_status
from tools.quit import shutdown_ai
tools = [web_search,
        search_visible_webpage, 
        download_file, 
        move_file, 
        get_clickable_elements,
        click_on_page, 
        type_into_page, 
        scroll_page, 
        click_and_download, 
        get_page_text,
        open_browser,
        code_ai,
        code_ai_status,
        research_ai,
        research_ai_status,
        shutdown_ai
]


system_prompt = """You are a helpful assistant. Allways check if there are anny awailable skills that can help you with the task. If there are, use them. If not, try to solve the task yourself.
"""

#config
config = {"configurable": {"thread_id": "some_id"}}

def make_agent() -> Any:
    agent = create_agent(
        model=llm,
        middleware=[
            HumanInTheLoopMiddleware(interrupt_on={
                "web_search": False,
                "download_file": True,
                "move_file": True,
                "click_and_download": True,
                "code_ai": True,
                "create_skill": True

            })
        ],
        system_prompt=system_prompt,
        tools = tools,
        checkpointer = memory_saver
    )
    return agent

agent = make_agent()
    
def ask(msg: str, agent: Any, user_id: str = "user") -> Any:
    with GPU_LOCK:
        for block in agent.stream(
            {"messages": [{"role": "user", "content": msg}]},
            config=config,
            stream_mode=["updates", "messages"],
            version="v2"
        ):
            block_type = block.get("type")
            token_data = block.get("data")
            token = None

            if isinstance(token_data, tuple):
                token_data = token_data[0]
                token = token_data
            else:
                token = token_data

            if isinstance(token, AIMessageChunk):
                yield token, block
            elif block_type == "updates":  
                if "__interrupt__" in block["data"]:
                    yield token_data, block
            else:
                continue

def main(msg, userid):
    for token_data, block in ask(msg, agent, userid):
        if token_data:
            yield token_data, block
        else:
            continue



#hämta resonemanget (reasoning-texten) som agenten redan producerat innan den bestämde
#sig för att göra tool-anropet som nu ligger på interrupt. Detta är samma "tanke" som
#ledde fram till beslutet, hämtat direkt ur checkpointern - inget nytt gissas fram.
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


#identifiera interupts
def interupt_identifier(chunk, voice_mode: bool = None):
    if voice_mode is None:
        voice_mode = VOICE_MODE

    if "__interrupt__" in chunk["data"]:
        interrupt = chunk['data']['__interrupt__']
        if isinstance(interrupt, tuple):
            interrupt = interrupt[0]
        req = interrupt.value['action_requests'][0]
        tool_name = req['name']
        args = req.get("args") or req.get("arguments") or {}
        reasoning = _get_last_ai_reasoning(config)

        decision = run_approval_conversation(tool_name, args, reasoning, _get_user_reply(voice_mode))

        if decision["type"] == "reject":
            print(f"\033[31mRejected: {decision['message']}\033[0m")
        else:
            print("\033[32mApproved.\033[0m")

        for token_data, block in resume_after_interrupt(agent, config, decision):
            response, node_type = get_last_text(token_data, block)
            if node_type == "interrupt":
                interupt_identifier(block, voice_mode)
            else:
                formater(response, node_type)
            
#resume afeter interupt
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
            yield token, block
        elif block["type"] == "updates":
            if "__interrupt__" in block["data"]:
                yield token_data, block
        else:
            continue


def chatloop():
    global VOICE_MODE
    VOICE_MODE = input("voice mode? (y/n): ").strip().lower() == "y"
    if VOICE_MODE:
        while True:
            process_pending_notifications()
            print("\nWaiting for wake word...")
            wait_for_wake_word()
            print("Wake word detected. Listening for command...")
            user_input = stt_main()
            print(f"User input: {user_input}") 
            for token_data, block in main(user_input, "user123"):
                response, node_type = get_last_text(token_data, block)
                if node_type == "interrupt":
                    interupt_identifier(block)
                else:
                    formater(response, node_type)
    else:
        while True:
            process_pending_notifications()
            user_input = input("\nask me anything: ")
            for token_data, block in main(user_input, "user123"):
                response, node_type = get_last_text(token_data, block)
                if node_type == "interrupt":
                    interupt_identifier(block)
                else:
                    formater(response, node_type)

if __name__ == "__main__":
    chatloop()