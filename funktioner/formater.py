full_response = ""

def formater(response, node_type):
    global full_response
    if response and node_type == "text":
        full_response = full_response + response
        print(response, end="", flush=True)
    elif response and node_type == "reasoning":
        print(f"\033[92m{response}\033[0m", end="", flush=True)
    elif response and node_type == "tool_call_chunk":
        print(f"\033[94m[TOOL CALL]: {response}\033[0m", end="", flush=True)
    elif response and node_type == "ToolMessage":
        print(f"\033[94m[TOOL MESSAGE]: {response}\033[0m", end="", flush=True)
    elif response and node_type == "interrupt":
        print(f"\n\n\033[33m[INTERRUPT]: {response}\033[0m", end="", flush=True)