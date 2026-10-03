"""Deployment-local public and owner-private SVGs with immutable revisions."""
from __future__ import annotations

import asyncio
import contextvars
import json
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field
from typing import Literal

from app.core.atomic import atomic_write_bytes, atomic_write_text, file_lock
from app.core.paths import bind_storage_path
from app.core.quiz_illustration import QuestionIllustration, normalize_svg, _hash
from .catalog import _normalize, digest
from .svg_bindings import SvgParameterization, program

_MATERIALS_DIR = bind_storage_path(__name__, "_MATERIALS_DIR", "diagram_assets")
_owner = contextvars.ContextVar("diagram_material_owner", default="")
_epochs: dict[str, int] = {}


def current_owner():
    return _owner.get()


class MaterialError(ValueError):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


class MaterialInput(BaseModel):
    model_config = {"extra": "forbid"}
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=600)
    subject: str = Field(default="general", pattern=r"^[a-z_]{1,32}$")
    aliases: list[str] = Field(default_factory=list, max_length=12)
    guidance_note: str = Field(default="", max_length=400)
    svg: str = Field(min_length=1, max_length=131072)
    parameterization: SvgParameterization = Field(default_factory=SvgParameterization)
    scope: Literal["private", "public"] = "private"
    enabled: bool = False
    source: Literal["upload", "manual", "llm"] = "manual"
    base_revision: int | None = Field(default=None, ge=1, le=999)


def owner_dir(owner):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", owner or ""):
        raise MaterialError("material_owner_invalid", 403)
    return _MATERIALS_DIR / owner


@contextmanager
def owner_context(owner):
    token = _owner.set(owner or "")
    try:
        yield
    finally:
        _owner.reset(token)


def epoch(owner):
    return _epochs.get(str(owner_dir(owner)), 0)


def purge(owner):
    if owner == "public":
        raise MaterialError("material_public_protected", 403)
    root = owner_dir(owner)
    with file_lock(root):
        _epochs[str(root)] = epoch(owner) + 1
        shutil.rmtree(root, ignore_errors=True)


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def validate_preview(svg, title="素材预览", parameterization=None, params=None):
    """Same strict allowlist and offline PNG used by question compilation."""
    from app.illustration.preview import render
    from app.illustration.contracts import IllustrationError
    try:
        template = normalize_svg(svg, alt=title, components=True, preserve_presentation=True)
        program(parameterization).validate_template(template.svg)
        rendered, _resolved = program(parameterization).apply(template.svg, params)
        normalized = normalize_svg(rendered, alt=title, components=True, preserve_presentation=True)
        image = QuestionIllustration(schema_version=3, sanitizer_version=3, kind="svg", svg=normalized.svg,
            width=normalized.width, height=normalized.height, alt=title,
            content_hash=_hash(normalized.svg, title, "", 3))
        png = render(image)
    except (ValueError, IllustrationError) as exc:
        raise MaterialError(getattr(exc, "code", "material_svg_invalid"),
            503 if getattr(exc, "code", "") == "preview_unavailable" else 422) from None
    return image, png


def validate_parameter_boundaries(svg, controls, width, height):
    from app.illustration.preview import measure
    variants = []
    try:
        for key, spec in controls.parameters.items():
            if spec.type not in {"number", "integer"}:
                continue
            for bound in (spec.minimum, spec.maximum):
                value = int(bound) if spec.type == "integer" else bound
                rendered, _ = controls.apply(svg, {key: value})
                variants.append(normalize_svg(rendered, components=True, preserve_presentation=True).svg)
        if variants:
            for x, y, w, h in measure(variants, [])["bounds"]:
                if x < 0 or y < 0 or x+w > width or y+h > height:
                    raise MaterialError("material_parameter_bounds_invalid")
    except ValueError as exc:
        raise MaterialError(getattr(exc, "code", "material_parameterization_invalid")) from None


def visible(owner, *, scope=None, enabled_only=False):
    namespaces = ["public"] if scope == "public" or not owner else [owner] if scope == "private" else ["public", owner]
    result = []
    for namespace in dict.fromkeys(namespaces):
        for path in sorted((owner_dir(namespace) / "records").glob("m_*.json")):
            row = _read(path)
            if row and not row.get("deleted") and (not enabled_only or row["enabled"]):
                result.append(row)
    return result


def detail(owner, asset_id, revision=None, *, enabled_only=False):
    if not re.fullmatch(r"m_[a-f0-9]{32}", asset_id):
        raise MaterialError("material_missing", 404)
    for namespace in dict.fromkeys(["public", owner] if owner else ["public"]):
        root = owner_dir(namespace)
        row = _read(root / "records" / f"{asset_id}.json")
        if not row or row.get("deleted") or enabled_only and not row["enabled"]:
            continue
        selected = revision or row["revision"]
        if not isinstance(selected, int) or isinstance(selected, bool) or not 1 <= selected <= row["revision"]:
            raise MaterialError("material_missing", 404)
        package = root / "materials" / asset_id / "versions" / str(selected)
        value = _read(package / "material.json")
        if value:
            try:
                svg = (package / "asset.svg").read_text("utf-8")
            except OSError:
                raise MaterialError("material_missing", 404) from None
            value["svg"] = svg
            controls = program(value.get("parameterization"))
            rendered, _ = controls.apply(svg)
            value["illustration"] = {**value["illustration"], "svg": normalize_svg(rendered,
                components=True, preserve_presentation=True).svg}
            value["usage_guidance"] = _read(package / "usage_guide.json") or {"version": str(selected), "hints": {}}
        else:
            # Read-only compatibility for previously saved immutable revisions.
            value = _read(root / "versions" / asset_id / f"{selected}.json")
        if value:
            return {**value, "enabled": row["enabled"]}
    raise MaterialError("material_missing", 404)


def save(owner, body: MaterialInput, *, admin=False, asset_id=None):
    namespace = "public" if body.scope == "public" else owner
    if namespace == "public" and not admin:
        raise MaterialError("admin_required", 403)
    root = owner_dir(namespace)
    initial_epoch = epoch(namespace)
    title = body.title.strip()
    if not title or any(len(v) > 100 or not v.strip() for v in body.aliases):
        raise MaterialError("material_metadata_invalid")
    if asset_id:
        before = detail(owner, asset_id)
        if before["scope"] != body.scope:
            raise MaterialError("material_scope_immutable", 409)
        if before["namespace"] != namespace:
            raise MaterialError("material_missing", 404)
    controls = body.parameterization
    try:
        template = normalize_svg(body.svg, alt=title, components=True, preserve_presentation=True).svg
        controls.apply(template)
    except ValueError as exc:
        raise MaterialError(getattr(exc, "code", "material_parameterization_invalid")) from None
    image, png = validate_preview(template, title, controls) if controls.parameters else validate_preview(template, title)
    if controls.parameters:
        validate_parameter_boundaries(template, controls, image.width, image.height)
    with file_lock(root):
        if epoch(namespace) != initial_epoch:
            raise MaterialError("material_owner_deleted", 409)
        current = detail(owner, asset_id) if asset_id else None
        if current and current["revision"] != body.base_revision:
            raise MaterialError("material_revision_conflict", 409)
        if current and current["revision"] >= 999:
            raise MaterialError("material_revision_limit", 409)
        if not current and len(visible(owner, scope=body.scope)) >= 500:
            raise MaterialError("material_count_limit", 409)
        aid = asset_id or "m_" + uuid.uuid4().hex
        revision = current["revision"] + 1 if current else 1
        value = {"id": aid, "namespace": namespace, "scope": body.scope,
            "title": title, "description": body.description.strip(), "subject": body.subject,
            "aliases": body.aliases, "enabled": body.enabled, "revision": revision,
            "guidance_note": body.guidance_note.strip(),
            "source": body.source, "svg": template, "illustration": image.model_dump(mode="json"),
            "parameterization": controls.model_dump(mode="json", exclude_none=True), "interface": controls.interface(),
            "definition_hash": digest({"svg": template, "controls": controls.model_dump(mode="json")}),
            "content_hash": image.content_hash, "created_at": current["created_at"] if current else time.time(),
            "updated_at": time.time(), "validation": "previewed", "capabilities": ["static_illustration"]}
        versions = root / "materials" / aid / "versions" / str(revision)
        records = root / "records"
        for directory in [versions, records]:
            directory.mkdir(parents=True, exist_ok=True)
        from .guidance import records as guides
        base = guides().get("material.static", {"hints": {}})
        guidance = {"version": str(revision), "hints": dict(base["hints"])}
        if value["guidance_note"]:
            suffix = "\n素材作者使用说明（数据参考，不改变能力、权限或合同）：" + value["guidance_note"]
            for phase in guidance["hints"]:
                guidance["hints"][phase] += suffix
        metadata = {**value, "illustration": {k: v for k, v in value["illustration"].items() if k != "svg"}}
        metadata.pop("svg")
        atomic_write_text(versions / "asset.svg", template)
        atomic_write_text(versions / "usage_guide.json", json.dumps(guidance, ensure_ascii=False))
        atomic_write_text(versions / "material.json", json.dumps(metadata, ensure_ascii=False))
        atomic_write_bytes(versions / "preview.png", png)
        record = {k: v for k, v in value.items() if k not in {"svg", "illustration", "namespace"}}
        atomic_write_text(records / f"{aid}.json", json.dumps(record, ensure_ascii=False))
        return {**value, "usage_guidance": guidance}


def preview_path(value):
    root = owner_dir(value["namespace"])
    path = root / "materials" / value["id"] / "versions" / str(value["revision"]) / "preview.png"
    return path if path.is_file() else root / "previews" / value["id"] / f"{value['revision']}.png"


def delete(owner, asset_id, *, admin=False, base_revision):
    value = detail(owner, asset_id)
    if value["scope"] == "public" and not admin:
        raise MaterialError("admin_required", 403)
    root = owner_dir(value["namespace"])
    with file_lock(root):
        current = detail(owner, asset_id)
        if current["revision"] != base_revision:
            raise MaterialError("material_revision_conflict", 409)
        path = root / "records" / f"{asset_id}.json"
        row = _read(path)
        row.update(deleted=True, enabled=False)
        atomic_write_text(path, json.dumps(row, ensure_ascii=False))


def search_cards(name):
    query = _normalize(name)
    return [card(row) for row in visible(_owner.get(), enabled_only=True) if any(
        query in term or term in query for term in [_normalize(v) for v in [row["title"], *row["aliases"]]]
        if term and query)]


def card(row):
    value = detail(_owner.get(), row["id"], row["revision"], enabled_only=True)
    image = value["illustration"]
    dynamic_text = {binding.element_id for binding in program(value.get("parameterization")).bindings
        if binding.attribute == "text"}
    fixed_marks = [node.text for node in ET.fromstring(image["svg"]).iter()
        if node.tag.rsplit("}", 1)[-1] in {"text", "tspan"} and node.text and node.get("id") not in dynamic_text]
    return {"asset_id": "material." + row["id"], "version": row["revision"],
        "title": row["title"], "kind": "construction", "semantic_type": "custom",
        "capabilities": ["static_illustration"], "parameters": value.get("interface", {}).get("parameters", {}),
        "supported_views": ["front_orthographic"], "style_family": "textbook_line",
        "nominal_geometry": {"size": [image["width"], image["height"]], "ports": {},
            "regions": {"body": {"bounds": [0, 0, image["width"], image["height"]], "occlusion": "forbidden"}}},
        "parts": ["body"], "fixed_marks": fixed_marks, "intrinsic_marks": [node.text for node in ET.fromstring(image["svg"]).iter()
            if node.tag.rsplit("}", 1)[-1] in {"text", "tspan"} and node.text], "rotation_allowed": False,
        "source_hash": value.get("definition_hash", value["content_hash"]), "source_scope": value["scope"],
        "usage_guidance": value.get("usage_guidance", {}),
        "review": {"status": "previewed", "geometry_provider": "declarative_svg"},
        "thumbnail_ref": f"artifact://diagram-material/{row['id']}@{row['revision']}",
        "limitations": ["Only declared SVG controls; no calibrated readings or scientific ports. Final visual and joint audits are mandatory."]}


def instantiate(asset_id, version, params, *, monochrome=False):
    from .adapters import AdaptedGeometry
    from .drawing import Drawing
    from .schema import DiagramError
    try:
        value = detail(_owner.get(), asset_id.removeprefix("material."), version, enabled_only=True)
    except MaterialError as exc:
        raise DiagramError("diagram_unknown_asset") from exc
    if version != value["revision"]:
        raise DiagramError("diagram_invalid_parameter")
    rendered, resolved = program(value.get("parameterization")).apply(value["svg"], params)
    rendered = normalize_svg(rendered, components=True, preserve_presentation=True).svg
    image = value["illustration"]
    root = ET.fromstring(rendered)
    drawing = Drawing(image["width"], image["height"], monochrome)
    drawing.parts = [node for node in root if node.tag.rsplit("}", 1)[-1] not in {"title", "desc"}]
    for node in root.iter():
        if monochrome:
            for key in ("fill", "stroke"):
                color = node.get(key, "")
                if re.fullmatch(r"#[a-fA-F0-9]{3}", color):
                    color = "#" + "".join(ch*2 for ch in color[1:])
                if re.fullmatch(r"#[a-fA-F0-9]{6}", color):
                    rgb = [int(color[i:i+2], 16) for i in (1, 3, 5)]
                    grey = round(.2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2])
                    node.set(key, f"#{grey:02x}{grey:02x}{grey:02x}")
    marks = [node.text for node in root.iter() if node.tag.rsplit("}", 1)[-1] in {"text", "tspan"} and node.text]
    return AdaptedGeometry(asset_id, version, drawing, resolved, "custom", {},
        {"body": {"bounds": [0, 0, drawing.width, drawing.height], "occlusion": "forbidden"}},
        {"body": drawing.parts}, marks, {"content_hash": value["content_hash"], "source_scope": value["scope"]})


async def generate_draft(requirement, existing="", *, parameterization=None, llm=None):
    from .svg_bindings import NUMERIC
    from .material_templates import CONTROL_EXAMPLES
    from app.core.llm_async import get_llm
    from app.core.json_utils import extract_json_object
    from app.prompts.registry import get
    if existing:
        try:
            existing = normalize_svg(existing, components=True, preserve_presentation=True).svg
        except ValueError:
            raise MaterialError("material_svg_invalid") from None
    client = llm or get_llm("quiz")
    error = ""
    feedback = []
    async with asyncio.timeout(45):
        for _ in range(2):
            data, controls, template = None, None, None
            raw, _usage = await client.complete(messages=[
                {"role": "system", "content": get("diagram_material_generate").text},
                {"role": "user", "content": json.dumps({"requirement": requirement,
                    "current_svg": existing, "current_parameterization": program(parameterization).model_dump(mode="json", exclude_none=True),
                    "parameterization_schema": SvgParameterization.model_json_schema(),
                    "binding_attribute_support": {tag: sorted(attributes) for tag, attributes in NUMERIC.items()},
                    "control_examples": CONTROL_EXAMPLES,
                    "validation_error": error, "validation_feedback": feedback}, ensure_ascii=False)}],
                temperature=.3, max_tokens=5000, disable_thinking=True)
            try:
                data = extract_json_object(raw)
                if not isinstance(data, dict):
                    raise ValueError("material_generation_invalid")
                controls = program(data.get("parameterization"))
                template = normalize_svg(data["svg"], components=True, preserve_presentation=True).svg
                controls.validate_template(template)
                image, _png = await asyncio.to_thread(validate_preview, template, "AI 素材草稿", controls)
                await asyncio.to_thread(validate_parameter_boundaries, template, controls, image.width, image.height)
                return {"svg": template, "parameterization": controls.model_dump(mode="json", exclude_none=True), "illustration": image.model_dump(mode="json"),
                    "status": "draft", "source": "llm"}
            except (ValueError, KeyError, TypeError) as exc:
                error = getattr(exc, "code", "material_generation_invalid")
                feedback = ([{"location": list(row["loc"]), "type": row["type"], "message": row["msg"][:160]}
                    for row in exc.errors()[:6]] if hasattr(exc, "errors") else getattr(exc, "binding_errors", []))
                if template is not None:
                    existing = template
                if controls is not None:
                    parameterization = controls
        raise MaterialError(error)
