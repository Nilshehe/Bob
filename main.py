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
from funktioner.queue import event_queue

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


# sätts av chatloop() beroende på om voice mode eller text mode valdes
VOICE_MODE = False
TALKING = False




#memory
from langgraph.checkpoint.memory import InMemorySaver
memory_saver = InMemorySaver()

#llm
llm = ChatOllama(model = "Qwen3:4b", reasoning = True, num_ctx=8192, num_predict=8192)

#tools
from tools.ddgs_tool import web_search
from tools.sok_visible import search_visible_webpage, download_file, move_file, get_clickable_elements, click_on_page, type_into_page, scroll_page, click_and_download, get_page_text, open_browser
from tools.code_ai import code_ai, code_ai_status
from tools.research_ai import research_ai, research_ai_status
from tools.quit import shutdown_ai
from tools.model3d_tools import get_tools as get_model3d_tools
from tools.model3d_complex_shapes import get_complex_tools as get_model3d_complex_tools
from tools.edit_ai import edit_ai, edit_ai_status, apply_edit_files
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
        shutdown_ai,
        edit_ai,
        edit_ai_status,
        apply_edit_files
#        *get_model3d_tools(),
#        *get_model3d_complex_tools()
]


system_prompt = """You are BOB a helpful assistant.

Always check if there are any available skills that can help you with the task.
If there are, use them. If not, try to solve the task yourself.

Always answer in Swedish.

You may receive relevant long-term memories about the user.
Use them when they are relevant to the current request.
Do not mention the memory system unless the user asks about it.
Do not assume a memory is correct if the current user message contradicts it.
Allways check memory for relevant information before using tools or answering questions.
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
                "research_ai": True,
                "download_material": True,
                "download_reference_shape": True,
                "apply_edit_files": True,


            })
        ],
        system_prompt=system_prompt,
        tools = tools,
        checkpointer = memory_saver
    )
    return agent

agent = make_agent()
    

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
            yield token, block

        elif block_type == "updates":
            if "__interrupt__" in block["data"]:
                yield token_data, block

async def main(msg, userid):
    async for token_data, block in ask(msg, agent, userid):
        if token_data:
            yield token_data, block



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

        decision = run_approval_conversation(tool_name, args, reasoning, _get_user_reply(voice_mode), TALKING)

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



async def input_loop():
    global VOICE_MODE

    while True:
        await INPUT_ENABLED.wait()

        if VOICE_MODE:
            print("\nWaiting for wake word...")
            await asyncio.to_thread(wait_for_wake_word)

            print("Wake word detected.")
            user_input = await asyncio.to_thread(stt_main)
            print(f"User input: {user_input}")

        else:
            user_input = await asyncio.to_thread(
                input,
                "\nask me anything: "
            )

        INPUT_ENABLED.clear()

        await event_queue.put({
            "type": "user_message",
            "content": user_input
        })
                

async def event_loop():
    while True:
        event = await event_queue.get()

        if event["type"] == "user_message":
            WORDS = []

            async for token_data, block in main(
                event["content"],
                "user123"
            ):
                response, node_type = get_last_text(
                    token_data,
                    block
                )

                if node_type == "interrupt":
                    interupt_identifier(block)
                else:
                    formater(response, node_type)

                    if node_type == "text" and response:
                        WORDS.append(response)

            if TALKING:
                words_to_speak = " ".join(WORDS)

                if words_to_speak:
                    await asyncio.to_thread(
                        speak,
                        words_to_speak
                    )
            INPUT_ENABLED.set()

        elif event["type"] == "code_ai_finished":
            WORDS = []

            async for token_data, block in main(
                f"THIS IS AN AUTOMATIC MESSAGE: "
                f"async job with job id {event['job_id']} is finished. "
                f"Result: {event['result']}",
                "user123"
            ):
                response, node_type = get_last_text(
                    token_data,
                    block
                )

                if node_type == "interrupt":
                    interupt_identifier(block)
                else:
                    formater(response, node_type)

                    if node_type == "text" and response:
                        WORDS.append(response)

            if TALKING:
                words_to_speak = " ".join(WORDS)

                if words_to_speak:
                    await asyncio.to_thread(
                        speak,
                        words_to_speak
                    )
            INPUT_ENABLED.set()

        elif event["type"] == "research_ai_finished":
            WORDS = []

            async for token_data, block in main(
                f"THIS IS AN AUTOMATIC MESSAGE: "
                f"research job with id {event['job_id']} is finished. "
                f"Result: {event['result']}",
                "user123"
            ):
                response, node_type = get_last_text(
                    token_data,
                    block
                )

                if node_type == "interrupt":
                    interupt_identifier(block)
                else:
                    formater(response, node_type)

                    if node_type == "text" and response:
                        WORDS.append(response)

            if TALKING:
                words_to_speak = " ".join(WORDS)

                if words_to_speak:
                    await asyncio.to_thread(
                        speak,
                        words_to_speak
                    )
        elif event["type"] == "edit_ai_finished":
            WORDS = []

            async for token_data, block in main(
                f"THIS IS AN AUTOMATIC MESSAGE: "
                f"edit job with id {event['job_id']} is finished. "
                f"Result: {event['result']}",
                "user123"
            ):
                response, node_type = get_last_text(
                    token_data,
                    block
                )

                if node_type == "interrupt":
                    interupt_identifier(block)
                else:
                    formater(response, node_type)

                    if node_type == "text" and response:
                        WORDS.append(response)

            if TALKING:
                words_to_speak = " ".join(WORDS)

                if words_to_speak:
                    await asyncio.to_thread(
                        speak,
                        words_to_speak
                    )

            INPUT_ENABLED.set()

async def app():
    global event_loop_instance

    event_loop_instance = asyncio.get_running_loop()

    INPUT_ENABLED.set()

    await asyncio.gather(
        input_loop(),
        event_loop(),
    )


if __name__ == "__main__":
    VOICE_MODE = input("voice mode? (y/n): ").strip().lower() == "y"
    TALKING = input("talking mode? (y/n): ").strip().lower() == "y"
    INPUT_ENABLED = asyncio.Event()

    asyncio.run(app())