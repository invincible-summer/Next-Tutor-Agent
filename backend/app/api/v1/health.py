from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok", "model": settings.llm_model, "version": "0.2.0"}


@router.get("/ready")
def ready():
    """Readiness（plan.md §18）：bootstrap 维护步骤的结果视图。

    职责与 /health（liveness，恒 200）分离：
      ready     全部关键步骤正常（非关键 degraded 仍服务）-> 200
      degraded  非关键步骤失败（可降级功能受损，站点仍可用）-> 200
      not_ready critical 步骤失败 -> 503
    """
    from app.core.bootstrap import get_bootstrap_report
    report = get_bootstrap_report()
    body = {"status": report.status_label(),
            "checks": report.to_dict()["checks"]}
    # R01：worker 状态（评价功能停用时明确 disabled，不伪装就绪）
    try:
        from app.core import learner_runtime
        if learner_runtime.evaluation_enabled():
            from app.agents.student_model.evaluation.worker import (
                get_evaluation_worker)
            body["evaluation_worker"] = get_evaluation_worker().status()
        else:
            body["evaluation_worker"] = {"running": False, "disabled": True}
    except Exception:
        body["evaluation_worker"] = {"running": False, "error": True}
    if not report.ready:
        return JSONResponse(body, status_code=503)
    return body


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
