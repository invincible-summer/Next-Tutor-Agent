"""Server-selected image generation gateway.

The browser/mobile clients submit an intent and optional references. Provider,
protocol, endpoint and model are deployment configuration, never user input.
The adapter accepts arbitrary model identifiers so a new model on an
OpenAI-compatible third-party service does not require a frontend release.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
from dataclasses import dataclass
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.identity.deps import require_user, resolve_student_id

router = APIRouter(
    prefix="/tools/image",
    tags=["tool-assistant"],
    dependencies=[Depends(require_user)],
)

ImageReferenceMode = Literal["direct", "auto", "selected"]
AspectRatio = Literal["1:1", "4:3", "16:9", "3:4"]


class GenerateImageRequest(BaseModel):
    # Deprecated compatibility fields. They are intentionally ignored by the
    # endpoint; keeping them optional lets an older web bundle upgrade safely.
    provider: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=200)
    prompt: str = Field(min_length=1, max_length=12000)
    reference_mode: ImageReferenceMode = "direct"
    reference_material_ids: list[str] = Field(default_factory=list, max_length=12)
    reference_material_versions: dict[str, int] = Field(default_factory=dict, max_length=12)
    reference_images: list[str] = Field(default_factory=list, max_length=8)
    aspect_ratio: AspectRatio = "16:9"

    @field_validator("reference_images")
    @classmethod
    def reference_budget(cls, values: list[str]) -> list[str]:
        if sum(len(value) for value in values) > 16_000_000:
            raise ValueError("reference_images_too_large")
        for value in values:
            # References supplied by a client must be inline image bytes. URL
            # fetching is reserved for the configured provider response so a
            # user cannot turn this endpoint into an SSRF proxy.
            if value and not value.startswith("data:image/"):
                raise ValueError("reference_image_invalid_url")
        return values


@dataclass(frozen=True)
class ActiveImageConfig:
    provider: str
    protocol: str
    base_url: str
    api_key: str
    model: str
    supports_reference: bool
    configured: bool


def active_image_config() -> ActiveImageConfig:
    """Resolve the single active backend with a migration fallback."""
    if settings.image_api_key and settings.image_api_base_url and settings.image_api_model:
        return ActiveImageConfig(
            provider=settings.image_api_provider,
            protocol=settings.image_api_protocol,
            base_url=settings.image_api_base_url,
            api_key=settings.image_api_key,
            model=settings.image_api_model,
            supports_reference=settings.image_api_supports_reference,
            configured=settings.image_api_enabled,
        )
    legacy = (
        ("gpt-image", "openai_compatible", settings.gpt_image_api_key,
         settings.gpt_image_base_url, "gpt-image-1"),
        ("qwen-image", "dashscope", settings.qwen_image_api_key,
         settings.qwen_image_base_url, "qwen-image-3.0-pro"),
        ("seed", "seedream", settings.seed_image_api_key,
         settings.seed_image_base_url, "doubao-seedream-4-5-251128"),
    )
    provider, protocol, key, url, model = next((row for row in legacy if row[2]), legacy[0])
    return ActiveImageConfig(
        provider=provider,
        protocol=protocol,
        base_url=url.rstrip("/"),
        api_key=key,
        model=model,
        supports_reference=True,
        configured=bool(settings.image_api_enabled and key),
    )


def _size(aspect: str) -> str:
    return {"1:1": "1024x1024", "4:3": "1536x1024", "16:9": "1536x864", "3:4": "1024x1536"}[aspect]


def _openai_size(body: GenerateImageRequest) -> str:
    model = body.model or ""
    if model.startswith("gpt-image-1"):
        return {"1:1": "1024x1024", "4:3": "1536x1024", "16:9": "1536x1024", "3:4": "1024x1536"}[body.aspect_ratio]
    return _size(body.aspect_ratio)


def _reference_prompt(body: GenerateImageRequest) -> str:
    if body.reference_mode == "auto":
        return body.prompt + "\nUse the reviewed teaching references as structural guidance; redraw the final image and keep labels readable."
    if body.reference_mode == "selected" and body.reference_material_ids:
        return body.prompt + "\nUse the selected reviewed teaching references as visual guidance and redraw the final image."
    return body.prompt


def _catalog_candidates(prompt: str):
    try:
        from app.diagrams.catalog import search
        return search(prompt, top_k=3, gallery=True)
    except Exception:
        return []


def _auto_reference_images(prompt: str) -> list[str]:
    """Render reviewed SVG catalog entries to bounded PNG data URLs."""
    try:
        from app.diagrams.compiler import preview_asset
        from app.illustration import preview
        refs = []
        for candidate in _catalog_candidates(prompt):
            try:
                illustration = preview_asset(candidate.id)
                png = preview.render(illustration)
                refs.append("data:image/png;base64," + base64.b64encode(png).decode("ascii"))
            except Exception:
                continue
        return refs[:3]
    except Exception:
        return []


def _material_reference_images(owner: str, ids: list[str], versions: dict[str, int] | None = None) -> list[str]:
    """Resolve only materials visible to the authenticated owner."""
    if not ids:
        return []
    try:
        from app.diagrams.materials import detail, owner_context
        from app.illustration import preview
        refs: list[str] = []
        with owner_context(owner):
            for asset_id in ids[:8]:
                # Mobile material cards prefix private IDs to distinguish them
                # from catalog assets; the server resolves the opaque ID only
                # after removing that presentation prefix.
                material_id = asset_id.removeprefix("material.")
                revision = (versions or {}).get(asset_id) or (versions or {}).get(material_id)
                material = detail(owner, material_id, revision, enabled_only=True)
                illustration = material.get("illustration")
                if not isinstance(illustration, dict):
                    continue
                png = preview.render(illustration)
                refs.append("data:image/png;base64," + base64.b64encode(png).decode("ascii"))
        return refs
    except Exception as exc:
        raise HTTPException(422, {"code": "image_reference_material_invalid"}) from exc


def _image_from_payload(provider: str, payload: dict) -> tuple[str, str | None]:
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    rows = payload.get("data") or output.get("results") or payload.get("images") or output.get("images") or []
    if isinstance(rows, (dict, str)):
        rows = [rows]
    if not rows:
        rows = [
            item
            for choice in output.get("choices", []) or []
            if isinstance(choice, dict)
            for item in (
                choice.get("message", {}).get("content", [])
                if isinstance(choice.get("message"), dict)
                else []
            )
        ]
    if not isinstance(rows, list) or not rows:
        raise HTTPException(502, {"code": "image_provider_empty", "provider": provider})
    first = rows[0]
    if isinstance(first, str) and first:
        return first, None
    row = first if isinstance(first, dict) else {}
    candidate = row.get("url") or row.get("image") or row.get("image_url") or row.get("uri")
    if isinstance(candidate, dict):
        candidate = candidate.get("url") or candidate.get("image_url") or candidate.get("uri")
    if candidate:
        return str(candidate), row.get("revised_prompt")
    encoded = row.get("b64_json") or row.get("base64")
    if encoded:
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(502, {"code": "image_provider_invalid_base64", "provider": provider}) from exc
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii"), row.get("revised_prompt")
    raise HTTPException(502, {"code": "image_provider_invalid_response", "provider": provider})


def _openai_edit_payload(body: GenerateImageRequest, prompt: str) -> dict:
    data = {
        "model": body.model,
        "prompt": prompt,
        "size": _openai_size(body),
        "n": 1,
        "output_format": "png",
        "images": [{"image_url": value} for value in body.reference_images],
    }
    if body.model in {"gpt-image-1", "gpt-image-1.5"}:
        data["input_fidelity"] = "high"
    return data


def _openai_generation_payload(body: GenerateImageRequest, prompt: str) -> dict:
    return {"model": body.model, "prompt": prompt, "size": _openai_size(body), "n": 1, "output_format": "png"}


def _qwen_payload(body: GenerateImageRequest, prompt: str) -> dict:
    content: list[dict[str, str]] = [{"image": image} for image in body.reference_images]
    content.append({"text": prompt})
    return {
        "model": body.model,
        "input": {"messages": [{"role": "user", "content": content}]} if body.reference_images else {"prompt": prompt},
        "parameters": {"size": _size(body.aspect_ratio).replace("x", "*")},
    }


def _seed_payload(body: GenerateImageRequest, prompt: str) -> dict:
    payload: dict = {
        "model": body.model,
        "prompt": prompt,
        "size": _size(body.aspect_ratio),
        "response_format": "url",
        "output_format": "png",
        "watermark": False,
    }
    if not body.model.startswith(("doubao-seedream-5-0-pro", "doubao-seedream-5-0-flash")):
        payload["sequential_image_generation"] = "disabled"
    if body.reference_images:
        payload["image"] = body.reference_images[0] if len(body.reference_images) == 1 else body.reference_images
    return payload


def _endpoint(base_url: str, suffix: str) -> str:
    return base_url if base_url.endswith(suffix) else base_url + suffix


async def _materialize_provider_url(image_url: str) -> str:
    if not image_url.startswith(("http://", "https://")):
        return image_url
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0), follow_redirects=True) as client:
            response = await client.get(image_url)
        if response.status_code >= 400 or len(response.content) > 12 * 1024 * 1024:
            return image_url
        mime = response.headers.get("content-type", "image/png").split(";", 1)[0]
        if not mime.startswith("image/"):
            return image_url
        return f"data:{mime};base64," + base64.b64encode(response.content).decode("ascii")
    except httpx.HTTPError:
        return image_url


async def generate_image(body: GenerateImageRequest, *, owner: str = "") -> dict:
    config = active_image_config()
    if not settings.image_api_enabled or not config.configured:
        raise HTTPException(503, {"code": "image_provider_unconfigured"})
    if body.reference_mode != "direct" and not config.supports_reference:
        raise HTTPException(422, {"code": "image_reference_unsupported"})
    request = body.model_copy(update={"model": config.model})
    prompt = _reference_prompt(request)
    refs = list(request.reference_images)
    if request.reference_material_ids:
        refs.extend(_material_reference_images(owner, request.reference_material_ids, request.reference_material_versions))
    if request.reference_mode == "auto":
        refs.extend(await asyncio.to_thread(_auto_reference_images, request.prompt))
    request = request.model_copy(update={"reference_images": refs[:8]})

    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    if config.protocol == "dashscope":
        url = config.base_url
        payload = _qwen_payload(request, prompt)
        headers["X-DashScope-Async"] = "enable"
    elif config.protocol == "seedream":
        url = config.base_url
        payload = _seed_payload(request, prompt)
    else:
        url = _endpoint(config.base_url, "/images/edits" if refs else "/images/generations")
        payload = _openai_edit_payload(request, prompt) if refs else _openai_generation_payload(request, prompt)
    timeout = httpx.Timeout(settings.image_api_timeout_seconds, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(502, {"code": "image_provider_unreachable"}) from exc
    if response.status_code >= 400:
        try:
            error_payload = response.json()
            nested_error = error_payload.get("error") if isinstance(error_payload, dict) else None
            message = (
                error_payload.get("message") if isinstance(error_payload, dict) else None
            ) or (nested_error.get("message") if isinstance(nested_error, dict) else None) or (
                error_payload.get("code") if isinstance(error_payload, dict) else None
            )
        except (ValueError, json.JSONDecodeError):
            message = response.text[:240]
        raise HTTPException(502, {"code": "image_provider_rejected", "status": response.status_code, "message": message})
    try:
        payload_out = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(502, {"code": "image_provider_invalid_json"}) from exc
    if not isinstance(payload_out, dict):
        raise HTTPException(502, {"code": "image_provider_invalid_json"})
    provider_output = payload_out.get("output") if isinstance(payload_out.get("output"), dict) else {}
    if config.protocol == "dashscope" and not (provider_output.get("results") or payload_out.get("data")):
        task_id = provider_output.get("task_id") or payload_out.get("task_id")
        if task_id:
            task_url = config.base_url.split("/services/", 1)[0] + "/tasks/" + str(task_id)
            async with httpx.AsyncClient(timeout=timeout) as poller:
                for _ in range(45):
                    await asyncio.sleep(2)
                    polled = await poller.get(task_url, headers={"Authorization": f"Bearer {config.api_key}"})
                    if polled.status_code >= 400:
                        break
                    payload_out = polled.json()
                    polled_output = payload_out.get("output") if isinstance(payload_out.get("output"), dict) else {}
                    status = polled_output.get("task_status") or payload_out.get("task_status")
                    if status in {"SUCCEEDED", "FAILED", "CANCELED"}:
                        break
                final_output = payload_out.get("output") if isinstance(payload_out.get("output"), dict) else {}
                final_status = final_output.get("task_status") or payload_out.get("task_status")
                if final_status in {"FAILED", "CANCELED"}:
                    raise HTTPException(502, {"code": "image_provider_task_failed", "status": final_status})
                if final_status not in {"SUCCEEDED"}:
                    raise HTTPException(504, {"code": "image_provider_timeout"})
    image_url, revised = _image_from_payload(config.provider, payload_out)
    image_url = await _materialize_provider_url(image_url)
    return {
        "provider": config.provider,
        "protocol": config.protocol,
        "model": config.model,
        "image_url": image_url,
        "mime_type": "image/png",
        "revised_prompt": revised,
        "source": "provider",
    }


@router.get("/capability")
def capability() -> dict:
    config = active_image_config()
    return {
        "configured": bool(settings.image_api_enabled and config.configured),
        "provider": config.provider,
        "protocol": config.protocol,
        "model": config.model if config.configured else "",
        "supports_reference": config.supports_reference,
        "max_references": 8,
    }


@router.get("/providers")
def providers() -> dict:
    """Compatibility envelope; only the active server capability is exposed."""
    active = capability()
    return {"active": active, "providers": [{"id": active["provider"], **active}]}


@router.post("/generate")
async def generate(body: GenerateImageRequest, owner: str = Depends(resolve_student_id)) -> dict:
    return await generate_image(body, owner=owner)
