"""课堂导出（plan.md §9.6：HTML 离线课件 ZIP / 逐页讲稿 Markdown）。

ZIP 结构：index.html（自包含课件及可信离线前后页控件）、
speaker-notes.md（每页讲稿+来源短标记）、credits.html（资料与图片署名）、
manifest.json（schema/renderer/theme 版本与文件 hash，不含 owner/token）、
licenses/（打包依赖许可证）。

不默认打包音频；不打包完整教材/网页、私有学习评价或正式随堂题答案。
"""
from __future__ import annotations

import io
import zipfile
from typing import Mapping

from ..core import classroom_store as store
from ..schemas.classroom import LessonRevision
from .render.assets import load_asset_pack
from .render.compiler import (
    compile_credits,
    compile_html,
    compile_speaker_notes,
)

_EXT_BY_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _collect_asset_bytes(revision: LessonRevision, *,
                         read_bytes) -> dict[str, bytes]:
    """只导出状态 ready 且磁盘仍存在的图片；已清理的上传图不导出旧 bytes。"""
    result: dict[str, bytes] = {}
    for asset in revision.assets:
        if asset.status.value != "ready":
            continue
        ext = _EXT_BY_MIME.get(asset.mime)
        if ext is None:
            continue
        data = read_bytes(asset.asset_id, ext)
        if data is not None:
            result[asset.asset_id] = data
    return result


def build_export_zip(revision: LessonRevision, *,
                     read_bytes=None) -> bytes:
    """生成 html_zip 导出；read_bytes(asset_id, ext) → bytes | None。"""
    if read_bytes is None:
        def read_bytes(asset_id: str, ext: str) -> bytes | None:  # noqa: F811
            return None
    pack = load_asset_pack()
    if pack is None:
        raise RuntimeError("renderer_unavailable")
    asset_bytes = _collect_asset_bytes(revision, read_bytes=read_bytes)
    index_html = compile_html(revision, mode="offline",
                              asset_bytes=asset_bytes)
    notes_md = compile_speaker_notes(revision)
    credits_html = compile_credits(revision)

    manifest = {
        "schema_version": revision.schema_version,
        "renderer_version": revision.renderer_version or pack.runtime_version,
        "runtime_version": pack.runtime_version,
        "theme_id": revision.brief.theme_id,
        "revision": revision.revision,
        "content_hash": revision.content_hash,
        "files": {
            "index.html": store.bytes_hash(index_html.encode("utf-8")),
            "speaker-notes.md": store.bytes_hash(notes_md.encode("utf-8")),
            "credits.html": store.bytes_hash(credits_html.encode("utf-8")),
        },
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", index_html)
        zf.writestr("speaker-notes.md", notes_md)
        zf.writestr("credits.html", credits_html)
        zf.writestr("manifest.json", store.canonical_json(manifest))
        try:
            from .render.assets import GENERATED_DIR
            license_text = (GENERATED_DIR / "KATEX_LICENSE").read_text(
                encoding="utf-8")
        except OSError:
            license_text = "KaTeX (MIT) — 见 node_modules/katex/LICENSE"
        zf.writestr("licenses/KATEX_LICENSE.txt", license_text)
    return buffer.getvalue()


def build_notes_markdown(revision: LessonRevision) -> bytes:
    """notes_md 导出：逐页讲稿（与 ZIP 内 speaker-notes.md 同源）。"""
    return compile_speaker_notes(revision).encode("utf-8")
