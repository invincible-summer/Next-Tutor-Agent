"""Provider compatibility for streamed tool calls.

DeepSeek V4.1 uses DSML internally for tool calls.  The official API normally
projects those calls onto the OpenAI-compatible ``delta.tool_calls`` channel,
but gateways and compatibility layers can surface the raw DSML text through
``delta.content`` instead.  This module provides a fail-closed streaming parser
for both V4 and V4.1 DSML, plus two small compatibility helpers:

* normalize a leaked ``agent.skill.*@version`` planner identifier back to its
  authorized runtime function name (only when that function is present in the
  actual tool schema for the request);
* keep DeepSeek ``reasoning_content`` transiently keyed by tool-call id so the
  native assistant/tool round-trip can replay it without persisting raw CoT.

The parser deliberately lives below the Agent executor: the executor consumes
one internal ``tool_calls`` contract and never needs to understand DSML syntax.
"""
from __future__ import annotations

from contextvars import ContextVar
import hashlib
import json
import re
from typing import Any, Iterable


# V4.1 changed ``<｜DSML｜tool_calls>`` to spaced tags such as
# ``<｜DSML｜ calls>``.  Some gateways render the special-token bars doubled;
# both spellings are accepted here because either form must never reach UI/TTS.
_DSML_TOKEN = r"(?:｜){1,2}DSML(?:｜){1,2}"
_OPEN_RE = re.compile(
    rf"<{_DSML_TOKEN}\s*(?P<kind>calls|tool_calls)\s*>", re.IGNORECASE)
_CLOSE_RE = re.compile(
    rf"</{_DSML_TOKEN}\s*(?:calls|tool_calls)\s*>", re.IGNORECASE)
_INVOKE_RE = re.compile(
    rf"<{_DSML_TOKEN}\s*invoke\b(?P<attrs>[^>]*)>"
    rf"(?P<body>.*?)</{_DSML_TOKEN}\s*invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_PARAMETER_RE = re.compile(
    rf"<{_DSML_TOKEN}\s*parameter\b(?P<attrs>[^>]*)>"
    rf"(?P<value>.*?)</{_DSML_TOKEN}\s*parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(r'([A-Za-z_][\w:.-]*)\s*=\s*"([^"]*)"')

_OPEN_PREFIXES = tuple(
    item.lower()
    for item in (
        "<｜DSML｜ calls>",
        "<｜DSML｜tool_calls>",
        "<｜｜DSML｜｜ calls>",
        "<｜｜DSML｜｜tool_calls>",
    )
)
_MAX_DSML_CHARS = 64_000


def _attrs(raw: str) -> dict[str, str]:
    return {m.group(1).lower(): m.group(2) for m in _ATTR_RE.finditer(raw or "")}


def _decode_parameter(value: str, *, is_string: bool) -> Any:
    if is_string:
        return value
    raw = value.strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        # Preserve malformed provider output under the declared key.  The
        # existing server-side tool validator remains authoritative and will
        # reject a wrong type instead of silently changing semantics here.
        return raw


def _synthetic_call_id(name: str, args: dict[str, Any], index: int) -> str:
    payload = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(f"{index}:{name}:{payload}".encode("utf-8")).hexdigest()[:14]
    return f"dsml_{digest}"


class DeepSeekDSMLStreamParser:
    """Incrementally suppress and parse raw DeepSeek V4/V4.1 DSML.

    ``feed`` returns only user-visible natural-language text.  Once a DSML
    control block starts, everything from the opening tag onward is withheld.
    A complete block becomes structured calls available via :attr:`calls`.
    An incomplete/malformed block is fail-closed: it is never flushed into the
    answer channel.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._raw = ""
        self._detected = False
        self._capturing = False
        self._closed = False
        self._malformed = False
        self._protocol = ""
        self._calls: list[dict[str, Any]] = []

    @property
    def detected(self) -> bool:
        return self._detected

    @property
    def malformed(self) -> bool:
        return self._malformed

    @property
    def protocol(self) -> str:
        return self._protocol

    @property
    def calls(self) -> list[dict[str, Any]]:
        return [dict(call) for call in self._calls]

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        if self._closed:
            # A tool-call completion must not contain a second user-visible
            # answer after the control block.  Suppress provider special tokens
            # and any accidental trailing control text as a fail-closed policy.
            return ""
        if self._capturing:
            self._append_capture(delta)
            return ""

        buf = self._pending + delta
        match = _OPEN_RE.search(buf)
        if match is not None:
            self._detected = True
            self._capturing = True
            kind = match.group("kind").lower()
            self._protocol = (
                "deepseek_dsml_v41" if kind == "calls" else "deepseek_dsml_v4"
            )
            visible = buf[:match.start()]
            self._pending = ""
            self._raw = buf[match.start():]
            self._finish_if_complete()
            return visible

        held = self._held_prefix_len(buf)
        if held:
            visible, self._pending = buf[:-held], buf[-held:]
        else:
            visible, self._pending = buf, ""
        return visible

    def flush(self) -> str:
        """Return safe pending text; never release a started DSML block."""
        if self._capturing and not self._closed:
            self._malformed = True
            self._pending = ""
            return ""
        if self._detected:
            self._pending = ""
            return ""
        visible, self._pending = self._pending, ""
        return visible

    def _append_capture(self, delta: str) -> None:
        if len(self._raw) >= _MAX_DSML_CHARS:
            self._malformed = True
            return
        remaining = _MAX_DSML_CHARS - len(self._raw)
        self._raw += delta[:remaining]
        if len(delta) > remaining:
            self._malformed = True
        self._finish_if_complete()

    def _finish_if_complete(self) -> None:
        match = _CLOSE_RE.search(self._raw)
        if match is None:
            return
        block = self._raw[:match.end()]
        self._calls = self._parse_calls(block)
        self._capturing = False
        self._closed = True
        if not self._calls:
            self._malformed = True

    @staticmethod
    def _parse_calls(block: str) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for index, invoke in enumerate(_INVOKE_RE.finditer(block)):
            invoke_attrs = _attrs(invoke.group("attrs"))
            name = str(invoke_attrs.get("name") or "").strip()
            if not name:
                continue
            args: dict[str, Any] = {}
            for parameter in _PARAMETER_RE.finditer(invoke.group("body")):
                param_attrs = _attrs(parameter.group("attrs"))
                key = str(param_attrs.get("name") or "").strip()
                if not key:
                    continue
                is_string = str(param_attrs.get("string") or "").lower() == "true"
                args[key] = _decode_parameter(
                    parameter.group("value"), is_string=is_string)
            calls.append({
                "id": _synthetic_call_id(name, args, index),
                "name": name,
                "args": args,
            })
        return calls

    @staticmethod
    def _held_prefix_len(buf: str) -> int:
        lower = buf.lower()
        max_len = max(len(item) for item in _OPEN_PREFIXES)
        start = max(0, len(buf) - max_len)
        for index in range(start, len(buf)):
            if buf[index] != "<":
                continue
            suffix = lower[index:]
            if any(prefix.startswith(suffix) for prefix in _OPEN_PREFIXES):
                return len(buf) - index
        return 0


def _allowed_tool_names(tools: Iterable[dict[str, Any]] | None) -> set[str]:
    out: set[str] = set()
    for schema in tools or []:
        function = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(function, dict) and function.get("name"):
            out.add(str(function["name"]))
    return out


def normalize_tool_call_name(
    raw_name: str,
    tools: Iterable[dict[str, Any]] | None,
) -> tuple[str, str]:
    """Resolve a provider-emitted name to an authorized function name.

    Returns ``(name, source)``.  Resolution is intentionally conservative:
    aliases are accepted only when the resolved function is present in the
    request's actual tool schema, so Skill Gate authorization cannot be bypassed.
    """
    name = str(raw_name or "").strip()
    allowed = _allowed_tool_names(tools)
    if not name or name in allowed:
        return name, "native"

    # DeepSeek V4.1 supports tool namespaces (``namespace::function``).  The
    # project currently exposes un-namespaced OpenAI functions, so accept the
    # suffix only when it is already authorized by the current schema.
    if "::" in name:
        suffix = name.rsplit("::", 1)[-1]
        if suffix in allowed:
            return suffix, "namespace_suffix"

    # The Tutor context contains planner Skill identifiers for auditability.
    # If a model mistakenly emits that identifier as the function name, map it
    # through the single Skill Registry rather than duplicating aliases here.
    skill_ref = name
    version: str | None = None
    if "@" in name:
        skill_ref, version = name.rsplit("@", 1)
    if skill_ref.startswith("agent.skill."):
        try:
            from ..agents.skill_runtime.registry import registry
            skill = registry.get(skill_ref, version=version or None)
            mapped = str(skill.tool_name or "")
        except (KeyError, ValueError, ImportError):
            mapped = ""
        if mapped and mapped in allowed:
            return mapped, "skill_registry"

    return name, "unresolved"


def normalize_tool_calls(
    calls: Iterable[dict[str, Any]],
    tools: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Normalize ids/names and deduplicate native + DSML projections."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(calls):
        raw_name = str(item.get("name") or "")
        name, source = normalize_tool_call_name(raw_name, tools)
        args = item.get("args")
        if not isinstance(args, dict):
            args = {"_raw": args}
        call_id = str(item.get("id") or "") or _synthetic_call_id(name, args, index)
        signature = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)}"
        if signature in seen:
            continue
        seen.add(signature)
        call = {"id": call_id, "name": name, "args": args}
        if name != raw_name:
            call["raw_name"] = raw_name
            call["name_resolution"] = source
        out.append(call)
    return out


# DeepSeek requires reasoning_content to be replayed on subsequent requests that
# carry tools.  Raw reasoning remains transient: it is keyed only until the
# corresponding assistant/tool message is built, never written to session data.
_REASONING_BY_CALL_ID: ContextVar[dict[str, str]] = ContextVar(
    "tool_reasoning_by_call_id", default={})


def should_replay_tool_reasoning(model: str, base_url: str = "") -> bool:
    model_l = str(model or "").lower()
    url_l = str(base_url or "").lower()
    return "deepseek" in model_l or "api.deepseek.com" in url_l


def remember_tool_reasoning(
    call_ids: Iterable[str],
    reasoning_content: str,
    *,
    model: str,
    base_url: str = "",
) -> None:
    if not reasoning_content or not should_replay_tool_reasoning(model, base_url):
        return
    mapping = dict(_REASONING_BY_CALL_ID.get())
    for call_id in call_ids:
        key = str(call_id or "")
        if key:
            mapping[key] = reasoning_content
    # Bound accidental leftovers from ignored parallel calls without truncating
    # any individual reasoning block (DeepSeek requires the complete value).
    if len(mapping) > 32:
        for key in list(mapping)[:-32]:
            mapping.pop(key, None)
    _REASONING_BY_CALL_ID.set(mapping)


def take_tool_reasoning(call_id: str) -> str:
    key = str(call_id or "")
    if not key:
        return ""
    mapping = dict(_REASONING_BY_CALL_ID.get())
    value = str(mapping.pop(key, "") or "")
    _REASONING_BY_CALL_ID.set(mapping)
    return value
