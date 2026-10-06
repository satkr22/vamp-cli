# registry.py
from typing import Dict, List, Optional, Any
from vamp_cli.tools.base import Tools


class ToolRegistry:
    """
    Registry mapping tool names -> tool instances.

    Tools are expected to inherit from `Tools` (exposing `name`,
    `description`, `input_schema`, `schema()` and `run()`).
    """

    def __init__(self) -> None:
        self._tools: Dict[str, Tools] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(self, tool: Tools) -> None:
        """Register a single tool. Raises if the name is already taken."""
        if not getattr(tool, "name", None):
            raise ValueError("Tool must define a non-empty `name`.")
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def register_many(self, tools: List[Tools]) -> None:
        """Register several tools at once (atomic: fails on first conflict)."""
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> None:
        """Remove a tool by name. Raises KeyError if not found."""
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found.")
        del self._tools[name]

    def clear(self) -> None:
        """Remove all registered tools."""
        self._tools.clear()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get_tool(self, name: str) -> Dict[str, Tools]:
        """
        Return a single tool as `{name: tool_object}`.
        Raises KeyError if the tool is not registered.
        """
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found.")
        return {name: self._tools[name]}

    def get_optional_tool(self, name: str) -> Optional[Dict[str, Tools]]:
        """
        Return `{name: tool_object}` if registered, otherwise None.
        """
        tool = self._tools.get(name)
        return {name: tool} if tool is not None else None

    def get_tools(self, names: List[str]) -> Dict[str, Tools]:
        """
        Resolve a list of tool names to `{name: tool_object}`.

        - Missing tools are skipped silently.
        - Duplicate names collapse into a single entry.
        """
        resolved: Dict[str, Tools] = {}
        for name in names:
            tool = self._tools.get(name)
            if tool is not None:
                resolved[name] = tool
        return resolved

    def get_tools_strict(self, names: List[str]) -> Dict[str, Tools]:
        """
        Same as `get_tools` but raises KeyError listing every missing name.
        """
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise KeyError(f"Tool(s) not found: {', '.join(missing)}")
        return {n: self._tools[n] for n in names}

    def get_all_tools(self) -> Dict[str, Tools]:
        """Return every registered tool as `{name: tool_object}`."""
        return dict(self._tools)
    
    
    def get_tool_list(self, names: Optional[List[str]] = None) -> List[Tools]:
        """
        Return Tools **objects as a list** — the shape ModelRouter.get expects.
        Unknown names are skipped. Order preserved. Duplicates collapse.
        """
        if names is None:
            return list(self._tools.values())
        seen: set[str] = set()
        out: List[Tools] = []
        for n in names:
            if n in seen:
                continue
            tool = self._tools.get(n)
            if tool is not None:
                out.append(tool)
                seen.add(n)
        return out

    def get_openai_tools(self, names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Ready-to-bind OpenAI-format tool list."""
        return [t.to_openai_tool() for t in self.get_tool_list(names)]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def has_tool(self, name: str) -> bool:
        """True if a tool with the given name is registered."""
        return name in self._tools

    def list_tool_names(self) -> List[str]:
        """All registered tool names."""
        return list(self._tools.keys())

    def get_schemas(self, names: Optional[List[str]] = None) -> List[dict]:
        """
        Return schemas for the given names (or all tools when names is None).
        Suitable for passing to the LLM as the tool/function list.
        """
        if names is None:
            tools = self._tools.values()
        else:
            tools = (self._tools[n] for n in names if n in self._tools)
        return [tool.schema() for tool in tools]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools)

    def __repr__(self) -> str:
        return f"ToolRegistry(tools={self.list_tool_names()})"
    
    
    
# used in agent langraph loop if llm demands a tool

# tool_obj = registry.get_tool(name)[name]     # dict as you specified
# result   = tool_obj.run(**json.loads(tool_call["function"]["arguments"]))