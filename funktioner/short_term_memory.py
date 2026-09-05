import json
import os
from datetime import datetime, timedelta

class ShortTermMemory:
    def __init__(self, storage_file='./short_term_memory.json'):
        self.storage_file = storage_file
        self.memories = self._load()

    def _load(self):
        if not os.path.exists(self.storage_file):
            return []
        try:
            with open(self.storage_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Filter out expired memories on load
                now = datetime.now()
                valid_memories = []
                for mem in data:
                    exp_str = mem.get('expiration')
                    if exp_str:
                        exp = datetime.fromisoformat(exp_str)
                        if exp > now:
                            valid_memories.append(mem)
                return valid_memories
        except Exception:
            return []

    def _save(self):
        with open(self.storage_file, 'w', encoding='utf-8') as f:
            json.dump(self.memories, f, indent=2, ensure_ascii=False)

    def _remove_expired(self):
        now = datetime.now()
        self.memories = [mem for mem in self.memories if datetime.fromisoformat(mem['expiration']) > now]
        self._save()

    def store(self, text, source, tags=None, importance=0.5):
        """Store a memory and return its ID."""
        now = datetime.now()
        expiration = now + timedelta(days=30)
        memory_id = f"{source}_{now.timestamp()}"
        memory = {
            'id': memory_id,
            'text': text,
            'source': source,
            'tags': tags or [],
            'importance': importance,
            'timestamp': now.isoformat(),
            'expiration': expiration.isoformat()
        }
        self.memories.append(memory)
        self._save()
        return memory_id

    def query(self, text, n_results=5):
        """Query memories that contain the given text (simple substring match)."""
        self._remove_expired()
        results = []
        for mem in self.memories:
            if text.lower() in mem['text'].lower():
                results.append(mem)
                if len(results) >= n_results:
                    break
        return results

    def get(self, memory_id):
        """Get a memory by its ID."""
        self._remove_expired()
        for mem in self.memories:
            if mem['id'] == memory_id:
                return mem
        return None

    def list(self, limit=100):
        """List memories (most recent first)."""
        self._remove_expired()
        # Sort by timestamp descending
        sorted_memories = sorted(self.memories, key=lambda x: x['timestamp'], reverse=True)
        return sorted_memories[:limit]

    def update(self, memory_id, text=None, source=None, tags=None, importance=None):
        """Update a memory. Returns True if found and updated, False otherwise."""
        self._remove_expired()
        for mem in self.memories:
            if mem['id'] == memory_id:
                if text is not None:
                    mem['text'] = text
                if source is not None:
                    mem['source'] = source
                if tags is not None:
                    mem['tags'] = tags
                if importance is not None:
                    mem['importance'] = importance
                self._save()
                return True
        return False

    def delete(self, memory_id):
        """Delete a memory by its ID. Returns True if found and deleted, False otherwise."""
        self._remove_expired()
        for i, mem in enumerate(self.memories):
            if mem['id'] == memory_id:
                del self.memories[i]
                self._save()
                return True
        return False

# Singleton instance
stm = ShortTermMemory()
