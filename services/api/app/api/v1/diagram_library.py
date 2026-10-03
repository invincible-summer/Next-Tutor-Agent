"""Read-only public project art, behind the workspace's existing access gate."""

from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.diagrams.catalog import catalog, search
from app.diagrams.compiler import preview_asset
from app.diagrams.schema import DiagramError
from app.diagrams.taxonomy import public_taxonomy

router = APIRouter(prefix="/diagram-assets", tags=["diagram-library"])


@lru_cache(maxsize=2048)
def _thumbnail(asset_id: str) -> dict:
    return preview_asset(asset_id).model_dump(mode="json")


def _metadata(asset) -> dict:
    from app.diagrams.semantics import COMPONENTS, asset_card
    return {
        "id": asset.id,
        "title": asset.title,
        "english": asset.english,
        "category": asset.category,
        "family": asset.renderer,
        "aliases": list(asset.aliases),
        "features": list(asset.features),
        "version": asset.version,
        "license": asset.license,
        "subjects": asset.subjects,
        "education_levels": asset.education_levels,
        "asset_kind": asset.asset_kind,
        "topics": asset.topics,
        "provenance": asset.provenance,
        "review": asset.review,
        "v2": asset_card(asset.id) if asset.review.get("status") == "passed" else None,
    }


def _published(asset):
    return asset is not None and asset.review.get("status") == "passed"


@router.get("")
def list_assets(
    q: str = Query(default="", max_length=100),
    category: str = Query(default="", max_length=40),
    family: str = Query(default="", max_length=40),
    subject: str = Query(default="", max_length=40),
    education_level: str = Query(default="", max_length=40),
    asset_kind: str = Query(default="", max_length=40),
    page: int = Query(default=0, ge=0, le=1000),
    per: int = Query(default=12, ge=1, le=48),
):
    version, assets = catalog()
    assets = {key: value for key, value in assets.items() if _published(value)}
    categories, families, subjects = {}, {}, {}
    for asset in assets.values():
        categories[asset.category] = categories.get(asset.category, 0) + 1
        families[asset.renderer] = families.get(asset.renderer, 0) + 1
        for key in asset.subjects:
            subjects[key] = subjects.get(key, 0) + 1
    # The browser's full catalogue search shares the local lexical ranking;
    # agent candidate retrieval remains capped at three per declared need.
    rows = (
        search(q, top_k=len(assets), gallery=True)
        if q.strip()
        else list(assets.values())
    )
    rows = [a for a in rows if a.id in assets]
    if not q.strip():
        featured = [
            "vessel.beaker",
            "apparatus.alcohol_lamp",
            "mechanics.pendulum",
            "chart.pie",
            "geometry.prism",
            "biology.microscope",
            "template.heating_beaker",
            "geometry.sphere",
            "measurement.vernier",
            "function.quadratic",
            "chemistry.water",
            "earth.globe",
        ]
        priority = {asset_id: i for i, asset_id in enumerate(featured)}
        rows.sort(key=lambda a: priority.get(a.id, len(featured)))
    rows = [
        a
        for a in rows
        if (not category or a.category == category)
        and (not family or a.renderer == family)
        and (not subject or subject in a.subjects)
        and (not education_level or education_level in a.education_levels)
        and (not asset_kind or asset_kind == a.asset_kind)
    ]
    return {
        "catalog_version": version,
        "catalog_total": len(assets),
        "categories": categories,
        "families": families,
        "subjects": subjects,
        "total": len(rows),
        "page": page,
        "per": per,
        "items": [
            {**_metadata(a), "illustration": _thumbnail(a.id)}
            for a in rows[page * per : (page + 1) * per]
        ],
    }


@router.get("/taxonomy")
def taxonomy():
    return public_taxonomy()


@router.get("/{asset_id}")
def asset_detail(asset_id: str):
    asset = catalog()[1].get(asset_id)
    if not _published(asset):
        raise HTTPException(404, "diagram_asset_missing")
    return {
        **_metadata(asset),
        **asset.card(),
        "sample_params": asset.sample_params,
        "illustration": _thumbnail(asset_id),
    }


class PreviewRequest(BaseModel):
    model_config = {"extra": "forbid"}
    params: dict = Field(default_factory=dict, max_length=24)
    profile: Literal["textbook", "monochrome"] = "textbook"


@router.post("/{asset_id}/preview")
def preview(asset_id: str, body: PreviewRequest):
    if not _published(catalog()[1].get(asset_id)):
        raise HTTPException(404, "diagram_asset_missing")
    try:
        return {
            "illustration": preview_asset(
                asset_id, body.params, body.profile
            ).model_dump(mode="json")
        }
    except (
        DiagramError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OverflowError,
        ZeroDivisionError,
    ) as exc:
        raise HTTPException(
            422, getattr(exc, "code", "diagram_invalid_parameter")
        ) from None
