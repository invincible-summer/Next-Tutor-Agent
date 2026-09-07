"""Feedback analyzer: classify student-side UX feedback from their phrasing.

Rule-based, zero LLM (like the M6 classifier and the M7 trace_analyzer). A
student saying "太复杂了/看不懂" is an EXPERIENCE signal, not an academic one --
it means the explanation did not fit, and the UX profile should adjust (shorten
or simplify future expression). This is distinct from M4's quiz verdict
(academic correctness) and M7's failure diagnosis (teaching effectiveness).

Returns FeedbackType.NONE when nothing matches, so a turn is unchanged.
"""
from __future__ import annotations

from .schema import FeedbackType

# Ordered by specificity. Each entry: (FeedbackType, [keyword phrases]).
# Chinese-first (primary audience), with a few English fallbacks. Kept short
# and unambiguous to avoid false positives on normal questions.
_RULES: list[tuple[FeedbackType, tuple[str, ...]]] = [
    (FeedbackType.EXPLANATION_TOO_HARD,
     ("看不懂", "太难了", "太复杂了", "太复杂", "不懂", "不明白", "听不懂",
      "不理解", "没看懂", "搞不懂", "confusing", "too hard", "don't understand")),
    (FeedbackType.EXPLANATION_TOO_LONG,
     ("太长了", "太啰嗦", "讲太多了", "太多", "废话", "太长", "too long",
      "too verbose", "简短点", "精简")),
    (FeedbackType.EXPLANATION_TOO_SHORT,
     ("太简略", "太短了", "没讲清", "没讲清楚", "展开", "详细点", "再多说点",
      "详细些", "太简短", "too short", "more detail")),
    (FeedbackType.TOO_FAST,
     ("太快了", "太快", "跟不上", "跳太快", "too fast")),
    (FeedbackType.TOO_SLOW,
     ("太慢了", "太慢", "讲快点", "啰嗦", "too slow")),
    (FeedbackType.PRAISE,
     ("讲得好", "讲得真好", "谢谢老师", "谢谢", "懂了", "明白了", "讲清楚",
      "学到了", "厉害", "great", "thanks", "got it")),
]


def classify(message: str) -> FeedbackType:
    """Classify a student message into a UX FeedbackType (rule-based).

    Scans in rule order; the FIRST matching rule wins so that the most
    specific signal (e.g. too-hard) is not shadowed by a vaguer one. Returns
    FeedbackType.NONE when no rule matches. Never raises.
    """
    if not message:
        return FeedbackType.NONE
    msg = message
    try:
        for ftype, phrases in _RULES:
            for p in phrases:
                if p in msg:
                    return ftype
    except Exception:
        return FeedbackType.NONE
    return FeedbackType.NONE


def is_experience_signal(ftype: FeedbackType) -> bool:
    """True for feedback that should adjust the UX profile (i.e. NOT praise and
    not NONE). Praise is recorded for morale but does not change style."""
    return ftype not in (FeedbackType.PRAISE, FeedbackType.NONE)
