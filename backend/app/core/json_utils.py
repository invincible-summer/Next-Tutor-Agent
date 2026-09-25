"""Tolerant JSON loading for LLM output (quiz chain shared helper).

Reasoning models routinely emit LaTeX inside JSON strings — `$\\{a_n\\}$`,
`$\\sqrt{2}$`, `$\\frac{m}{V}$`. A single backslash before `{`, `}`, `$`,
`(`, `)` … is an *invalid JSON escape*, so a plain ``json.loads`` rejects the
whole document even though every structural character is intact. Live
verification (update_plan acceptance) showed this turning every generation
attempt into "0 questions" — the chat quiz card then never appears.

``loads_tolerant`` first tries strict ``json.loads`` and only on
``JSONDecodeError`` retries once with invalid backslash escapes repaired
(``\\{`` → ``\\\\{``, i.e. the characters the writer visibly intended).
The repair is semantics-preserving: valid escapes (``\\" \\\\ \\/ \\b \\f
\\n \\r \\t \\uXXXX``) are passed through untouched, and a lone trailing
backslash is escaped rather than truncated.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Escapes that are valid in JSON string literals.
_VALID_ESCAPE_NEXT = set('"\\/bfnrtu')

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def repair_backslash_escapes(text: str) -> str:
    """Escape backslashes that do not begin a valid JSON escape sequence."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1] if i + 1 < n else ""
        if nxt in _VALID_ESCAPE_NEXT:
            if nxt == "u":
                hex_tail = text[i + 2:i + 6]
                if len(hex_tail) == 4 and re.fullmatch(
                        r"[0-9a-fA-F]{4}", hex_tail):
                    out.append(text[i:i + 6])
                    i += 6
                    continue
                # \u 后不足 4 位十六进制：按非法转义修复（保留字面 \u）。
                out.append("\\\\")
                i += 1
                continue
            out.append(ch)
            out.append(nxt)
            i += 2
            continue
        out.append("\\\\")
        i += 1
    return "".join(out)


# json.loads 会把 \f \b \r \t \n 等合法转义还原成控制字符；数学题干里
# 这些几乎总是 LaTeX 命令的开头（\frac \beta \right \tau \nu），反斜杠被
# 当作转义标记"吃掉"后公式损坏（live 验收：$\frac{1}{2}$ 变成 <FF>rac{1}{2}）。
# 恢复规则按"控制字符 + 后续字母 = LaTeX 命令"反推被吃掉的反斜杠。
_RESTORE_ALWAYS = (("\x0c", "\\f"), ("\x08", "\\b"), ("\x0b", "\\v"))
_RESTORE_BEFORE_LETTER = re.compile("([\r\t])(?=[A-Za-z])")
# 真换行很常见，只有 \n 后紧跟 u/e/o（\nu \neq \not …）才视为 LaTeX。
_RESTORE_LF = re.compile("\n(?=[ueo])")


def _restore_eaten_backslashes(value: Any) -> Any:
    """Recursively undo JSON control escapes that ate LaTeX backslashes."""
    if isinstance(value, str):
        for ctrl, latex in _RESTORE_ALWAYS:
            value = value.replace(ctrl, latex)
        value = _RESTORE_BEFORE_LETTER.sub(lambda m: {
            "\r": "\\r", "\t": "\\t"}[m.group(1)], value)
        return _RESTORE_LF.sub("\\\\n", value)
    if isinstance(value, list):
        return [_restore_eaten_backslashes(v) for v in value]
    if isinstance(value, dict):
        return {_restore_eaten_backslashes(k): _restore_eaten_backslashes(v)
                for k, v in value.items()}
    return value


def loads_tolerant(text: str) -> Any:
    """``json.loads`` with backslash-escape repair, LaTeX-preserving.

    Pass 1 repairs invalid escapes (``\\{`` → ``\\\\{``) when strict parsing
    rejects the document. After a successful parse (either pass), control
    characters that JSON escapes produce are restored to their backslash
    forms when they are unmistakably LaTeX commands — ``\\frac`` must reach
    the student as ``\\frac``, never ``<form-feed>rac``.

    Raises ``json.JSONDecodeError`` (with the *repaired* attempt's error) if
    both passes fail, mirroring the strict API so callers keep a single
    except-clause.
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(repair_backslash_escapes(text))
        except json.JSONDecodeError as exc:
            raise exc
    return _restore_eaten_backslashes(parsed)


def extract_json_object(raw: str) -> Any:
    """First ``{...}`` span (DOTALL) parsed tolerantly; None if unparseable.

    Same contract as the ``re.search(r"\\{.*\\}")`` idiom previously
    duplicated across quiz parsers, so LLM prose around the object is
    ignored.
    """
    m = _JSON_OBJECT_RE.search(raw)
    candidate = m.group(0) if m else raw
    try:
        return loads_tolerant(candidate)
    except json.JSONDecodeError:
        return None
