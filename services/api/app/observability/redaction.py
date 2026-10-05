"""Default-deny log redaction.

Anything log-shaped passes through here first: credential-ish keys,
JWT/refresh material, provider API keys and raw auth headers collapse to
their TYPE with lengths, never their values. Prompt/conversation/audio
content has no place in logs at all — ``redact`` keeps only a size marker
for the known content keys so debugging stays possible without leaking.
"""
from __future__ import annotations

import re
from typing import Any

# Keys whose VALUES never appear in logs — collapsed to "<redacted:len>".
SECRET_KEYS = frozenset({
    "password", "new_password", "old_password", "password_hash",
    "secret", "client_secret", "client_secret_encrypted",
    "token", "access_token", "refresh_token", "id_token", "api_key",
    "authorization", "auth", "cookie", "set-cookie", "jwt",
    "llm_api_key", "tavily_api_key", "pexels_api_key", "pixabay_api_key",
    "azure_speech_key", "iflytek_api_key", "iflytek_api_secret",
    "deepgram_api_key", "session", "ticket", "private_pem", "secret_hash",
})

# Keys whose values are CONTENT (prompts/messages/audio/notes): log only a
# size marker, never the payload.
CONTENT_KEYS = frozenset({
    "prompt", "messages", "message", "content", "body", "text", "answer",
    "transcript", "audio", "svg", "html", "note", "note_text", "question",
    "explanation", "user_input", "chunk", "page_text", "ocr_text",
})

_JWT_SHAPE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_BEARER = re.compile(r"(?i)bearer\s+\S+")
_PROVIDER_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")

_REDACTED = "<redacted>"


def _collapse(value: str, marker: str) -> str:
    return f"{marker}:{len(value)}"


def redact(value: Any) -> Any:
    """Recursively redact a log-shaped payload.

    Dict keys matching SECRET_KEYS/CONTENT_KEYS collapse; strings anywhere
    lose JWT/bearer/provider-key shapes. Unknown structures pass through
    structurally — redaction is key-based, not blocklist-of-the-world.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str.lower() in SECRET_KEYS:
                out[key_str] = (_collapse(item, _REDACTED)
                                if isinstance(item, str) else _REDACTED)
            elif key_str.lower() in CONTENT_KEYS:
                out[key_str] = _collapse(item, "<content>") if isinstance(
                    item, str) else "<content>"
            else:
                out[key_str] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        redacted = _JWT_SHAPE.sub(_REDACTED, value)
        redacted = _BEARER.sub("Bearer " + _REDACTED, redacted)
        redacted = _PROVIDER_KEY.sub(_REDACTED, redacted)
        return redacted
    return value


def redact_record(extra: dict[str, Any] | None) -> dict[str, Any]:
    """Convenience for logging ``extra`` fields."""
    return redact(dict(extra or {}))
