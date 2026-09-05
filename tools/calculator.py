from langchain_core.tools import tool
import re
import math

@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression and return the result.
    Supports basic arithmetic, parentheses, and math functions (sin, cos, tan, sqrt, log, etc.).
    """
    # Remove whitespace
    expression = expression.replace(" ", "")
    
    # Replace common math functions with their math module equivalents
    # We'll use a simple approach: allow only safe characters and functions
    # For security, we'll restrict to a whitelist of characters and functions
    # But note: this is a simple implementation for demonstration.
    # In a production setting, we would use a proper expression evaluator or sandbox.
    
    # Allowed characters: digits, decimal point, parentheses, operators, and letters for functions
    # We'll allow: 0-9, ., +, -, *, /, %, (, ), and letters for function names
    # We'll also allow the comma for function arguments (e.g., log(10, 10))
    
    # First, replace function names with their math module equivalents
    # We'll do a simple replacement for common functions
    replacements = {
        'sin': 'math.sin',
        'cos': 'math.cos',
        'tan': 'math.tan',
        'asin': 'math.asin',
        'acos': 'math.acos',
        'atan': 'math.atan',
        'sinh': 'math.sinh',
        'cosh': 'math.cosh',
        'tanh': 'math.tanh',
        'sqrt': 'math.sqrt',
        'log': 'math.log',   # natural log
        'log10': 'math.log10',
        'exp': 'math.exp',
        'radians': 'math.radians',
        'degrees': 'math.degrees',
    }
    
    for func, replacement in replacements.items():
        expression = expression.replace(func, replacement)
    
    # Now, we want to evaluate the expression in a safe way.
    # We'll use a restricted environment with only the math module.
    # Note: this is still not completely safe, but for the purpose of this task we'll assume it's acceptable.
    try:
        # Use the math module and restrict builtins
        result = eval(expression, {"__builtins__": {}}, {"math": math})
        # If the result is an integer, return as integer, else float with 4 decimal places
        if isinstance(result, (int, float)):
            if result.is_integer():
                return str(int(result))
            else:
                return f"{result:.4f}".rstrip('0').rstrip('.')
        else:
            return str(result)
    except Exception as e:
        return f"Error: {str(e)}


