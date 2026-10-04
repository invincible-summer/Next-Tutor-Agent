"""Resolve selected references through the same catalog/owner boundary as retrieval."""
from __future__ import annotations

from app.diagrams.catalog import catalog, digest
from app.diagrams.guidance import for_asset
from app.diagrams.materials import MaterialError, card as material_card, detail, owner_context
from app.diagrams.semantics import asset_card, catalog_version, METADATA_VERSION, RECIPES
from .contracts import CandidateBundleV2, CandidateNeed, IllustrationError
from .preview import material_drawing
from .v3_contracts import CandidateBundleV3, CandidateMaterialV3, CandidateNeedV3
from .v3_retrieval import MAX_SOURCE_BYTES, _similarity


class ReferenceError(ValueError):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


def selected_cards(owner, selections):
    cards = []
    with owner_context(owner):
        library = catalog()[1]
        for selected in selections:
            aid = selected.asset_id
            try:
                if aid.startswith("material."):
                    row = detail(owner, aid.removeprefix("material."), selected.version, enabled_only=True)
                    value = material_card(row)
                else:
                    asset = library.get(aid)
                    if asset is None and aid not in RECIPES:
                        raise ReferenceError("illustration_material_missing", 404)
                    if asset is not None and asset.review.get("status") != "passed":
                        raise ReferenceError("illustration_material_missing", 404)
                    if aid in RECIPES and any(library[child].review.get("status") != "passed"
                            for _, child, *_ in RECIPES[aid].children):
                        raise ReferenceError("illustration_material_missing", 404)
                    value = asset_card(aid)
                    if selected.version != value["version"]:
                        raise ReferenceError("illustration_material_version_conflict", 409)
                cards.append(value)
            except MaterialError:
                raise ReferenceError("illustration_material_missing", 404) from None
    return cards


def selected_bundle_v2(brief, cards):
    needs, assets = [], {}
    for need in brief.needs:
        compatible = [row for row in cards if set(need.capabilities) <= set(row["capabilities"])
                      and brief.view in row["supported_views"]]
        qualified = sorted(compatible, key=lambda row: -max(
            _similarity(need.name, term) for term in [row["title"], *row.get("aliases", []), row["asset_id"]]))[:6]
        if not qualified and need.priority == "required":
            raise IllustrationError("candidate_capability_mismatch")
        ids = [row["asset_id"] for row in qualified]
        needs.append(CandidateNeed(need_id=need.need_id, matched=bool(ids), candidate_ids=ids,
                                   missing_capabilities=[] if ids else need.capabilities))
        for row in qualified:
            aid = row["asset_id"]
            if aid not in assets:
                assets[aid] = {**row, "allowed_for_needs": []}
            assets[aid]["allowed_for_needs"].append(need.need_id)
    return CandidateBundleV2(catalog_version=catalog_version(), metadata_version=METADATA_VERSION,
        needs=needs, assets=list(assets.values()), retrieval_trace={"retrieval_algorithm": "user_selected",
            "selected_reference_count": len(cards), "candidate_limit_per_need": 6})


def selected_bundle_v3(needs, cards):
    sources, byte_count = [], 0
    for row in cards:
        svg = material_drawing(row).svg()
        byte_count += len(svg.encode("utf-8"))
        if byte_count > MAX_SOURCE_BYTES:
            raise ReferenceError("illustration_material_budget_exceeded")
        guide = for_asset(row["asset_id"], row["version"])
        sources.append(CandidateMaterialV3(asset_id=row["asset_id"], version=row["version"],
            title=row["title"], svg=svg, source_hash=digest(svg), guidance_version=guide["version"],
            usage_guidance=guide.get("hints", {}).get("compose", ""),
            review_guidance=guide.get("hints", {}).get("review", ""),
            allowed_for_needs=[need.need_id for need in needs]))
    # Selected SVG references are offered directly, independent of name ranking.
    return CandidateBundleV3(catalog_version=catalog_version(), assets=sources,
        needs=[CandidateNeedV3(need_id=need.need_id,
            candidate_ids=[row.asset_id for row in sources[:3]]) for need in needs],
        retrieval_trace={"retrieval_algorithm": "user_selected", "reference_count": len(sources),
                         "full_svg_bytes": byte_count, "source_byte_limit": MAX_SOURCE_BYTES})
