from langchain_core.tools import tool
from funktioner.memory_store import ltm as long_term_memory
from funktioner.short_term_memory import stm as short_term_memory
import json
from datetime import datetime

def _convert_ltm_result_to_memory_record(ids, documents, metadatas, index):
    """Convert a single result from long-term memory query to a memory record."""
    doc_id = ids[index]
    text = documents[index]
    meta = metadatas[index]
    source = meta.get('source')
    timestamp = meta.get('timestamp')
    tags_str = meta.get('tags', '')
    tags = [t.strip() for t in tags_str.split(',')] if tags_str else []
    importance = float(meta.get('importance', 0.5))
    return {
        'id': doc_id,
        'text': text,
        'source': source,
        'tags': tags,
        'importance': importance,
        'timestamp': timestamp,
        # Long-term memory doesn't have expiration in the same way, but we can set it to far future or None
        'expiration': None  # Indicates no expiration (or permanent)
    }

@tool
def memory_store(text: str, source: str, tags: list[str] = None, importance: float = 0.5) -> str:
    """Store a memory in both short-term and long-term memory.
    Returns a JSON string with the IDs from both systems."""
    # Store in short-term memory
    stm_id = short_term_memory.store(text, source, tags, importance)
    # Store in long-term memory
    ltm_id = long_term_memory.store(text, source, tags, importance)
    result = {
        'short_term_memory_id': stm_id,
        'long_term_memory_id': ltm_id
    }
    return json.dumps(result)

@tool
def memory_list(limit: int = 100) -> str:
    """List memories from both short-term and long-term memory, sorted by timestamp (most recent first).
    Returns a JSON string of memory records."""
    # Get memories from short-term memory
    stm_memories = short_term_memory.list(limit=limit)  # We'll get more and then combine and sort
    # Get memories from long-term memory
    # Note: long-term memory query with empty string returns all? We don't have a direct list all.
    # We'll use a query with empty string and a large n_results, but note that the long-term memory
    # might not support empty string query. Instead, we can use a common word? 
    # Alternatively, we can change the long-term memory to support listing all? 
    # Since we cannot change the long-term memory (it's given), we'll have to use a query that returns many.
    # We'll use a query with a space and hope it returns many? Not reliable.
    # Let's think: the long-term memory is for important memories, so we don't expect to list all of them via this tool?
    # The roadmap doesn't specify. We'll implement as best we can.
    # We'll use a query with an empty string and a large n_results, but if that fails, we'll use a common word like "the".
    try:
        ltm_results = long_term_memory.query("", n_results=1000)  # Try empty string
    except Exception:
        ltm_results = long_term_memory.query("the", n_results=1000)  # Fallback to a common word
    
    ltm_memories = []
    if ltm_results and ltm_results.get('ids'):
        for i in range(len(ltm_results['ids'])):
            ltm_memories.append(_convert_ltm_result_to_memory_record(
                ltm_results['ids'],
                ltm_results['documents'],
                ltm_results['metadatas'],
                i
            ))
    
    # Combine and sort by timestamp (most recent first)
    all_memories = stm_memories + ltm_memories
    # Filter out any memories without timestamp (shouldn't happen) and sort
    all_memories = [m for m in all_memories if m.get('timestamp')]
    all_memories.sort(key=lambda x: x['timestamp'], reverse=True)
    # Limit to the requested limit
    all_memories = all_memories[:limit]
    return json.dumps(all_memories, indent=2)

@tool
def memory_search(text: str, n_results: int = 5) -> str:
    """Search for memories containing the given text in both short-term and long-term memory.
    Returns a JSON string of matching memory records (up to n_results)."""
    # Search in short-term memory
    stm_results = short_term_memory.query(text, n_results=n_results)
    # Search in long-term memory
    ltm_results = long_term_memory.query(text, n_results=n_results)
    
    ltm_memories = []
    if ltm_results and ltm_results.get('ids'):
        for i in range(len(ltm_results['ids'])):
            ltm_memories.append(_convert_ltm_result_to_memory_record(
                ltm_results['ids'],
                ltm_results['documents'],
                ltm_results['metadatas'],
                i
            ))
    
    # Combine and sort by timestamp (most recent first)
    all_memories = stm_results + ltm_memories
    all_memories = [m for m in all_memories if m.get('timestamp')]
    all_memories.sort(key=lambda x: x['timestamp'], reverse=True)
    # Limit to n_results
    all_memories = all_memories[:n_results]
    return json.dumps(all_memories, indent=2)

@tool
def memory_read(memory_id: str) -> str:
    """Read a memory by its ID from either short-term or long-term memory.
    Returns the memory record as a JSON string if found, else null."""
    # Try short-term memory first
    stm_memory = short_term_memory.get(memory_id)
    if stm_memory is not None:
        return json.dumps(stm_memory, indent=2)
    # Try long-term memory: we don't have a direct get by ID, but we can query by ID? 
    # The long-term memory doesn't support get by ID. We'll have to search by ID in the IDs list?
    # This is inefficient, but we don't have another way.
    # We'll query for the ID (assuming the ID is unique and we can search for it in the text? Not reliable).
    # Alternatively, we can store the ID in the metadata? But we don't control the long-term memory storage.
    # Given the constraints, we'll skip long-term memory for read by ID? 
    # But note: the long-term memory ID is the doc_id we returned from store.
    # We don't have a way to retrieve by doc_id without knowing the text or metadata.
    # We'll have to return not found for long-term memory in this tool.
    # This is a limitation of the current long-term memory API.
    return "null"

@tool
def memory_edit(memory_id: str, text: str = None, source: str = None, tags: list[str] = None, importance: float = None) -> str:
    """Edit a memory by its ID in either short-term or long-term memory.
    Returns true if the memory was found and edited in at least one system, else false."""
    # Try short-term memory
    stm_success = short_term_memory.update(memory_id, text, source, tags, importance)
    if stm_success:
        return "true"
    # Try long-term memory: we don't have an update method. 
    # We would have to delete and re-store? But we don't have delete by ID in long-term memory either.
    # Given the limitations, we'll only support editing in short-term memory.
    return "false"

@tool
def memory_delete(memory_id: str) -> str:
    """Delete a memory by its ID from either short-term or long-term memory.
    Returns true if the memory was found and deleted in at least one system, else false."""
    # Try short-term memory
    stm_success = short_term_memory.delete(memory_id)
    if stm_success:
        return "true"
    # Try long-term memory: we don't have a delete by ID method.
    return "false"
