"""Local lexical retrieval, hard capability/view filters, six cards per need."""
from __future__ import annotations

from difflib import SequenceMatcher

from app.diagrams.catalog import _normalize, catalog, search
from app.diagrams.semantics import (COMPONENTS, RECIPES, METADATA_VERSION,
    asset_card, capabilities, catalog_version)

from .contracts import CandidateBundleV2, CandidateNeed, VisualBriefV2


def retrieve(brief: VisualBriefV2, *, education_level="") -> CandidateBundleV2:
    needs, assets, queries = [], {}, []
    for need in brief.needs:
        from app.diagrams.materials import search_cards
        private_hits = [card["asset_id"] for card in search_cards(need.name)]
        import re
        queries_for_need = list(dict.fromkeys([need.name, re.sub(r"完整|整体|示意", "", need.name)]))
        hits = [a.id for name in queries_for_need for a in search(name, gallery=True, top_k=60,
                                   education_level=education_level) if a.review.get("status") == "passed"]
        query = _normalize(need.name)
        for asset_id, recipe in RECIPES.items():
            names = [_normalize(s) for s in (recipe.title, *recipe.aliases)]
            if any(query == name or query in name or name in query or (
                    len(query) >= 4 and SequenceMatcher(None, query, name).ratio() >= .8) for name in names):
                hits.append(asset_id)
        from app.diagrams.guidance import records
        for asset_id, guide in records().items():
            if asset_id != "material.static" and any(_normalize(term) in _normalize(name)
                    for name in queries_for_need for term in guide.get("match_terms", [])):
                hits.append(asset_id)
        # User's exact named material takes precedence over generic synonyms.
        hits = private_hits + hits
        qualified = []
        for asset_id in dict.fromkeys(hits):
            if not set(need.capabilities) <= capabilities(asset_id):
                continue
            if asset_id in RECIPES and any(catalog()[1][child].review.get("status") != "passed"
                    for _, child, *_ in RECIPES[asset_id].children):
                continue
            card = asset_card(asset_id)
            if brief.view not in card["supported_views"]:
                continue
            qualified.append(asset_id)
        selected = qualified[:6]
        needs.append(CandidateNeed(need_id=need.need_id, matched=bool(selected),
            candidate_ids=selected, missing_capabilities=[] if selected else need.capabilities))
        for asset_id in selected:
            if asset_id not in assets:
                assets[asset_id] = asset_card(asset_id)
                assets[asset_id]["allowed_for_needs"] = []
            assets[asset_id]["allowed_for_needs"].append(need.need_id)
        queries.append({"need_id": need.need_id, "normalized": query,
                        "features": list(need.capabilities)})
    return CandidateBundleV2(catalog_version=catalog_version(), metadata_version=METADATA_VERSION,
        retrieval_trace={"query_count": len(queries), "candidate_limit_per_need": 6,
                         "retrieval_algorithm": "local_lexical_v2", "queries": queries},
        needs=needs, assets=list(assets.values()))
