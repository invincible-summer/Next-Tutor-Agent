"""Internal native-tool message projection used in shadow before migration."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .context import estimate_tokens


@dataclass(frozen=True)
class InternalToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class InternalMessage:
    role: str
    content: str = ""
    tool_calls: tuple[InternalToolCall, ...] = field(default_factory=tuple)
    tool_call_id: str = ""
    tool_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content,
                "tool_calls": [x.__dict__ for x in self.tool_calls],
                "tool_call_id": self.tool_call_id, "tool_name": self.tool_name}


def build_native_tool_shadow(assistant_content: str, *, call_id: str,
                             tool_name: str, args: dict[str, Any],
                             result_text: str) -> dict[str, Any]:
    call_id = call_id or f"shadow_{tool_name}_{uuid.uuid4().hex[:10]}"
    messages = [
        InternalMessage(role="assistant", content=assistant_content,
                        tool_calls=(InternalToolCall(call_id, tool_name, args),)),
        InternalMessage(role="tool", content=result_text,
                        tool_call_id=call_id, tool_name=tool_name),
    ]
    serialized = json.dumps([m.to_dict() for m in messages], ensure_ascii=False)
    return {"valid": bool(call_id and tool_name), "tool_call_id": call_id,
            "message_count": len(messages),
            "estimated_tokens": estimate_tokens(serialized)}


def build_openai_tool_messages(assistant_content: str, *, call_id: str,
                               tool_name: str, args: dict[str, Any],
                               result_text: str) -> list[dict[str, Any]]:
    call_id = call_id or f"call_{tool_name}_{uuid.uuid4().hex[:10]}"
    return [
        {"role": "assistant", "content": assistant_content or None,
         "tool_calls": [{"id": call_id, "type": "function",
                         "function": {"name": tool_name,
                                      "arguments": json.dumps(args, ensure_ascii=False)}}]},
        {"role": "tool", "tool_call_id": call_id, "content": result_text},
    ]


def native_to_legacy_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best-effort rollback for Providers rejecting native tool messages."""
    out: list[dict[str, Any]] = []
    pending: dict[str, tuple[str, str]] = {}
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            content = str(message.get("content") or "")
            for call in message.get("tool_calls") or []:
                cid = str(call.get("id", ""))
                fn = call.get("function") or {}
                pending[cid] = (str(fn.get("name", "")), content)
            continue
        if role == "tool":
            cid = str(message.get("tool_call_id", ""))
            name, content = pending.pop(cid, (str(message.get("name", "tool")), ""))
            out.append({"role": "assistant", "content": content or f"(调用工具: {name})"})
            out.append({"role": "user", "content": str(message.get("content", ""))})
            continue
        out.append(message)
    return out
