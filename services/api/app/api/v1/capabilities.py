"""产品能力聚合端点（客户端能力面单点，见 voice.md 聚合一节）。

客户端（Web/Mobile）从这里了解本部署可用功能面；每个能力返回
``{available, reason}``，不可用时 reason 是稳定 code 而非 UI 文案。判定
全部复用现有域内判断（``classroom.capabilities.user_allowed``、
``site_assistant.capabilities``、voice tts/stt service、LLM 配置），本
文件只做归一化聚合：零网络请求、零计费触发、零凭证回显。

不用单一 ``illustration=true`` 掩盖"题图可用但工具助手/某模式不可用"
的差异：illustration.quiz / illustration.scenario / illustration.v3 与
diagram.materials 各自独立成项。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.identity.deps import optional_user
from app.identity.models import User

router = APIRouter(tags=["capabilities"])


class CapabilityEntry(BaseModel):
    model_config = {"extra": "forbid"}

    available: bool
    reason: str = ""


class ProductCapabilities(BaseModel):
    model_config = {"extra": "forbid"}

    chat: CapabilityEntry
    upload: CapabilityEntry
    classroom: CapabilityEntry
    cloud_stt: CapabilityEntry
    cloud_tts: CapabilityEntry
    assistant: CapabilityEntry
    illustration_quiz: CapabilityEntry = Field(
        serialization_alias="illustration.quiz")
    illustration_scenario: CapabilityEntry = Field(
        serialization_alias="illustration.scenario")
    illustration_v3: CapabilityEntry = Field(
        serialization_alias="illustration.v3")
    diagram_materials: CapabilityEntry = Field(
        serialization_alias="diagram.materials")


def _llm_capability() -> CapabilityEntry:
    from app.core.config import settings
    if not settings.llm_api_key:
        return CapabilityEntry(available=False, reason="model_not_configured")
    return CapabilityEntry(available=True)


def _classroom_capability(student_id: str) -> CapabilityEntry:
    """复用 user_allowed 判定；reason 归一化为稳定 code。"""
    from app.classroom.capabilities import user_allowed
    from app.core.config import settings
    if not settings.classroom_enabled:
        return CapabilityEntry(available=False, reason="classroom_disabled")
    allowed, reason = user_allowed(student_id)
    if allowed:
        return CapabilityEntry(available=True)
    if "游客" in reason:
        return CapabilityEntry(available=False, reason="classroom_guest_denied")
    return CapabilityEntry(available=False, reason="classroom_not_permitted")


def _assistant_capability() -> CapabilityEntry:
    from app.agents.site_assistant import capabilities as caps
    if not caps.assistant_enabled():
        return CapabilityEntry(available=False, reason="assistant_disabled")
    if not caps.model_available():
        return CapabilityEntry(available=False, reason="model_not_configured")
    return CapabilityEntry(available=True)


@router.get("/capabilities", response_model=ProductCapabilities)
def get_product_capabilities(
        user: User | None = Depends(optional_user)) -> ProductCapabilities:
    """聚合产品能力（只读、无状态：登录/游客均可读，判定与域内一致）。"""
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    from app.voice.stt import service as stt_service
    from app.voice.tts import service as tts_service

    student_id = user.id if user is not None else DEFAULT_STUDENT_ID
    stt_ok, stt_reason = stt_service.stt_status()
    cloud_tts_ok = tts_service.cloud_available()
    llm = _llm_capability()
    return ProductCapabilities(
        chat=llm,
        upload=CapabilityEntry(available=True),
        classroom=_classroom_capability(student_id),
        cloud_stt=CapabilityEntry(available=stt_ok, reason=stt_reason),
        cloud_tts=CapabilityEntry(
            available=cloud_tts_ok,
            reason="" if cloud_tts_ok else "cloud_tts_not_configured"),
        assistant=_assistant_capability(),
        illustration_quiz=llm,
        illustration_scenario=llm,
        illustration_v3=llm,
        diagram_materials=llm,
    )
