import sys
import time

import logging

logging.basicConfig(level=logging.WARN)

    
def get_last_text(token, chunk = None):

    
    if chunk["type"] == "updates":
        if "__interrupt__" in chunk["data"]:
            return None, "interrupt"
        

    if not hasattr(token, "content_blocks"):
        return None, None
    
    reasoning = [b for b in token.content_blocks if b["type"] == "reasoning"]
    text = [b for b in token.content_blocks if b["type"] == "text"]
    tool_calls = [b for b in token.content_blocks if b["type"] == "tool_call_chunk"]
            

    if reasoning:
        return "".join(r["reasoning"] for r in reasoning), "reasoning"
    if text:
        return "".join(t["text"] for t in text), "text"
    if tool_calls:
        return "".join(f"{tc['name']}({tc['args']})" for tc in tool_calls), "tool_call_chunk"
    return None, None

