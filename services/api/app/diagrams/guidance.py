"""Trusted, bounded asset hints; model/user strings never become file paths."""
import json
import re
from functools import lru_cache
from pathlib import Path

GUIDE_DIR = Path(__file__).resolve().parents[2] / "assets/diagram_library/materials"
PHASES = {"authoring", "requirements", "compose", "review"}


@lru_cache(maxsize=1)
def records():
    result = {}
    for path in sorted(GUIDE_DIR.glob("*/usage_guide.json")):
        row = json.loads(path.read_text("utf-8"))
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,95}", path.parent.name):
            raise ValueError("invalid asset guidance identity")
        if set(row) - {"version", "hints", "match_terms"} or not {"version", "hints"} <= set(row) or not set(row["hints"]) <= PHASES:
            raise ValueError("invalid asset guidance")
        terms = row.get("match_terms", [])
        if len(terms) > 12 or any(not isinstance(term, str) or not 2 <= len(term) <= 80 for term in terms):
            raise ValueError("invalid asset guidance match terms")
        if any(not isinstance(text, str) or len(text) > 600 for text in row["hints"].values()):
            raise ValueError("asset guidance exceeds budget")
        result[path.parent.name] = {"version": row["version"], "hints": row["hints"], "match_terms": terms}
    return result


def for_asset(asset_id, version=None):
    if asset_id.startswith("material.m_"):
        from .materials import current_owner, detail
        return detail(current_owner(), asset_id.removeprefix("material."), version, enabled_only=True).get(
            "usage_guidance", records().get("material.static", {"version": "1.0.0", "hints": {}}))
    return records().get(asset_id, {"version": "1.0.0", "hints": {}})


def selected_hints(bundle, phase):
    return [{"asset_id": card["asset_id"], "version": row["version"], "text": row["hints"][phase]}
        for card in bundle.assets if phase in (row := for_asset(card["asset_id"], card["version"]))["hints"]]


def bundle_view(bundle, phase):
    value = bundle.model_dump(mode="json")
    for card in value["assets"]:
        guide = for_asset(card["asset_id"], card["version"])
        card["usage_guidance"] = {"version": guide["version"],
            "text": guide["hints"].get(phase, "")}
        card["review"] = {key: item for key, item in card.get("review", {}).items()
            if key in {"status", "source_hash", "metadata_version", "geometry_provider"}}
    return value


def register():
    from app.prompts.registry import PromptDef, _register
    for asset_id, row in records().items():
        for phase, hint in row["hints"].items():
            _register(PromptDef(id=f"diagram_asset.{asset_id}.{phase}",
                version=row["version"], text=hint), active=False)
