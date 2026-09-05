from langchain_core.tools import tool
from main import event_loop_instance, event_queue
from funktioner.memory import memory_saver
import asyncio

@tool
def restart_agent_tool(_: str = '') -> str:
    """Restart the agent to clear the conversation buffer."""
    # Avoid circular import by importing inside the function
    if event_loop_instance is None:
        return 'Error: event loop not initialized'
    # Clear the memory buffer
    memory_saver.storage.clear()
    # Put restart event on the queue using thread-safe coroutine runner
    async def put_restart_event():
        await event_queue.put({'type': 'restart_agent'})
    
    asyncio.run_coroutine_threadsafe(
        put_restart_event(),
        event_loop_instance
    )
    return 'Agent restart initiated and memory cleared'
