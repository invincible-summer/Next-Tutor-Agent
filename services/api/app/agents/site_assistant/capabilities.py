"""助手能力旗标（plan.md §19.9）。

capabilities 是后端真实白名单：只反映配置与身份，不查询个人记录；
前端不以其缓存作为最终授权，执行动作前会重新校验。
"""
from __future__ import annotations

from app.agents.site_assistant import catalog
from app.core.config import settings
from app.identity.models import User
from app.schemas.assistant import (
    AssistantCapabilities,
    ModuleCapability,
)

# 当前实现并开放的只读工具白名单（§10.3，A09 全量八工具）；
# A12 起接入概念主张到报告卡；B02 增加全站实体检索。
IMPLEMENTED_TOOLS: list[str] = [
    "get_product_help",
    "resolve_destination",
    "get_learning_summary",
    "get_concept_explanation",
    "get_teaching_summary",
    "get_saved_tasks",
    "find_course",
    "get_reference_detail",
    "search_site_entities",
]

# 当前实现并开放的动作 payload kind 白名单（§9.1；A10 导航族 + A13
# 交接族）——与 policy.IMPLEMENTED_ACTION_KINDS 单一来源。
from app.agents.site_assistant.policy import IMPLEMENTED_ACTION_KINDS

IMPLEMENTED_ACTION_KINDS: list[str] = list(IMPLEMENTED_ACTION_KINDS)


def model_available() -> bool:
    """单通道 LLM 是否可用；无 key 时按不可用降级，不伪造可用。"""
    return bool(settings.llm_api_key)


def assistant_enabled() -> bool:
    return bool(settings.site_assistant_enabled)


def actions_enabled() -> bool:
    """§26.5 领域写开关；action_kinds 如实反映（总开关由 enabled 把关）。"""
    return bool(settings.site_assistant_actions_enabled)


def workflows_enabled() -> bool:
    """§26.5 工作流开关；start_workflow 动作与 disabled_reasons 如实反映。"""
    return bool(settings.site_assistant_workflows_enabled)


def proactive_enabled() -> bool:
    """§26.5 主动服务调度开关；manage_subscription 与订阅设置如实反映。"""
    return bool(settings.site_assistant_proactive_enabled)


def _classroom_open_for(student_id: str) -> tuple[bool, str | None]:
    if not settings.classroom_enabled:
        return False, "课堂功能未开启"
    try:
        from app.classroom import capabilities as classroom_caps
        allowed, reason = classroom_caps.user_allowed(student_id)
        if not allowed:
            return False, reason or "课堂功能未对你开放"
    except Exception:  # 课堂能力读取失败按不可用处理，不伪造可用
        return False, "课堂能力状态未知"
    return True, None


def build_assistant_capabilities(user: User | None) -> AssistantCapabilities:
    enabled = assistant_enabled()
    identity_mode = "authenticated" if user is not None else "guest"
    student_id = user.id if user is not None else "student_default"

    modules: list[ModuleCapability] = []
    disabled_reasons: dict[str, str] = {}
    for module in catalog.load_product_catalog().modules:
        available = True
        reason: str | None = None
        if module.route_id.value == "admin":
            if user is None or user.role != "admin":
                available, reason = False, "需要管理员账号"
        elif module.route_id.value == "course":
            available, reason = _classroom_open_for(student_id)
        if not available and reason:
            disabled_reasons[module.route_id.value] = reason
        modules.append(ModuleCapability(
            route_id=module.route_id, available=available, disabled_reason=reason))

    return AssistantCapabilities(
        enabled=enabled,
        catalog_version=catalog.load_product_catalog().catalog_version,
        identity_mode=identity_mode,
        conversation_enabled=enabled and model_available() and user is not None,
        model_available=model_available(),
        modules=modules,
        tools=list(IMPLEMENTED_TOOLS) if enabled else [],
        action_kinds=[k for k in IMPLEMENTED_ACTION_KINDS
                      if (k != "domain_write" or actions_enabled())
                      and (k != "start_workflow" or workflows_enabled())
                      and (k != "manage_subscription"
                           or proactive_enabled())]
            if enabled else [],
        disabled_reasons=disabled_reasons,
    )
