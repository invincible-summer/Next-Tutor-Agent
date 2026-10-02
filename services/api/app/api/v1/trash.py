"""Unified recycle-bin API for private user resources."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core import trash
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/trash", tags=["trash"])


class RestoreRequest(BaseModel):
    workspace_ids: list[str] = Field(default_factory=list, max_length=100)


class RetentionRequest(BaseModel):
    retention_days: int = Field(..., ge=1, le=30)


@router.get("")
def list_trash(resource_type: str = Query(""),
               student_id: str = Depends(resolve_student_id)):
    return {"status": "ok", "items": trash.list_items(student_id, resource_type)}


@router.get("/policy")
def policy(student_id: str = Depends(resolve_student_id)):
    return trash.get_user_policy(student_id)


@router.put("/policy")
def update_policy(req: RetentionRequest,
                  student_id: str = Depends(resolve_student_id)):
    return trash.set_user_policy(student_id, req.retention_days)


@router.get("/{item_id}")
def trash_detail(item_id: str, student_id: str = Depends(resolve_student_id)):
    item = trash.get_item(student_id, item_id)
    if item is None:
        raise HTTPException(404, "归档不存在")
    return {"status": "ok", "item": trash._public_manifest(
        item, trash._item_dir(student_id, item_id))}


@router.post("/{item_id}/restore")
async def restore(item_id: str, req: RestoreRequest,
            student_id: str = Depends(resolve_student_id)):
    try:
        return trash.restore_item(student_id, item_id, workspace_ids=req.workspace_ids)
    except FileNotFoundError:
        raise HTTPException(404, "归档不存在")
    except FileExistsError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/{item_id}")
def purge(item_id: str, student_id: str = Depends(resolve_student_id)):
    try:
        return trash.purge_item(student_id, item_id)
    except FileNotFoundError:
        raise HTTPException(404, "归档不存在")


@router.delete("")
def empty(student_id: str = Depends(resolve_student_id)):
    return trash.empty_trash(student_id)
