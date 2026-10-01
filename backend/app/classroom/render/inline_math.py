"""Recognize explicit TeX and conservative, unwrapped math in prose.

Only well-known math commands start an implicit expression. File paths, unknown
commands and ordinary prose remain text; code blocks never call this parser.
"""
from __future__ import annotations

import re
from collections.abc import Iterator

_EXPLICIT = re.compile(
    r"(?<!\\)\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|"
    r"(?<![\\\d])\$(?!\s)([^$\n]+?)(?<![\s\\])\$(?!\d)", re.S)
_COMMAND = re.compile(r"\\([A-Za-z]+)(?![A-Za-z])")
_START = re.compile(r"(?<![\\A-Za-z0-9:/])\\[A-Za-z]+")
_SYMBOLS = frozenset("""
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa
lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega
Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
triangle nabla partial infty pm mp times cdot div neq ne leq le geq ge
approx equiv propto sim simeq in notin subset subseteq supset supseteq
cup cap emptyset forall exists neg land lor to rightarrow leftarrow leftrightarrow
Rightarrow Leftarrow Leftrightarrow ldots cdots vdots ddots angle perp parallel
sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh log ln exp lim min max
sum prod int iint oint Re Im degree ell hbar
""".split())
_ARGUMENTS = {"frac": 2, "dfrac": 2, "tfrac": 2, "sqrt": 1,
              "vec": 1, "overrightarrow": 1, "hat": 1, "bar": 1,
              "overline": 1, "dot": 1, "ddot": 1, "mathbf": 1,
              "mathrm": 1, "mathit": 1, "mathbb": 1, "mathcal": 1,
              "text": 1, "operatorname": 1}
_ATOM = re.compile(r"(?:[A-Za-z](?![A-Za-z])|\d+(?:\.\d+)?)")


def _group_end(text: str, pos: int, left: str = "{", right: str = "}") -> int:
    if pos >= len(text) or text[pos] != left:
        return pos
    depth = 0
    for i in range(pos, len(text)):
        if i and text[i - 1] == "\\":
            continue
        if text[i] == left:
            depth += 1
        elif text[i] == right:
            depth -= 1
            if not depth:
                return i + 1
    return pos


def _atom_end(text: str, pos: int) -> int:
    command = _COMMAND.match(text, pos)
    if command:
        name = command[1]
        if name not in _SYMBOLS and name not in _ARGUMENTS:
            return pos
        end = command.end()
        if name == "sqrt" and end < len(text) and text[end] == "[":
            optional = _group_end(text, end, "[", "]")
            if optional == end:
                return pos
            end = optional
        for _ in range(_ARGUMENTS.get(name, 0)):
            start = end
            while start < len(text) and text[start] == " ":
                start += 1
            end = _group_end(text, start)
            if end == start:
                return pos
        return end
    group = _group_end(text, pos)
    if group != pos:
        return group
    atom = _ATOM.match(text, pos)
    return atom.end() if atom else pos


def _bare_end(text: str, start: int) -> int:
    end = _atom_end(text, start)
    if end == start:
        return start
    while end < len(text):
        pos = end
        while pos < len(text) and text[pos] == " ":
            pos += 1
        if pos >= len(text):
            break
        # Consume an operator/script only when followed by a complete atom.
        if text[pos] in "_^=+-*/<>":
            pos += 1
            while pos < len(text) and text[pos] == " ":
                pos += 1
        next_end = _atom_end(text, pos)
        if next_end == pos:
            break
        end = next_end
    return end


def _implicit_parts(text: str) -> Iterator[tuple[bool, str]]:
    end = 0
    for match in _START.finditer(text):
        if match.start() < end:
            continue
        stop = _bare_end(text, match.start())
        if stop == match.start():
            continue
        yield False, text[end:match.start()]
        yield True, text[match.start():stop]
        end = stop
    yield False, text[end:]


def math_parts(text: str) -> Iterator[tuple[bool, str]]:
    """Yield (is_math, source), keeping explicit delimiters authoritative."""
    end = 0
    for match in _EXPLICIT.finditer(text):
        yield from _implicit_parts(text[end:match.start()])
        yield True, next(group for group in match.groups() if group is not None)
        end = match.end()
    yield from _implicit_parts(text[end:])
