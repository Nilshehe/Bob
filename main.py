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

#memory
from langgraph.checkpoint.memory import InMemorySaver
memory_saver = InMemorySaver()

#llm
llm = ChatOllama(model = "Qwen3:4b", reasoning = True)

#tools
from tools.ddgs_tool import web_search
from tools.sok_visible import search_visible_webpage, download_file, move_file, get_clickable_elements, click_on_page, type_into_page, scroll_page, click_and_download, get_page_text
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
]


system_prompt = """You are a helpful assistant"""

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
                "click_and_download": True

            })
        ],
        system_prompt=system_prompt,
        tools = tools,
        checkpointer = memory_saver
    )
    return agent

agent = make_agent()
    
def ask(msg: str, agent: Any, user_id: str = "user") -> Any:
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
            print("Waiting for wake word...")
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
            user_input = input("\nask me anything: ")
            for token_data, block in main(user_input, "user123"):
                response, node_type = get_last_text(token_data, block)
                if node_type == "interrupt":
                    interupt_identifier(block)
                else:
                    formater(response, node_type)

if __name__ == "__main__":
    chatloop()

