"""Guest capabilities are opaque, page-scoped, and never persisted."""
from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse, Response

from app.core import guest_learning, guest_runtime
from app.core.ratelimit import rate_limit
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/guest", tags=["guest"])


@router.post("/session", dependencies=[Depends(rate_limit("guest_session", 20))])
def create_session():
    context = guest_runtime.create_context()
    return JSONResponse({"token": context.token, "expires_in": guest_runtime.IDLE_SECONDS},
                        headers={"Cache-Control": "no-store"})


@router.delete("/session")
def delete_session(x_guest_token: str | None = Header(default=None)):
    guest_runtime.dispose(x_guest_token)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/textbooks")
def textbooks(student_id: str = Depends(resolve_student_id)):
    return {"items": guest_learning.public_textbooks()}


@router.post("/quiz/generate", dependencies=[Depends(rate_limit("guest_quiz", 20))])
async def generate_quiz(req: guest_learning.GenerateRequest,
                        student_id: str = Depends(resolve_student_id)):
    return await guest_learning.generate(guest_runtime.context_for_owner(student_id), req)
