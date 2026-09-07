"""Live thinking stream gate (display-only provider reasoning).

The executor/V1 chat loop forwards provider ``reasoning_content`` deltas to
the browser as ``thinking`` SSE events (``summary: False``) so the student
sees the deep-thinking process as it happens. The gate bounds how much is
streamed per turn:

  - ``-1`` (default, REASONING_LIVE_MAX_CHARS=-1): stream everything;
  - ``0``: off — the pre-2026-09 hidden-CoT behavior (template summaries
    only, grounding turns force-disable thinking);
  - ``>0``: cap the total streamed characters at N.

Contract (DESIGN.md §4.3/§5.1): live thinking is display-only. It is never
persisted into session.messages, never logged, and never reaches the voice
WebSocket/TTS pipeline — persistence keeps storing the public summary, and
voice.py keeps dropping thinking events server-side.
"""
from __future__ import annotations


class LiveThinkingGate:
    """Slice/counter helper shared by executor.py and chat_agent.py."""

    __slots__ = ("max_chars", "emitted")

    def __init__(self, max_chars: int):
        try:
            max_chars = int(max_chars)
        except (TypeError, ValueError):
            max_chars = -1
        self.max_chars = -1 if max_chars < 0 else max_chars
        self.emitted = 0

    @property
    def enabled(self) -> bool:
        return self.max_chars != 0

    @property
    def remaining_hint(self) -> str:
        """Short trace label for which mode the gate is in."""
        if self.max_chars < 0:
            return "unlimited"
        return f"cap:{self.max_chars}" if self.max_chars else "off"

    def take(self, delta: str) -> str:
        """Return the slice of ``delta`` still allowed this turn.

        Empty string means "drop" (gate off or cap exhausted). Updates the
        emitted counter only for what it actually returns.
        """
        if self.max_chars == 0 or not delta:
            return ""
        if self.max_chars > 0:
            remaining = self.max_chars - self.emitted
            if remaining <= 0:
                return ""
            if len(delta) > remaining:
                delta = delta[:remaining]
        self.emitted += len(delta)
        return delta
