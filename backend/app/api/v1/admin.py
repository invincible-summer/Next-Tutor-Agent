"""Admin API (P6-B4)：账号总览/占用统计、聊天数据清理与账号彻底删除，仅 role=admin 可用。

- `GET /admin/users`：全部账号（公开字段，绝不含 password_hash）+ 每账号
  存储占用分桶与汇总。
- `POST /admin/users/{id}/clear-chat`：清理账号的聊天侧数据。scope="all"
  连会话一起删；scope="uploads_only" 只删上传文件、保留会话文本。
- `DELETE /admin/users/{id}`：彻底删除账号——账号记录 + 名下全部数据
  （聊天/上传/笔记/学习档案/知识图谱/回收站）不可恢复地清除；不能删除
  管理员账号（含自己）。
- `GET /admin/orphan-data` / `POST /admin/orphan-data/purge`：扫描/清理
  孤儿数据（不在册 owner 的测试残留与注销遗物、无引用 trace、失会话转写）。
  注册账号、public / student_default 共享命名空间与全局策略文件受保护。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import account_data, orphan_cleanup
from app.identity import store as id_store
from app.identity.deps import require_admin
from app.identity.models import User

router = APIRouter(prefix="/admin", tags=["admin"])


class RetentionPolicyRequest(BaseModel):
    default_days: int = Field(..., ge=1, le=30)
    user_max_days: int = Field(..., ge=1, le=30)
    forced_max_days: int = Field(..., ge=1, le=365)
    mode: str = Field("auto", pattern="^(auto|manual)$")
    cleanup_interval_seconds: int = Field(3600, ge=300, le=86400)


@router.get("/data-retention")
def get_retention_policy(admin: User = Depends(require_admin)) -> dict:
    from app.core.trash import get_global_policy
    return get_global_policy()


@router.put("/data-retention")
def update_retention_policy(req: RetentionPolicyRequest,
                            admin: User = Depends(require_admin)) -> dict:
    from app.core.trash import set_global_policy
    return set_global_policy(**req.model_dump())


@router.get("/public-trash")
def list_public_trash(admin: User = Depends(require_admin)) -> dict:
    from app.core.trash import list_items
    from app.core.textbook import PUBLIC_STUDENT_ID
    return {"items": list_items(PUBLIC_STUDENT_ID)}


@router.post("/public-trash/{item_id}/restore")
async def restore_public_trash(item_id: str, admin: User = Depends(require_admin)) -> dict:
    from app.core.trash import restore_item
    from app.core.textbook import PUBLIC_STUDENT_ID
    try:
        return restore_item(PUBLIC_STUDENT_ID, item_id)
    except FileNotFoundError:
        raise HTTPException(404, "公共库归档不存在")
    except FileExistsError as exc:
        raise HTTPException(409, str(exc))


@router.delete("/public-trash/{item_id}")
def purge_public_trash(item_id: str, admin: User = Depends(require_admin)) -> dict:
    from app.core.trash import purge_item
    from app.core.textbook import PUBLIC_STUDENT_ID
    try:
        return purge_item(PUBLIC_STUDENT_ID, item_id)
    except FileNotFoundError:
        raise HTTPException(404, "公共库归档不存在")


@router.get("/users")
def list_users(admin: User = Depends(require_admin)) -> dict:
    """账号列表（id/email/username/role/创建与最近登录时间 + 存储占用）。"""
    users = id_store.list_users()
    storage = account_data.scan_storage([u.id for u in users])
    payload = []
    for u in users:
        d = u.to_public_dict()
        d["storage"] = storage.get(u.id) or {"total_bytes": 0}
        payload.append(d)
    return {
        "users": payload,
        "summary": {
            "count": len(payload),
            "total_bytes": sum(int(u["storage"].get("total_bytes", 0)) for u in payload),
        },
    }


class ClearChatRequest(BaseModel):
    scope: str = Field("all", pattern="^(all|uploads_only)$")


@router.post("/users/{user_id}/clear-chat")
def clear_user_chat(user_id: str, req: ClearChatRequest,
                    admin: User = Depends(require_admin)) -> dict:
    """清理账号的聊天侧数据（all=连会话一起删；uploads_only=仅上传文件）。
    账号本身与其余数据（笔记/学习档案/图谱）保留。"""
    target = id_store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")
    report = account_data.clear_chat_data(user_id, req.scope)
    return {"status": "cleared", "user_id": user_id, "report": report}


@router.delete("/users/{user_id}")
def delete_user_admin(user_id: str, admin: User = Depends(require_admin)) -> dict:
    """彻底删除账号及名下全部数据，不可恢复。管理员账号（含自己）不可删。"""
    target = id_store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")
    if target.role == "admin":
        raise HTTPException(400, "不能删除管理员账号")
    report = account_data.purge_account(user_id)
    return {"status": "purged", "user_id": user_id, "report": report}


class OrphanPurgeRequest(BaseModel):
    dry_run: bool = False
    categories: list[str] | None = Field(
        None, description="只清理这些类别（orphan_cleanup.CATEGORIES 子集）；缺省=全部")


def _protected_ids() -> set[str]:
    return {u.id for u in id_store.list_users()}


@router.get("/orphan-data")
def scan_orphan_data(admin: User = Depends(require_admin)) -> dict:
    """扫描孤儿数据（测试残留/注销遗物/无引用 trace 等），只读无副作用。"""
    return {"status": "ok", "report": orphan_cleanup.scan_orphans(_protected_ids())}


@router.post("/orphan-data/purge")
def purge_orphan_data(req: OrphanPurgeRequest,
                      admin: User = Depends(require_admin)) -> dict:
    """清理孤儿数据。dry_run=True 只统计；注册账号与共享命名空间受保护。"""
    try:
        report = orphan_cleanup.purge_orphans(
            _protected_ids(), categories=req.categories, dry_run=req.dry_run)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"status": report["status"], "report": report}


class PromptMemoryPolicyRequest(BaseModel):
    default_window: int = Field(..., ge=5, le=30)
    max_window: int = Field(..., ge=5, le=30)
    core_char_limit: int = Field(1800, ge=400, le=5000)
    directive_char_limit: int = Field(2600, ge=600, le=6000)


class OCRPolicyRequest(BaseModel):
    concurrency: int = Field(..., ge=1, le=100)
    failure_mode: str = Field("persistent_api", pattern="^(persistent_api|bounded_then_local|bounded_api_only)$")
    max_attempts: int = Field(3, ge=1, le=100)
    retry_interval_seconds: int = Field(60, ge=0, le=3600)
    request_timeout_seconds: int = Field(60, ge=10, le=300)


@router.get("/ocr-policy")
def get_ocr_policy(admin: User = Depends(require_admin)) -> dict:
    from app.core.ocr_policy import get_policy
    return get_policy()


@router.put("/ocr-policy")
async def update_ocr_policy(req: OCRPolicyRequest,
                            admin: User = Depends(require_admin)) -> dict:
    from app.core.ocr_policy import set_policy
    return await set_policy(**req.model_dump())


class TextbookPipelineRequest(BaseModel):
    mode: str = Field("parallel", pattern="^(parallel|legacy)$")
    build_concurrency: int = Field(2, ge=1, le=4)
    volume_concurrency: int = Field(2, ge=1, le=4)
    llm_concurrency: int = Field(4, ge=1, le=8)


@router.get("/textbook-pipeline")
def get_textbook_pipeline_policy(admin: User = Depends(require_admin)) -> dict:
    """教材解析调度策略（只改执行并发，不改解析方式/产出）。"""
    from app.core.textbook_pipeline import get_policy
    return get_policy()


@router.put("/textbook-pipeline")
async def update_textbook_pipeline_policy(
        req: TextbookPipelineRequest,
        admin: User = Depends(require_admin)) -> dict:
    from app.core.textbook_pipeline import set_policy
    try:
        return await set_policy(**req.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/prompt-memory-policy")
def get_prompt_memory_policy(admin: User = Depends(require_admin)) -> dict:
    from app.agents.memory.prompt_memory import get_policy
    return get_policy()


@router.put("/prompt-memory-policy")
def update_prompt_memory_policy(req: PromptMemoryPolicyRequest,
                                admin: User = Depends(require_admin)) -> dict:
    from app.agents.memory.prompt_memory import set_policy
    return set_policy(**req.model_dump())
