"""Usage-docs API: the /docs page content (read for everyone, edit for admins).

GET /docs/content is intentionally public (no auth dependency): the usage doc
is product documentation, not user data, and guests should be able to read it
before logging in. PUT is admin-only — the same human gate as every other
global setting (ocr policy, library administration).
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core import usage_docs
from app.identity.deps import require_admin
from app.identity.store import User

router = APIRouter(prefix="/docs", tags=["docs"])

_PAGE_RE = re.compile(r"^p-\d{1,4}\.png$")


@router.get("/content")
def docs_content() -> dict:
    """The usage document (markdown + last-edit metadata). Public read."""
    data = usage_docs.read_docs()
    return {"status": "ok", "show_manual": usage_docs.show_manual_available(), **data}


@router.get("/show")
def docs_show_manual() -> FileResponse:
    """The rendered PDF manual shell (public read, same policy as /content)."""
    index = usage_docs.show_manual_dir() / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="show manual not built")
    return FileResponse(index, media_type="text/html")


@router.get("/show/pages/{page}")
def docs_show_manual_page(page: str) -> FileResponse:
    """One rasterised manual page. Fixed name pattern blocks path traversal."""
    if not _PAGE_RE.match(page):
        raise HTTPException(status_code=404, detail="no such page")
    png = usage_docs.show_manual_dir() / "pages" / page
    if not png.is_file():
        raise HTTPException(status_code=404, detail="no such page")
    return FileResponse(png, media_type="image/png")


class DocsContentBody(BaseModel):
    markdown: str


@router.put("/content")
def docs_update(body: DocsContentBody,
                admin: User = Depends(require_admin)) -> dict:
    """Replace the usage document (markdown). Admin only."""
    try:
        payload = usage_docs.write_docs(body.markdown, updated_by=admin.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError:
        raise HTTPException(status_code=500, detail="failed to persist docs")
    return {"status": "ok", **payload}
