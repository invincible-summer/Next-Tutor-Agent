"""课堂导出回归（plan.md §19.2 test_classroom_exports）。

覆盖：ZIP 结构与路径安全、自包含离线 HTML、无 token/答案/全文教材、
credits 署名、manifest hash、源图已清理不导出旧 bytes。
（过期/删除后拒绝属导出端点行为，后续阶段补充。）
"""
from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classroom.exports import build_export_zip, build_notes_markdown  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402

from tests import classroom_fixtures as fx  # noqa: E402


def _png_bytes() -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


def _make_revision_with_image(*, asset_status: str = "ready"):
    revision = fx.make_revision(1)
    asset = sc.AssetRecord(
        asset_id=fx.hex_id("ast", 7), sha256="d" * 64, mime="image/png",
        width=100, height=60,
        provenance=sc.AssetProvenance(
            provider=sc.AssetProvider.pexels,
            provider_asset_id="123",
            source_url="https://www.pexels.com/photo/123/",
            creator="摄影师 A", creator_url="https://www.pexels.com/@a",
            license_url="https://www.pexels.com/license/",
            fetched_at=fx.utcnow()),
        alt="实验照片", caption="碰撞实验示意", role=sc.AssetRole.scene,
        bytes=len(_png_bytes()), status=sc.AssetStatus(asset_status))
    image_block = sc.ImageBlock(id=fx.hex_id("blk", 9),
                                asset_id=asset.asset_id, alt="实验照片",
                                caption="碰撞实验示意",
                                fit=sc.ImageFit.contain)
    slide = fx.make_slide(1, layout=sc.SlideLayout.image_explain,
                          blocks=[image_block])
    revision = revision.model_copy(update={
        "slides": [slide],
        "assets": [asset],
    })
    return revision, asset


class ExportZipTests(unittest.TestCase):
    def test_zip_structure_and_manifest(self):
        revision, asset = _make_revision_with_image()
        data = build_export_zip(
            revision, read_bytes=lambda aid, ext: _png_bytes())
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
        for name in ("index.html", "speaker-notes.md", "credits.html",
                     "manifest.json", "licenses/KATEX_LICENSE.txt"):
            self.assertIn(name, names)
        # 路径安全：无绝对路径与 ..
        for name in names:
            self.assertFalse(name.startswith("/"))
            self.assertNotIn("..", name)
        import json
        manifest = json.loads(zf.read("manifest.json"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["theme_id"], revision.brief.theme_id)
        self.assertEqual(manifest["content_hash"], revision.content_hash)
        self.assertIn("index.html", manifest["files"])
        # manifest 不含 owner/token
        self.assertNotIn("owner", manifest)
        self.assertNotIn("token", manifest)

    def test_offline_html_self_contained(self):
        revision, _ = _make_revision_with_image()
        data = build_export_zip(
            revision, read_bytes=lambda aid, ext: _png_bytes())
        zf = zipfile.ZipFile(io.BytesIO(data))
        index = zf.read("index.html").decode("utf-8")
        self.assertIn('data-mode="offline"', index)
        # 图片以 data URI 内嵌；无外链资源
        self.assertIn("data:image/png;base64,", index)
        self.assertNotIn('src="http', index)
        self.assertNotIn("href=\"https://fonts", index)

    def test_no_answer_or_token_in_zip(self):
        revision, _ = _make_revision_with_image()
        template = sc.CheckpointTemplate(
            checkpoint_id=fx.hex_id("ckp"), slide_id=fx.slide_hex(),
            kind=sc.CheckpointKind.question, prompt="检查题：动量守恒条件？",
            verified_question_template={"answer": "SECRET-ANSWER-XYZ"})
        revision = revision.model_copy(
            update={"checkpoint_templates": [template]})
        data = build_export_zip(revision, read_bytes=lambda a, e: None)
        raw = b"".join(zipfile.ZipFile(io.BytesIO(data)).read(n)
                       for n in zipfile.ZipFile(io.BytesIO(data)).namelist())
        self.assertNotIn(b"SECRET-ANSWER-XYZ", raw)
        self.assertNotIn(b"edu-agent-token", raw)
        self.assertNotIn(b"Bearer ", raw)

    def test_credits_contains_attribution(self):
        revision, _ = _make_revision_with_image()
        data = build_export_zip(revision, read_bytes=lambda a, e: None)
        zf = zipfile.ZipFile(io.BytesIO(data))
        credits = zf.read("credits.html").decode("utf-8")
        self.assertIn("摄影师 A", credits)
        self.assertIn("pexels.com/license", credits)

    def test_unavailable_asset_not_exported(self):
        revision, asset = _make_revision_with_image(asset_status="unavailable")
        data = build_export_zip(
            revision, read_bytes=lambda aid, ext: _png_bytes())
        zf = zipfile.ZipFile(io.BytesIO(data))
        index = zf.read("index.html").decode("utf-8")
        self.assertNotIn("data:image/png;base64,", index)
        self.assertIn("图片暂不可用", index)

    def test_notes_markdown(self):
        revision, _ = _make_revision_with_image()
        notes = build_notes_markdown(revision).decode("utf-8")
        self.assertIn("# ", notes)
        self.assertIn("动量守恒有一个前提", notes)


if __name__ == "__main__":
    unittest.main()
