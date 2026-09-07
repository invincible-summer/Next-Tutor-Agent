"""Projection from rich material-grounded turns to safe long-term memory text."""
from __future__ import annotations

import re

_TAG_BLOCKS = re.compile(
    r"<(?:material_excerpt|ocr_material)>.*?</(?:material_excerpt|ocr_material)>",
    re.IGNORECASE | re.DOTALL,
)
_AUTO_RESEARCH = re.compile(r"\[系统自动预检索\].*", re.DOTALL)


def memory_safe_text(text: str, *, max_chars: int = 1200) -> str:
    """Remove source/OCR bodies while retaining the student's learning intent.

    This is deterministic and runs before transcript-based cross-session recall,
    M6 consolidation and workspace public-memory summarization.  Session JSON
    still keeps the original message for same-session resume.
    """
    value = str(text or "")
    value = _TAG_BLOCKS.sub("[资料原文已省略]", value)
    value = _AUTO_RESEARCH.sub("[本轮已基于资料检索]", value)
    if value.lstrip().startswith("[OCR题目]"):
        # Legacy frontend format: OCR body followed by a blank line and the
        # actual instruction. Keep the final instruction only.
        tail = value.rsplit("\n\n", 1)[-1].strip()
        value = f"[用户上传了题目图片] {tail}" if tail else "[用户上传了题目图片]"
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value[:max_chars]
