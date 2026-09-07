"""Tool parameter validation (agent-develop pre-ship checklist).

Validates tool call arguments against the tool's JSON schema before execution:
type checks, required fields, enum/range constraints. Returns a ToolResult
error on failure so the agent gets diagnosable feedback instead of a crash.
"""
from __future__ import annotations

from typing import Any

from .tool_protocol import ErrorCode, ToolResult, err


def validate_tool_args(tool_name: str, args: dict[str, Any],
                       schema: dict[str, Any]) -> ToolResult | None:
    """Return None if valid, or a ToolResult error if invalid."""
    if not isinstance(args, dict):
        return err(tool_name, ErrorCode.BAD_ARGS, f"参数必须是一个对象，得到 {type(args).__name__}")
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    for field in required:
        if field not in args or args[field] is None:
            return err(tool_name, ErrorCode.BAD_ARGS, f"缺少必填参数: {field}")
    for key, val in args.items():
        spec = props.get(key)
        if spec is None:
            continue  # allow extra keys (forward-compat)
        vtype = spec.get("type")
        if vtype and not _check_type(val, vtype):
            return err(tool_name, ErrorCode.BAD_ARGS,
                       f"参数 {key} 类型应为 {vtype}，得到 {type(val).__name__}")
        if "enum" in spec and val not in spec["enum"]:
            return err(tool_name, ErrorCode.BAD_ARGS,
                       f"参数 {key} 值 '{val}' 不在允许范围 {spec['enum']} 内")
        if vtype == "integer":
            if "minimum" in spec and isinstance(val, (int, float)) and val < spec["minimum"]:
                return err(tool_name, ErrorCode.BAD_ARGS,
                           f"参数 {key} 值 {val} 小于最小值 {spec['minimum']}")
            if "maximum" in spec and isinstance(val, (int, float)) and val > spec["maximum"]:
                return err(tool_name, ErrorCode.BAD_ARGS,
                           f"参数 {key} 值 {val} 大于最大值 {spec['maximum']}")
    return None


def _check_type(val: Any, vtype: str) -> bool:
    if vtype == "string":
        return isinstance(val, str)
    if vtype == "integer":
        return isinstance(val, int) and not isinstance(val, bool)
    if vtype == "number":
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    if vtype == "boolean":
        return isinstance(val, bool)
    if vtype == "array":
        return isinstance(val, list)
    if vtype == "object":
        return isinstance(val, dict)
    return True
