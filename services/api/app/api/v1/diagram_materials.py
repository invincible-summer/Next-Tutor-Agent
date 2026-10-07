"""Owned SVG draft creation, editing and administrator public publication."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from typing import Literal

from app.identity.deps import require_user, resolve_student_id
from app.identity.models import User
from app.diagrams import materials as store
from app.diagrams.catalog import catalog, is_subsequence
from app.diagrams.compiler import preview_asset
from app.diagrams.material_templates import TEMPLATES

router = APIRouter(prefix="/diagram-materials", tags=["diagram-library"], dependencies=[Depends(require_user)])


def _public(value):
    return {key: item for key, item in value.items() if key != "namespace"}


def _invoke(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except store.MaterialError as exc:
        raise HTTPException(exc.status, exc.code) from None


def _require_admin_user(user: User) -> None:
    if user.role != "admin":
        raise HTTPException(403, "admin_required")


def _fuzzy_match(query: str, row: dict) -> bool:
    """Token AND match: each token is a substring of any field, or a
    subsequence of a title/alias (CJK-friendly fuzzy, e.g. 烧被→烧杯)."""
    names = [row["title"], *row["aliases"]]
    haystack = " ".join([row["title"], row["description"], *row["aliases"]]).casefold()
    return all(
        token in haystack
        or any(len(token) >= 2 and is_subsequence(token, name.casefold().replace(" ", ""))
               for name in names if name)
        for token in query.split()
    )


@router.get("")
def listing(scope: Literal["private", "public"] = "private", q: str = Query("", max_length=100),
        page: int = Query(0, ge=0, le=1000), per: int = Query(12, ge=1, le=48),
        subject: str = Query("", max_length=40), enabled_only: bool = False,
        owner: str = Depends(resolve_student_id)):
    rows = store.visible(owner, scope=scope, enabled_only=enabled_only)
    if subject:
        rows = [row for row in rows if row.get("subject") == subject]
    if q.strip():
        rows = [row for row in rows if _fuzzy_match(q.strip().casefold(), row)]
    rows.sort(key=lambda row: row["updated_at"], reverse=True)
    return {"total": len(rows), "page": page, "per": per,
        "items": [_public(store.detail(owner, row["id"])) for row in rows[page*per:(page+1)*per]]}


@router.get("/templates")
def templates():
    return {"templates": TEMPLATES, "guide": ["viewBox 定义画布；x/y 是坐标；width/height 是尺寸。",
        "circle 用 cx/cy/r；line 用 x1/y1/x2/y2；text 的内容可直接改写。",
        "路径 points/d 可在源码中修改。使用纯SVG，不含脚本、外部图片或HTML。",
        "保存前检查位置、标签和题目含义，启用后素材可以进入当前账户的出题候选。",
        "可调参数由参数规范声明：文字用text，示意尺寸用schematic，实际条件用quantity，外观用appearance。",
        "绑定通过图元id定位，可用同一个参数联动多个图元；模板提供完整样例。",
        "预览值用于检查调整效果，不会修改规范的默认值；出题使用题面事实。"]}


@router.get("/catalog")
def catalog_listing(
    q: str = Query("", max_length=100),
    subject: str = Query("", max_length=40),
    family: str = Query("", max_length=40),
    education_level: str = Query("", max_length=40),
    asset_kind: str = Query("", max_length=40),
    page: int = Query(0, ge=0, le=1000),
    per: int = Query(12, ge=1, le=48),
    owner: str = Depends(resolve_student_id),
):
    """One discoverable public catalogue for built-in and published SVG art.

    The built-in catalogue remains immutable and the custom namespace remains
    admin-owned.  The endpoint only joins their read models, so existing
    material revision and permission contracts stay unchanged.
    """
    version, assets = catalog()
    builtins = [asset for asset in assets.values() if asset.review.get("status") == "passed"]
    # The joined public catalogue is a publication surface; disabled drafts
    # stay in the administrator's editor and never enter discovery results.
    custom = store.visible(owner, scope="public", enabled_only=True)
    needle = q.strip().casefold()
    tokens = needle.split()
    builtin_rows: list[dict] = []
    for asset in builtins:
        haystack = " ".join([asset.title, asset.english, *asset.aliases, *asset.features]).casefold()
        names = [asset.title, asset.english, *asset.aliases]
        if tokens and not all(
            token in haystack or any(
                len(token) >= 2 and is_subsequence(token, name.casefold().replace(" ", ""))
                for name in names
            ) for token in tokens
        ):
            continue
        if subject and subject not in asset.subjects:
            continue
        if family and family != asset.renderer:
            continue
        if education_level and education_level not in asset.education_levels:
            continue
        if asset_kind and asset_kind != asset.asset_kind:
            continue
        builtin_rows.append({
            "id": asset.id,
            "asset_id": asset.id,
            "source": "builtin",
            "title": asset.title,
            "english": asset.english,
            "description": " · ".join(asset.features),
            "aliases": list(asset.aliases),
            "subject": asset.subjects[0] if asset.subjects else "general",
            "subjects": list(asset.subjects),
            "family": asset.renderer,
            "category": asset.category,
            "education_levels": list(asset.education_levels),
            "asset_kind": asset.asset_kind,
            "version": asset.version,
            "license": asset.license,
        })
    custom_rows: list[dict] = []
    for row in custom:
        if subject and row.get("subject") != subject:
            continue
        if family and family != "custom":
            continue
        if education_level:
            continue
        if asset_kind and asset_kind != "custom":
            continue
        if needle and not _fuzzy_match(needle, row):
            continue
        custom_rows.append({
            "id": row["id"],
            "asset_id": "material." + row["id"],
            "source": "public",
            "title": row["title"],
            "english": row["title"],
            "description": row.get("description", ""),
            "aliases": row.get("aliases", []),
            "subject": row.get("subject", "general"),
            "subjects": [row.get("subject", "general")],
            "family": "custom",
            "category": "custom",
            "education_levels": [],
            "asset_kind": "custom",
            "version": row.get("revision", 1),
            "license": "workspace-public",
            "updated_at": row.get("updated_at", 0),
        })
    featured = [
        "vessel.beaker", "apparatus.alcohol_lamp", "mechanics.pendulum", "chart.pie",
        "geometry.prism", "biology.microscope", "template.heating_beaker", "geometry.sphere",
        "measurement.vernier", "function.quadratic", "chemistry.water", "earth.globe",
    ]
    priority = {asset_id: index for index, asset_id in enumerate(featured)}
    builtin_rows.sort(key=lambda row: (priority.get(row["id"], len(featured)), row["title"]))
    custom_rows.sort(key=lambda row: (-row["updated_at"], row["title"]))
    # Keep a few familiar project marks at the top while surfacing published
    # workspace material on the first page; a public card should never be
    # hidden behind 1,119 immutable catalogue entries.
    rows = builtin_rows[:4] + custom_rows + builtin_rows[4:]
    start, end = page * per, (page + 1) * per
    items = []
    for row in rows[start:end]:
        if row["source"] == "builtin":
            row["illustration"] = preview_asset(row["id"]).model_dump(mode="json")
        else:
            row["illustration"] = store.detail(owner, row["id"])["illustration"]
        items.append(row)
    return {
        "catalog_version": version,
        "total": len(rows),
        "page": page,
        "per": per,
        "source_counts": {"builtin": len(builtin_rows), "public": len(custom_rows)},
        "items": items,
    }


class SvgInput(BaseModel):
    model_config = {"extra": "forbid"}
    svg: str = Field(min_length=1, max_length=131072)
    parameterization: store.SvgParameterization = Field(default_factory=store.SvgParameterization)
    params: dict = Field(default_factory=dict)


@router.post("/preview")
def preview(body: SvgInput):
    image, _png = _invoke(store.validate_preview, body.svg, parameterization=body.parameterization, params=body.params)
    from app.core.quiz_illustration import normalize_svg
    template = normalize_svg(body.svg, components=True, preserve_presentation=True).svg
    return {"svg": template, "parameterization": body.parameterization.model_dump(mode="json", exclude_none=True), "illustration": image.model_dump(mode="json"), "status": "previewed"}


class GenerateInput(BaseModel):
    model_config = {"extra": "forbid"}
    requirement: str = Field(min_length=3, max_length=2400)
    current_svg: str = Field(default="", max_length=131072)
    current_parameterization: store.SvgParameterization = Field(default_factory=store.SvgParameterization)


@router.post("/generate")
async def generate(body: GenerateInput):
    try:
        return await store.generate_draft(body.requirement, body.current_svg, parameterization=body.current_parameterization)
    except store.MaterialError as exc:
        raise HTTPException(exc.status, exc.code) from None
    except TimeoutError:
        raise HTTPException(504, "material_generation_timeout") from None
    except Exception:
        raise HTTPException(503, "material_generation_unavailable") from None


@router.post("")
def create(body: store.MaterialInput, owner: str = Depends(resolve_student_id), user: User = Depends(require_user)):
    if body.scope == "public":
        _require_admin_user(user)
    return _public(_invoke(store.save, owner, body, admin=user.role == "admin"))


@router.get("/{asset_id}")
def detail(asset_id: str, revision: int | None = Query(None, ge=1, le=999), owner: str = Depends(resolve_student_id)):
    value = _invoke(store.detail, owner, asset_id, revision)
    current = _invoke(store.detail, owner, asset_id)
    return {**_public(value), "latest_revision": current["revision"], "revisions": list(range(1, current["revision"]+1))}


@router.put("/{asset_id}")
def update(asset_id: str, body: store.MaterialInput, owner: str = Depends(resolve_student_id), user: User = Depends(require_user)):
    if body.scope == "public":
        _require_admin_user(user)
    return _public(_invoke(store.save, owner, body, admin=user.role == "admin", asset_id=asset_id))


@router.delete("/{asset_id}")
def delete(asset_id: str, base_revision: int = Query(..., ge=1, le=999), owner: str = Depends(resolve_student_id), user: User = Depends(require_user)):
    value = _invoke(store.detail, owner, asset_id)
    if value["scope"] == "public":
        _require_admin_user(user)
    _invoke(store.delete, owner, asset_id, admin=user.role == "admin", base_revision=base_revision)
    return {"deleted": True}


@router.get("/{asset_id}/preview.png")
def png(asset_id: str, revision: int | None = Query(None, ge=1, le=999), owner: str = Depends(resolve_student_id)):
    value = _invoke(store.detail, owner, asset_id, revision)
    path = store.preview_path(value)
    if not path.is_file():
        raise HTTPException(404, "material_preview_missing")
    return Response(path.read_bytes(), media_type="image/png", headers={"Cache-Control": "private, no-store"})
