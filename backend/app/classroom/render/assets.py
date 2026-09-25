"""课堂渲染资产包（plan.md §9.4）。

`backend/app/classroom/static/generated/` 由前端
`pnpm run build:classroom` 生成（tsc frame-runtime + 固定 KaTeX dist + 字体
data URI 化 + manifest/hash），Git 忽略源码外的产物。缺包时 capability
明确 renderer_unavailable，运行时不从 CDN 下载补齐。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

GENERATED_DIR = Path(__file__).resolve().parent.parent / "static" / "generated"

RUNTIME_VERSION = "1.0.0"


@dataclass(frozen=True)
class AssetPack:
    runtime_version: str
    runtime_js: str
    runtime_sha256: str
    katex_js: str
    katex_sha256: str
    katex_css: str
    katex_css_sha256: str


def load_asset_pack() -> AssetPack | None:
    """读取生成包；缺失/损坏返回 None（调用方报 renderer_unavailable）。"""
    manifest_path = GENERATED_DIR / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest.get("files") or {}
        runtime = (GENERATED_DIR / "runtime.js").read_text(encoding="utf-8")
        katex_js = (GENERATED_DIR / "katex.min.js").read_text(encoding="utf-8")
        katex_css = (GENERATED_DIR / "katex.min.css").read_text(
            encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        return None
    if not runtime or not katex_js or not katex_css:
        return None
    return AssetPack(
        runtime_version=str(manifest.get("runtime_version") or RUNTIME_VERSION),
        runtime_js=runtime,
        runtime_sha256=str(files.get("runtime.js", {}).get("sha256", "")),
        katex_js=katex_js,
        katex_sha256=str(files.get("katex.min.js", {}).get("sha256", "")),
        katex_css=katex_css,
        katex_css_sha256=str(files.get("katex.min.css", {}).get("sha256", "")),
    )


def renderer_available() -> bool:
    return load_asset_pack() is not None
