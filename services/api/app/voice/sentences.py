"""Sentence segmentation for streaming TTS.

Voice turns speak answers while the LLM is still streaming: answer deltas
are buffered and every complete sentence is dispatched to TTS immediately,
so playback of the first sentence starts seconds before the turn ends.
Chinese terminators end a sentence unconditionally; an ASCII dot only ends
one when it is NOT glued to an alphanumeric neighbour (so "3.14" and
"e.g" stay intact while "fine. Next" splits). Terminators inside
$...$ / $$...$$ math spans never split: display formulas wrap lines and
would be cut open mid-span, leaving unbalanced delimiters that downstream
to_speakable can no longer recognize (raw LaTeX read aloud).

The model also writes \\(...\\) / \\[...\\] math; :func:`take_complete`
normalizes those delimiters on its (cumulative, re-scanned) buffer first,
so they enjoy the same no-split protection as dollar spans. Oversized
sentences are force-split by :func:`_force_split`, which never cuts inside
a math span unless the span alone exceeds ``_HARD_MAX`` — the MeloTTS
sidecar rejects requests beyond 400 chars, so an unbreakable formula may
stretch a chunk but never past that ceiling.

:func:`take_speech_cuts` is the clause-level variant the voice endpoint
feeds its synthesis pipeline: a weak punctuation also ends a cut once the
buffer holds ``_SPEECH_MIN_CHARS`` chars, so the first audio clip is
dispatched after ~24 chars (a second or two of speech) and every clip is
short enough to synthesize well inside the previous clip's playback time.
``` code fences join math spans as no-cut zones — the splitter runs on raw
markdown, before :func:`to_speakable` collapses fences to placeholders.
Markdown tables are no-cut units too: every row starts with ``|``, and the
block ends at the first following line that does not; the voice worker
replaces the whole block with a spoken cue + blackboard event, so slicing
it mid-row would leak half a table into TTS.
"""
from __future__ import annotations

from app.voice.speak_text import normalize_math_delimiters

_SENTENCE_END = "。！？!?…；;"
_MAX_CHARS = 120
_HARD_MAX = 280
_MIN_CUT_RATIO = 0.5
_CUT_PUNCTS = ("，", "、", "：", ",", ":", " ")

# Speech-cut granularity: cut at the first weak punctuation once the buffer
# reaches _SPEECH_MIN_CHARS; past _SPEECH_HARD_MAX even a punctuation-free
# run is cut (mid-word for unbroken prose, never inside a math/fence span)
# so no clip can stretch synthesis into an audible gap.
_SPEECH_WEAK = ("，", "、", "：", ",", ":", " ")
_SPEECH_MIN_CHARS = 24
_SPEECH_HARD_MAX = 120


def split_sentences(text: str) -> list[str]:
    """Split finalized text into speakable sentences (whitespace dropped)."""
    complete, rest = take_complete(text)
    if rest.strip():
        complete.append(rest.strip())
    return _force_split_all(complete)


def take_complete(text: str) -> tuple[list[str], str]:
    """Streaming variant: return (complete sentences, trailing remainder).

    The remainder carries no terminator yet and stays buffered until more
    deltas arrive (the endpoint flushes it on done). An unclosed $ holds
    the buffer — the math span is still streaming in.
    """
    text = normalize_math_delimiters(text)
    sentences: list[str] = []
    buf = ""
    n = len(text)
    in_math = False
    i = 0
    while i < n:
        ch = text[i]
        buf += ch
        if ch == "$":
            # "$$" opens/closes display math, a lone "$" inline math; the
            # same token that opened a span also closes it.
            if text.startswith("$$", i):
                buf += "$"
                i += 2
            else:
                i += 1
            in_math = not in_math
            continue
        if not in_math:
            if ch in _SENTENCE_END:
                sentences.append(buf)
                buf = ""
            elif ch == ".":
                nxt = text[i + 1] if i + 1 < n else ""
                if not (nxt and nxt.isalnum()):
                    sentences.append(buf)
                    buf = ""
        i += 1
    out = [s.strip() for s in sentences if s.strip()]
    return out, buf


def take_speech_cuts(text: str) -> tuple[list[str], str]:
    """Clause-level streaming variant for the synthesis pipeline.

    Same cumulative re-scan contract as :func:`take_complete`: returns
    (complete cuts, trailing remainder) and the remainder stays buffered
    until more deltas arrive. Strong terminators end a cut unconditionally;
    a weak punctuation (，、：, : space) ends one once the buffer holds
    ``_SPEECH_MIN_CHARS`` chars, so the pipeline dispatches short clips
    instead of waiting for a full sentence. Unclosed ``$`` spans,
    ``` fences and markdown tables hold the buffer like an unclosed
    sentence.
    """
    text = normalize_math_delimiters(text)
    cuts: list[str] = []
    buf = ""
    n = len(text)
    in_math = False
    in_fence = False
    in_table = False
    i = 0
    while i < n:
        ch = text[i]
        # Markdown table rows start with '|' at line start: the whole block
        # is one no-cut unit (the voice worker swaps it for a spoken cue +
        # blackboard event), so it must never be sliced mid-row. Any prose
        # buffered before the table is flushed as its own cut first.
        if not in_math and not in_fence and not in_table and ch == "|" \
                and (buf == "" or buf.endswith("\n")):
            if buf.strip():
                cuts.append(buf)
            buf = ""
            in_table = True
        if in_table:
            buf += ch
            # The table ends at the first newline whose next line does not
            # start another '|' row; at a streaming boundary (no next char
            # yet) the block stays buffered and is re-scanned next delta.
            if ch == "\n":
                nxt = text[i + 1] if i + 1 < n else None
                if nxt is not None and nxt != "|":
                    cuts.append(buf)
                    buf = ""
                    in_table = False
            i += 1
            continue
        # Fences win inside themselves (their closer must still toggle) but
        # are ignored inside math spans; $ toggling is ignored inside fences
        # so shell-style dollars in code cannot corrupt the math pairing.
        if not in_math and text.startswith("```", i):
            buf += "```"
            i += 3
            in_fence = not in_fence
            continue
        buf += ch
        if not in_fence and ch == "$":
            if text.startswith("$$", i):
                buf += "$"
                i += 2
            else:
                i += 1
            in_math = not in_math
            continue
        if not in_math and not in_fence:
            if ch in _SENTENCE_END:
                cuts.append(buf)
                buf = ""
            elif ch == ".":
                nxt = text[i + 1] if i + 1 < n else ""
                if not (nxt and nxt.isalnum()):
                    cuts.append(buf)
                    buf = ""
            elif ch in _SPEECH_WEAK and len(buf) >= _SPEECH_MIN_CHARS:
                cuts.append(buf)
                buf = ""
            elif len(buf) >= _SPEECH_HARD_MAX:
                cuts.append(buf)
                buf = ""
        i += 1
    out = [s.strip() for s in cuts if s.strip()]
    return out, buf


def _force_split_all(sentences: list[str]) -> list[str]:
    out: list[str] = []
    for s in sentences:
        out.extend(_force_split(s))
    return out


def _force_split(sentence: str) -> list[str]:
    if len(sentence) <= _MAX_CHARS:
        return [sentence]
    parts: list[str] = []
    rest = sentence
    while len(rest) > _MAX_CHARS:
        cut = _next_cut(rest)
        parts.append(rest[:cut].strip())
        rest = rest[cut:]
    if rest.strip():
        parts.append(rest.strip())
    return [p for p in parts if p]


def _next_cut(text: str) -> int:
    """Characters to keep from the head of ``text``.

    Prefer a weak punctuation outside $-math spans; a long formula pushes
    the cut to just after the span closes (capped at _HARD_MAX) instead of
    slicing LaTeX mid-command. Pure prose without any punctuation falls
    back to the plain _MAX_CHARS cut.
    """
    outside = _cut_positions(text, _MAX_CHARS + 1)
    idx = outside[-1] if outside else -1
    if idx >= _MAX_CHARS * _MIN_CUT_RATIO:
        return idx + 1
    outside = _cut_positions(text, _HARD_MAX + 1)
    if outside:
        return outside[-1] + 1
    if "$" not in text[:_HARD_MAX]:
        return _MAX_CHARS
    # The window is (mostly) one math span: cut right after it closes, or
    # at a space inside it as a last resort — never past the sidecar cap.
    closed = _math_close(text, _HARD_MAX + 1)
    if closed:
        return closed
    space = text.rfind(" ", 0, _HARD_MAX)
    comma = max(text.rfind("，", 0, _HARD_MAX), text.rfind(",", 0, _HARD_MAX))
    idx = max(space, comma)
    if idx >= _MAX_CHARS * _MIN_CUT_RATIO:
        return idx + 1
    return _HARD_MAX


def _cut_positions(text: str, limit: int) -> list[int]:
    """Indices of weak punctuation that sit outside $-math spans."""
    positions: list[int] = []
    in_math = False
    i = 0
    n = min(len(text), limit)
    while i < n:
        ch = text[i]
        if ch == "$":
            step = 2 if text.startswith("$$", i) else 1
            in_math = not in_math
            i += step
            continue
        if not in_math and ch in _CUT_PUNCTS:
            positions.append(i)
        i += 1
    return positions


def _math_close(text: str, limit: int) -> int:
    """Index just past the first math-span close, 0 when none closes."""
    in_math = False
    i = 0
    n = min(len(text), limit)
    while i < n:
        if text[i] == "$":
            step = 2 if text.startswith("$$", i) else 1
            in_math = not in_math
            i += step
            if not in_math:
                return i
            continue
        i += 1
    return 0
