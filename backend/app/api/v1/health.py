from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok", "model": settings.llm_model, "version": "0.2.0"}


@router.get("/model-info")
def model_info():
    """Return non-sensitive model configuration info.

    Only model *names* and a boolean for multimodal availability are exposed.
    API keys are NEVER returned. The frontend uses this to show the user
    which model is active and whether image OCR falls back to local tesseract.
    """
    return {
        "llm_model": settings.llm_model,
        "multimodal_configured": bool(settings.multimodal_api_key),
        "multimodal_model": settings.multimodal_model or "",
    }
