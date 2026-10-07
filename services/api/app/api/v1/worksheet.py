"""Cross-device teacher worksheet authoring API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.identity.deps import require_user, resolve_student_id
from app.schemas.worksheet import (
    WorksheetCreateRequest, WorksheetDocument, WorksheetExport,
    WorksheetGenerateRequest, WorksheetGenerateResponse,
    WorksheetImageAttachRequest, WorksheetList, WorksheetPatchRequest,
    WorksheetQuestionPatchRequest, WorksheetRefineRequest,
)
from app.worksheets import service, storage

router = APIRouter(prefix="/tools/worksheets", tags=["tool-assistant"], dependencies=[Depends(require_user)])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.WorksheetError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from None


@router.get("", response_model=WorksheetList)
def list_worksheets(owner: str = Depends(resolve_student_id)):
    return service.list_documents(owner)


@router.post("", response_model=WorksheetDocument)
def create_worksheet(body: WorksheetCreateRequest, owner: str = Depends(resolve_student_id)):
    return _call(service.create, owner, body)


@router.get("/{worksheet_id}", response_model=WorksheetDocument)
def get_worksheet(worksheet_id: str, owner: str = Depends(resolve_student_id)):
    return _call(service.get, owner, worksheet_id)


@router.patch("/{worksheet_id}", response_model=WorksheetDocument)
def patch_worksheet(worksheet_id: str, body: WorksheetPatchRequest, owner: str = Depends(resolve_student_id)):
    return _call(service.patch, owner, worksheet_id, body)


@router.delete("/{worksheet_id}")
def delete_worksheet(worksheet_id: str, owner: str = Depends(resolve_student_id)):
    return _call(service.delete, owner, worksheet_id)


@router.post("/{worksheet_id}/generate", response_model=WorksheetGenerateResponse)
async def generate_worksheet(worksheet_id: str, body: WorksheetGenerateRequest, owner: str = Depends(resolve_student_id)):
    try:
        return await service.generate(owner, worksheet_id, body)
    except service.WorksheetError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from None


@router.patch("/{worksheet_id}/questions/{question_id}", response_model=WorksheetDocument)
def patch_question(worksheet_id: str, question_id: str, body: WorksheetQuestionPatchRequest, owner: str = Depends(resolve_student_id)):
    return _call(service.patch_question, owner, worksheet_id, question_id, body)


@router.delete("/{worksheet_id}/questions/{question_id}", response_model=WorksheetDocument)
def delete_question(worksheet_id: str, question_id: str, etag: str = Query(..., min_length=1), owner: str = Depends(resolve_student_id)):
    return _call(service.delete_question, owner, worksheet_id, question_id, etag)


@router.post("/{worksheet_id}/questions/{question_id}/refine", response_model=WorksheetDocument)
async def refine_question(worksheet_id: str, question_id: str, body: WorksheetRefineRequest, owner: str = Depends(resolve_student_id)):
    try:
        return await service.refine_question(owner, worksheet_id, question_id, body)
    except service.WorksheetError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from None


@router.post("/{worksheet_id}/questions/{question_id}/attach-image", response_model=WorksheetDocument)
def attach_image(worksheet_id: str, question_id: str, body: WorksheetImageAttachRequest, owner: str = Depends(resolve_student_id)):
    return _call(service.attach_image, owner, worksheet_id, question_id, data_url=body.data_url, alt=body.alt, etag=body.etag)


@router.get("/{worksheet_id}/assets/{asset_id}")
def worksheet_asset(worksheet_id: str, asset_id: str, owner: str = Depends(resolve_student_id)):
    try:
        path = storage.asset_path(owner, worksheet_id, asset_id)
    except ValueError:
        raise HTTPException(404, detail={"code": "worksheet_image_missing"}) from None
    if not path.is_file():
        raise HTTPException(404, detail={"code": "worksheet_image_missing"})
    suffix = path.suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix, "application/octet-stream")
    return Response(path.read_bytes(), media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{worksheet_id}/export", response_model=WorksheetExport)
def export_worksheet(
    worksheet_id: str,
    variant: str = Query(default="student", pattern="^(student|teacher)$"),
    format: str = Query(default="markdown", pattern="^(markdown|html)$"),
    owner: str = Depends(resolve_student_id),
):
    return _call(service.export, owner, worksheet_id, variant, format)
