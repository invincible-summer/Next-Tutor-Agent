"""Server-owned illustration permissions, shared by chat, fit and CAT."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IllustrationRequest = Literal["auto", "none", "required"]
IllustrationPolicy = Literal["off", "auto", "required"]
REQUEST_SCHEMA = {"type": "string", "enum": ["auto", "none", "required"],
                  "description": "明确要求配图才用 required；明确不要图用 none；其余 auto。账户权限由服务端控制。"}


class IllustrationDisabled(ValueError):
    code = "illustration_disabled"


def explicit_illustration_request(message: str) -> IllustrationRequest:
    # Quoted examples/material are data, not switches. Prefer a conservative
    # fallback; the understanding model can handle less explicit phrasing.
    message = re.sub(r"<(material_excerpt|ocr_material|history_excerpt|reference)[^>]*>[\s\S]*?</\1>", " ", message, flags=re.I)
    message = re.sub(r"(?m)^\s*>.*$", " ", message)
    text = re.sub(r"```[\s\S]*?```|[“\"][^”\"]*[”\"]|<[^>]*>", " ", message)
    if re.search(r"(?:不要|无需|不用|不必|不需要|禁止).{0,6}(?:配图|插图|示意图|画图)|"
                 r"纯文字题|文字题即可|(?:no|without)\s+(?:diagrams?|illustrations?|images?)", text, re.I):
        return "none"
    if re.search(r"(?:请.{0,8}配图|配.{0,3}(?:一|张)?图)|(?:带|附|配|提供|生成|需要|必须|包含|画).{0,5}(?:插图|示意图|配图|图像)|"
                 r"(?:带图|有图).{0,5}(?:题|练习)|(?:with|include|add|require)\s+(?:a\s+)?(?:diagrams?|illustrations?)", text, re.I):
        return "required"
    return "auto"


def svg_available() -> bool:
    from .config import settings
    return settings.quiz_svg_enabled


def _account_pref(student_id: str, key: str, default: bool) -> bool:
    if not student_id or student_id in {"student_default", "compat_agent"}:
        return default
    try:
        from ..identity.store import get_by_id
        user = get_by_id(student_id)
        if user is None:
            return default
        value = user.profile.prefs.get(key, default)
        return value is True if default else value is not False
    except Exception:
        return default


def account_allows_illustration(student_id: str) -> bool:
    if not svg_available() or not student_id or student_id in {"student_default", "compat_agent"}:
        return False
    try:
        from ..identity.store import get_by_id
        user = get_by_id(student_id)
        if user is None:
            return False
        value = user.profile.prefs.get("quiz_svg_enabled", True)
        return value is True
    except Exception:
        return False


def account_allows_illustration_review(student_id: str) -> bool:
    """Per-student switch for the post-generation diagram audit LLM call.

    The deterministic sanitizer always runs; this only gates the independent
    semantic audit, which is the step most likely to time out or false-reject
    on a real model.
    """
    return _account_pref(student_id, "quiz_illustration_review_enabled", True)


def account_allows_quiz_critic(student_id: str) -> bool:
    """Per-student switch for the post-generation question critic."""
    return _account_pref(student_id, "quiz_critic_enabled", True)


def effective_quiz_verify_mode(student_id: str) -> str:
    """Resolve QUIZ_VERIFY_MODE with the account-level critic switch.

    The environment keeps final authority to *lower* quality gates only: an
    operator-set basic/off stays basic/off. A student switch can only turn a
    deployment-default critic lane down to basic, never up.
    """
    from .config import settings
    mode = str(settings.quiz_verify_mode or "critic").strip().lower()
    if mode not in {"critic", "basic", "off"}:
        mode = "critic"
    if mode == "critic" and not account_allows_quiz_critic(student_id):
        return "basic"
    return mode


def resolve_illustration_policy(student_id: str, request: str = "auto") -> IllustrationPolicy:
    if request not in {"auto", "none", "required"}:
        raise ValueError("invalid_illustration_request")
    if not account_allows_illustration(student_id):
        if request == "required":
            raise IllustrationDisabled("题目插图生成已关闭，请在出题中心开启后重试。")
        return "off"
    return "off" if request == "none" else request


@dataclass
class IllustrationPolicyProvider:
    student_id: str
    explicit_request: IllustrationRequest = "auto"

    def bind_understanding(self, request: str) -> None:
        if self.explicit_request == "auto" and request in {"none", "required"}:
            self.explicit_request = request

    def __call__(self, requested: str = "auto") -> IllustrationPolicy:
        # A tool call cannot pretend the user required illustrations. It may
        # suppress images; strong requirements come from the bound user turn.
        effective = self.explicit_request
        if effective == "auto" and requested == "none":
            effective = "none"
        return resolve_illustration_policy(self.student_id, effective)
