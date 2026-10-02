"""Tool base class for the async agent. Each tool is name+description+schema+run.

The description is the single most important field: the LLM uses it to decide
*when* to call the tool. All tools return the unified ToolResult protocol.
"""
from __future__ import annotations

import abc
from typing import Any

from .tool_protocol import ToolResult


class Tool(abc.ABC):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    @abc.abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        ...

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"
