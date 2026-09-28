"""Pydantic schemas for the chat API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ClassroomRef(BaseModel):
    """课堂插问引用（plan.md §12.4）：普通调用缺省 None，旧客户端兼容。

    workspace_id/lesson_id 是定位提示；run 始终在调用者 owner 根下加载，
    归属校验由服务端完成，外来 ID 只得到 404 语义。
    """
    run_id: str = Field(..., min_length=8, max_length=64)
    lesson_revision: int = Field(..., ge=1)
    slide_id: str = Field(..., min_length=4, max_length=32)
    segment_id: str | None = Field(None, min_length=8, max_length=64)
    workspace_id: str | None = Field(None, max_length=128)
    lesson_id: str | None = Field(None, min_length=8, max_length=64)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="学生输入")
    session_id: str | None = Field(None, description="已有会话 id；为空则新建")
    grade: str = Field("", description="学段: 空=自动/小学/初中/高中/本科")
    lang: str = Field("zh", description="UI 语言: zh|en；决定回答语言（覆盖提问语言）")
    output_language: str | None = Field(None, description="回答语言: None=自动(检测+外语学习豁免) | zh=强制中文 | en=强制英文")
    workspace_id: str | None = Field(None, description="工作学习区 id；新建对话在该学习区内时传入")
    attachments: list[dict] | None = Field(None, description="本条用户消息附带的文件元数据 [{id, filename, char_count, chunk_count}]，用于在对话流中展示")
    classroom_ref: ClassroomRef | None = Field(None, description="课堂插问：服务端验证 run/版本/页段并构造受边界材料区（§12.4）")


class ChatAttachment(BaseModel):
    id: str
    filename: str
    char_count: int = 0


class SessionItem(BaseModel):
    session_id: str
    grade: str = ""
    title: str = "未命名对话"
    message_count: int = 0
    round_count: int = 0  # 一次 agent 回复算一轮
    quiz_count: int = 0
    file_count: int = 0
    updated_at: float = 0


class SessionListResponse(BaseModel):
    sessions: list[SessionItem]


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=60)


class SessionPatchRequest(BaseModel):
    """PATCH /chat/sessions/{id} body: rename and/or switch stage (P1).

    Both fields optional; the route validates that at least one is present and
    that ``grade`` is "" (auto) or one of the four explicit stages.
    """
    title: str | None = Field(None, min_length=1, max_length=60)
    grade: str | None = Field(None, description="切换学段：空=自动 / 小学 / 初中 / 高中 / 本科")


class UploadResult(BaseModel):
    id: str = ""
    filename: str
    char_count: int = 0
    chunk_count: int = 0
    error: str | None = None
    warning: str | None = None
    ocr_used: bool = False
    preview_text: str | None = None
    source_scope: str | None = None
    source_visibility: str | None = None


class UploadResponse(BaseModel):
    results: list[UploadResult]
    session_id: str
