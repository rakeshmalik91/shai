"""Load canonical model-facing tool declarations from assets/tools_registry.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "assets" / "tools_registry.json"

def load_tool_registry(path=None):
    """Return validated tool declarations keyed by tool name."""
    registry_path = Path(path) if path else REGISTRY_PATH
    with registry_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    entries = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Tool registry must contain a tools array.")
    result = {}
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("function"), dict):
            raise ValueError("Each tool registry entry must contain a function object.")
        function = item["function"]
        name = function.get("name")
        if not isinstance(name, str) or not name or name in result:
            raise ValueError("Tool registry contains a missing or duplicate tool name.")
        result[name] = item
    return result

def load_tool_functions(path=None, chat_only=False):
    """Return OpenAI-compatible function declarations in registry order."""
    entries = load_tool_registry(path)
    return [
        {"type": "function", "function": item["function"]}
        for item in entries.values()
        if not chat_only or item.get("chat_available") is True
    ]
