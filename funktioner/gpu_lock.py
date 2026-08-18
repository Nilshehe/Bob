import threading
import ollama
from langchain_ollama import ChatOllama

GPU_SEMAPHORE = threading.Semaphore(2)  # matchar OLLAMA_NUM_PARALLEL=2


def make_gpu_locked_chat_ollama(model: str = "qwen3:4b", **kwargs) -> ChatOllama:
    llm = ChatOllama(model=model, **kwargs)
    original_invoke = llm.invoke
    original_ainvoke = llm.ainvoke

    def locked_invoke(*args, **kw):
        with GPU_SEMAPHORE:
            return original_invoke(*args, **kw)

    async def locked_ainvoke(*args, **kw):
        with GPU_SEMAPHORE:
            return await original_ainvoke(*args, **kw)

    llm.invoke = locked_invoke
    llm.ainvoke = locked_ainvoke
    return llm


def gpu_locked_embeddings(model: str = "nomic-embed-text", prompt: str = "") -> list[float]:
    with GPU_SEMAPHORE:
        return ollama.embeddings(model=model, prompt=prompt)["embedding"]