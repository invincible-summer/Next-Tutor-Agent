"""Unified tool result protocol (adapted from Paper_Agent D-067).

Every tool returns a ToolResult: {status, data, text, error}. status in
success|partial|error; error.code is machine-readable so the agent can branch.
This is what makes the agent diagnosable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    BAD_ARGS = "BAD_ARGS"
    NO_TOOL = "NO_TOOL"
    TOOL_ERROR = "TOOL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    TIMEOUT = "TIMEOUT"
    DUPLICATE_CALL = "DUPLICATE_CALL"


@dataclass
class ToolResult:
    tool: str
    status: str = "success"
    data: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    error: dict[str, Any] | None = None
    error_code: str | None = None

    @property
    def is_error(self) -> bool:
        return self.status == "error"

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "status": self.status, "data": self.data,
                "text": self.text, "error": self.error, "error_code": self.error_code}


def ok(tool: str, data: dict | None = None, text: str = "") -> ToolResult:
    return ToolResult(tool=tool, status="success", data=data or {}, text=text)


def partial_result(tool: str, data: dict | None = None, text: str = "") -> ToolResult:
    return ToolResult(tool=tool, status="partial", data=data or {}, text=text)


def err(tool: str, code: ErrorCode | str, message: str, data: dict | None = None) -> ToolResult:
    c = code.value if isinstance(code, ErrorCode) else str(code)
    return ToolResult(tool=tool, status="error", data=data or {}, text=message,
                      error={"code": c, "message": message}, error_code=c)
