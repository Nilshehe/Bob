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
        research_ai_status
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



#identifiera interupts
def interupt_identifier(chunk):
    if "__interrupt__" in chunk["data"]:
        interrupt = chunk['data']['__interrupt__']
        if isinstance(interrupt, tuple):
            interrupt = interrupt[0]
        req = interrupt.value['action_requests'][0]
        tool_name = req['name']
        args = req.get("args") or req.get("arguments") or {}

        print(f"\033[33mInterrupt received: \"{tool_name}\" with args: \"{args}\"\033[0m")
        resp = input("Enter your decision (approve (leave blank)/reject (r)/edit(e(not avaible))): ").strip().lower()

        if not resp:
            decision = {"type": "approve"}
        elif resp == "r":
            rejectmsg = input("Enter a reject message: ")
            decision = {"type": "reject", "message": rejectmsg}
        else:
            print("sorry, edit functionality not implemented yet")
            decision = {"type": "reject", "message": "edit not implemented"}

        for token_data, block in resume_after_interrupt(agent, config, decision):
            response, node_type = get_last_text(token_data, block)
            if node_type == "interrupt":
                interupt_identifier(block)
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
    if input("voice mode? (y/n): ").strip().lower() == "y":
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

