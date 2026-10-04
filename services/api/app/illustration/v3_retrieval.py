"""Fuzzy, permission-aware SVG references; geometry interfaces are not filters."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.diagrams.catalog import _normalize, catalog, digest
from app.diagrams.guidance import for_asset, records
from app.diagrams.materials import MaterialError, current_owner, visible
from app.diagrams.semantics import RECIPES, asset_card, catalog_version

from .preview import material_drawing
from .v3_contracts import (CandidateBundleV3, CandidateMaterialV3, CandidateNeedV3,
                           MaterialNeedV3)

MAX_REFERENCES = 12
MAX_SOURCE_BYTES = 128 * 1024
PER_NEED = 3
_GENERIC = re.compile(r"示意图|素材|illustration|diagram|material", re.I)


def _tokens(value):
    value = _GENERIC.sub("", value)
    words = set(re.findall(r"[a-z][a-z0-9_-]+", value.lower()))
    for part in re.findall(r"[\u3400-\u9fff]+", value):
        words.update(part[i:i+2] for i in range(len(part)-1))
    return words


def _similarity(query, term):
    query, term = _normalize(_GENERIC.sub("", query)), _normalize(_GENERIC.sub("", term))
    if not query or not term:
        return 0.0
    if query == term:
        return 12.0
    if min(len(query), len(term)) >= 2 and (query in term or term in query):
        return 8.0 + min(len(query), len(term))/max(len(query), len(term))
    ratio = SequenceMatcher(None, query, term).ratio()
    overlap = _tokens(query) & _tokens(term)
    return 4*ratio + min(2.0, len(overlap)) if ratio >= .52 or overlap else 0.0


def _inventory():
    """Do not enumerate another owner's records or read arbitrary file names."""
    library = catalog()[1]
    guides = records()
    rows = []
    for asset in library.values():
        if asset.review.get("status") != "passed":
            continue
        guide = guides.get(asset.id, {})
        rows.append({"asset_id": asset.id, "version": asset.version,
            "title": asset.title, "names": [asset.title, asset.english, *asset.aliases],
            "features": [*asset.features, *asset.topics, *guide.get("match_terms", [])],
            "guidance": " ".join(guide.get("hints", {}).values()),
            "subjects": asset.subjects, "education_levels": asset.education_levels})
    for aid, recipe in RECIPES.items():
        if any(library[child].review.get("status") != "passed" for _, child, *_ in recipe.children):
            continue
        guide = guides.get(aid, {})
        rows.append({"asset_id": aid, "title": recipe.title,
            "names": [recipe.title, *recipe.aliases], "features": guide.get("match_terms", []),
            "guidance": " ".join(guide.get("hints", {}).values()),
            "subjects": [], "education_levels": []})
    for row in visible(current_owner(), enabled_only=True):
        # Saved materials have already passed strict sanitizer + real preview.
        if row.get("validation") != "previewed":
            continue
        rows.append({"asset_id": "material."+row["id"], "version": row["revision"],
            "title": row["title"], "names": [row["title"], *row.get("aliases", [])],
            "features": [row.get("description", "")], "guidance": row.get("guidance_note", ""),
            "subjects": [row.get("subject", "")], "education_levels": []})
    return rows


def _rank(need, rows, *, subject, education_level):
    queries = [need.name, *need.synonyms]
    ranked = []
    for row in rows:
        name_score = max((_similarity(query, term) for query in queries for term in row["names"]), default=0)
        feature_score = max((_similarity(query, term) for query in queries for term in row["features"]), default=0)*.65
        guidance_tokens = _tokens(row["guidance"])
        guide_score = min(2.0, len(set().union(*(_tokens(query) for query in queries)) & guidance_tokens)*.4)
        relevance = max(name_score, feature_score, guide_score)
        # Purpose adds ranking context, but cannot make an unrelated reference
        # win because its usage guide contains generic teaching vocabulary.
        if relevance < 1.2:
            continue
        score = relevance + min(1.0, len(_tokens(need.purpose) & (
            _tokens(" ".join(row["names"]+row["features"])) | guidance_tokens))*.1)
        if subject and any(_normalize(subject) == _normalize(item) for item in row["subjects"]):
            score += .25
        if education_level and education_level in row["education_levels"]:
            score += .15
        ranked.append((score, row))
    return [row for _, row in sorted(ranked, key=lambda item: (-item[0], item[1]["asset_id"]))]


def retrieve(needs: list[MaterialNeedV3], *, subject="", education_level="",
             existing: CandidateBundleV3 | None = None) -> CandidateBundleV3:
    """At most three complete sources per request, twelve and 128 KiB total.

    Missing and oversized references remain misses; V3 can draw the absent
    parts itself. Never truncate an SVG or impose a V2 capability/view filter.
    """
    assets = {row.asset_id: row.model_copy(deep=True) for row in existing.assets} if existing else {}
    byte_count = sum(len(row.svg.encode("utf-8")) for row in assets.values())
    result_needs = {row.need_id: row.model_copy(deep=True) for row in existing.needs} if existing else {}
    skipped_budget = 0
    rows = _inventory()
    ranked = {need.need_id: _rank(need, rows, subject=subject, education_level=education_level) for need in needs}
    positions = {need.need_id: 0 for need in needs}
    selections = {need.need_id: [] for need in needs}
    # Give every requested object its first reference before offering a second
    # or third alternative. Broad scenes must not starve their last objects.
    for _round in range(PER_NEED):
        for need in needs:
            selected = selections[need.need_id]
            matches = ranked[need.need_id]
            while positions[need.need_id] < len(matches):
                row = matches[positions[need.need_id]]
                positions[need.need_id] += 1
                aid = row["asset_id"]
                if aid in selected:
                    continue
                if aid in assets:
                    if need.need_id not in assets[aid].allowed_for_needs:
                        assets[aid].allowed_for_needs.append(need.need_id)
                    selected.append(aid)
                    break
                if len(assets) >= MAX_REFERENCES:
                    continue
                try:
                    card = asset_card(aid)
                    svg = material_drawing(card).svg()
                    size = len(svg.encode("utf-8"))
                    if byte_count+size > MAX_SOURCE_BYTES:
                        skipped_budget += 1
                        continue
                    guide = for_asset(aid, card["version"])
                    assets[aid] = CandidateMaterialV3(asset_id=aid, version=card["version"],
                        title=row["title"], svg=svg, source_hash=digest(svg),
                        guidance_version=guide["version"],
                        usage_guidance=guide.get("hints", {}).get("compose", ""),
                        review_guidance=guide.get("hints", {}).get("review", ""),
                        allowed_for_needs=[need.need_id])
                    byte_count += size
                    selected.append(aid)
                    break
                except MaterialError:
                    # Disabled/deleted after inventory: try the next authorized
                    # candidate without exposing the stale private reference.
                    continue
    for need in needs:
        result_needs[need.need_id] = CandidateNeedV3(need_id=need.need_id, candidate_ids=selections[need.need_id])
    return CandidateBundleV3(catalog_version=catalog_version(), needs=list(result_needs.values()),
        assets=list(assets.values()), retrieval_trace={"retrieval_algorithm": "local_fuzzy_v3",
            "candidate_limit_per_need": PER_NEED, "reference_count": len(assets),
            "full_svg_bytes": byte_count, "source_byte_limit": MAX_SOURCE_BYTES,
            "omitted_for_source_budget": skipped_budget,
            "unmatched_need_ids": [row.need_id for row in result_needs.values() if not row.candidate_ids]})
