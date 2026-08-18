import chromadb
from chromadb.utils import embedding_functions
from datetime import datetime
from funktioner.gpu_lock import gpu_locked_embeddings

class OllamaEmbedder(embedding_functions.EmbeddingFunction):
    def __call__(self, input):
        return [gpu_locked_embeddings(prompt=text) for text in input]

class LongTermMemory:
    def __init__(self, path="./bob_memory_db"):
        self.client = chromadb.PersistentClient(path=path)
        self.collection = self.client.get_or_create_collection(
            name="bob_ltm",
            embedding_function=OllamaEmbedder()
        )

    def store(self, text: str, source: str, tags: list[str] = None, importance: float = 0.5):
        doc_id = f"{source}_{datetime.now().timestamp()}"
        self.collection.add(
            documents=[text],
            metadatas=[{"source": source, "timestamp": datetime.now().isoformat(),
                        "tags": ",".join(tags or []), "importance": importance}],
            ids=[doc_id]
        )
        return doc_id

    def query(self, text: str, n_results: int = 5):
        return self.collection.query(query_texts=[text], n_results=n_results)

ltm = LongTermMemory()  # singleton, importeras överallt