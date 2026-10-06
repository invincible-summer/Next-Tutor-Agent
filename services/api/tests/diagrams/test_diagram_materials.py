"""Authenticated material ownership, immutable versions and safe SVG drafts."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import httpx

from app.diagrams import materials
from app.diagrams.material_templates import TEMPLATES
from app.identity.security import create_token
from app.identity.store import create_user
from app.illustration.contracts import VisualBriefV2
from app.illustration.retrieval import retrieve
from app.main import create_app
from tests.support.storage_sandbox import StorageSandboxTestCase
import unittest


class MaterialApiTest(StorageSandboxTestCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.alice, self.bob, self.admin = "usr_material_alice", "usr_material_bob", "usr_material_admin"
        for owner in (self.alice, self.bob, self.admin):
            create_user(owner+"@test.local", owner, "unused", user_id=owner,
                role="admin" if owner == self.admin else "student")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")
        self.body = {"title": "合成容器图", "svg": TEMPLATES[1]["svg"], "enabled": True}

    async def asyncTearDown(self):
        await self.client.aclose()

    async def request(self, method, path="", *, owner=None, body=None):
        return await self.client.request(method, "/api/v1/diagram-materials"+path,
            headers={"Authorization": "Bearer "+create_token(owner or self.alice)}, json=body)

    async def create(self, **kwargs):
        response = await self.request("POST", body={**self.body, **kwargs})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_private_owner_isolation_and_public_admin_permissions(self):
        asset = await self.create()
        aid = asset["id"]
        self.assertNotIn("namespace", asset)
        for path in ("/"+aid, "/"+aid+"/preview.png"):
            response = await self.request("GET", path, owner=self.bob)
            self.assertEqual(response.status_code, 404)
        self.assertEqual((await self.request("PUT", "/"+aid, owner=self.bob,
            body={**self.body, "base_revision": 1})).status_code, 404)
        self.assertEqual((await self.request("DELETE", "/"+aid+"?base_revision=1", owner=self.bob)).status_code, 404)
        self.assertEqual((await self.request("GET", "?scope=private", owner=self.bob)).json()["total"], 0)
        self.assertEqual((await self.request("POST", body={**self.body, "scope": "public"})).status_code, 403)
        public = await self.request("POST", owner=self.admin, body={**self.body, "scope": "public"})
        self.assertEqual(public.status_code, 200, public.text)
        pid = public.json()["id"]
        self.assertEqual((await self.request("GET", "/"+pid, owner=self.bob)).status_code, 200)
        self.assertEqual((await self.request("DELETE", "/"+pid+"?base_revision=1", owner=self.bob)).status_code, 403)
        self.assertEqual((await self.request("PUT", "/"+pid, body={**self.body, "scope": "public", "base_revision": 1})).status_code, 403)
        self.assertEqual((await self.client.get("/api/v1/diagram-materials")).status_code, 401)

    async def test_listing_fuzzy_search_over_titles_and_aliases(self):
        await self.request("POST", owner=self.admin, body={
            **self.body, "title": "锥形瓶支架", "aliases": ["三角瓶"], "scope": "public"})
        items = (await self.request("GET", "?scope=public&q=三角瓶")).json()["items"]
        self.assertEqual([row["title"] for row in items], ["锥形瓶支架"])
        # CJK-friendly fuzzy: dropped characters still match via subsequence.
        items = (await self.request("GET", "?scope=public&q=锥支")).json()["items"]
        self.assertEqual([row["title"] for row in items], ["锥形瓶支架"])
        self.assertEqual((await self.request("GET", "?scope=public&q=量筒")).json()["total"], 0)
        # Private drafts stay out of public search even with matching terms.
        await self.create()
        self.assertEqual((await self.request("GET", "?scope=public&q=合成容器图")).json()["total"], 0)
        mine = (await self.request("GET", "?scope=private&q=容器图")).json()["items"]
        self.assertEqual([row["title"] for row in mine], ["合成容器图"])

    async def test_revision_conflict_scope_and_frozen_source(self):
        asset = await self.create()
        frozen_svg = asset["svg"]
        updated = await self.request("PUT", "/"+asset["id"], body={**self.body,
            "title": "新版容器", "svg": TEMPLATES[0]["svg"], "base_revision": 1})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["revision"], 2)
        stale = await self.request("PUT", "/"+asset["id"], body={**self.body, "base_revision": 1})
        self.assertEqual(stale.status_code, 409)
        old = await self.request("GET", "/"+asset["id"]+"?revision=1")
        self.assertEqual(old.json()["svg"], frozen_svg)
        self.assertEqual(old.json()["latest_revision"], 2)
        png = await self.request("GET", "/"+asset["id"]+"/preview.png?revision=1")
        self.assertTrue(png.content.startswith(b"\x89PNG"))
        self.assertEqual((await self.request("DELETE", "/"+asset["id"]+"?base_revision=1")).status_code, 409)
        self.assertEqual((await self.request("DELETE", "/"+asset["id"]+"?base_revision=2")).status_code, 200)
        self.assertEqual((await self.request("GET", "/"+asset["id"])).status_code, 404)
        self.assertEqual(asset["illustration"]["svg"], frozen_svg)

    async def test_isolated_package_guidance_versions_and_native_presentation(self):
        from xml.etree import ElementTree as ET
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><rect width="640" height="400" fill="#fff"/><circle cx="320" cy="200" r="60" fill="#f00"/></svg>'
        asset = await self.create(svg=svg, guidance_note="只有容器轮廓，不用于读数。")
        package = materials.owner_dir(self.alice) / "materials" / asset["id"] / "versions" / "1"
        self.assertEqual({p.name for p in package.iterdir()}, {"asset.svg", "material.json", "usage_guide.json", "preview.png"})
        metadata = json.loads((package / "material.json").read_text())
        self.assertNotIn("svg", metadata)
        self.assertNotIn("svg", metadata["illustration"])
        self.assertNotIn("usage_guidance", metadata)
        root = ET.fromstring(asset["svg"])
        self.assertEqual(next(n for n in root if n.tag.endswith("rect")).get("stroke"), "none")
        old_guide = json.loads((package / "usage_guide.json").read_text())
        self.assertIn("不用于读数", old_guide["hints"]["compose"])
        await self.request("PUT", "/"+asset["id"], body={**self.body, "guidance_note": "仅用作结构示意。", "base_revision": 1})
        with materials.owner_context(self.alice):
            from app.diagrams.guidance import for_asset
            self.assertEqual(for_asset("material."+asset["id"], 1), old_guide)
            self.assertNotEqual(for_asset("material."+asset["id"], 2), old_guide)
        self.assertEqual((await self.request("GET", "/"+asset["id"]+"?revision=3")).status_code, 404)

    async def test_legacy_revision_read_and_edit_into_isolated_package(self):
        from app.core.atomic import atomic_write_text
        import shutil
        asset = await self.create()
        root = materials.owner_dir(self.alice)
        value = materials.detail(self.alice, asset["id"])
        legacy = root / "versions" / asset["id"]
        legacy.mkdir(parents=True)
        atomic_write_text(legacy / "1.json", json.dumps(value))
        shutil.rmtree(root / "materials" / asset["id"])
        read = await self.request("GET", "/"+asset["id"])
        self.assertEqual(read.json()["svg"], asset["svg"])
        edited = await self.request("PUT", "/"+asset["id"], body={**self.body, "base_revision": 1})
        self.assertEqual(edited.json()["revision"], 2)
        self.assertTrue((root / "materials" / asset["id"] / "versions/2/asset.svg").is_file())

    async def test_templates_and_unsafe_svg_and_forged_owner(self):
        response = await self.request("GET", "/templates")
        self.assertEqual(len(response.json()["templates"]), 4)
        for template in response.json()["templates"]:
            preview = await self.request("POST", "/preview", body={"svg": template["svg"]})
            self.assertEqual(preview.status_code, 200, preview.text)
        for content in ('<script>alert(1)</script>', '<image href="https://bad.test/a.png"/>',
                '<foreignObject/>', '<rect x="0" y="0" width="10" height="10" onclick="bad()"/>'):
            svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400">'+content+'</svg>'
            self.assertEqual((await self.request("POST", "/preview", body={"svg": svg})).status_code, 422)
        self.assertEqual((await self.request("POST", body={**self.body, "owner": self.bob})).status_code, 422)
        self.assertEqual((await self.request("POST", "/preview", body={"svg": "a"*131073})).status_code, 422)

    async def test_retrieval_only_enabled_public_or_current_owner(self):
        asset = await self.create()
        brief = VisualBriefV2(visual_role="supplemental", purpose="合成容器示意", needs=[
            {"need_id": "vessel", "name": self.body["title"], "entity_ids": ["vessel"], "capabilities": ["static_illustration"]}])
        with materials.owner_context(self.alice):
            bundle = retrieve(brief)
            self.assertIn("material."+asset["id"], [row["asset_id"] for row in bundle.assets])
            drawing = materials.instantiate("material."+asset["id"], 1, {}, monochrome=True)
            self.assertEqual(drawing.domain, "custom")
            self.assertEqual(drawing.ports, {})
        with materials.owner_context(self.bob):
            self.assertNotIn("material."+asset["id"], [row["asset_id"] for row in retrieve(brief).assets])
            with self.assertRaises(materials.MaterialError): materials.detail(self.bob, asset["id"])
        await self.request("PUT", "/"+asset["id"], body={**self.body, "enabled": False, "base_revision": 1})
        with materials.owner_context(self.alice):
            self.assertNotIn("material."+asset["id"], [row["asset_id"] for row in retrieve(brief).assets])

    async def test_llm_returns_editable_draft_without_writing(self):
        class FakeLLM:
            async def complete(self, **_kwargs):
                return json.dumps({"svg": TEMPLATES[0]["svg"]}), {}
        with patch("app.core.llm_async.get_llm", return_value=FakeLLM()):
            response = await self.request("POST", "/generate", body={"requirement": "合成几何图"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "draft")
        self.assertIn("circle", response.json()["svg"])
        self.assertEqual(materials.visible(self.alice), [])
        self.assertFalse(materials.owner_dir(self.alice).exists())

    async def test_parameterized_preview_save_and_private_roundtrip(self):
        template = TEMPLATES[2]
        response = await self.request("POST", "/preview", body={"svg": template["svg"],
            "parameterization": template["parameterization"], "params": {"left_text": "过滤", "right_text": "回收"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("过滤", response.json()["illustration"]["svg"])
        self.assertIn("过程一", response.json()["svg"])
        asset = await self.create(svg=template["svg"], parameterization=template["parameterization"])
        fetched = await self.request("GET", "/"+asset["id"])
        self.assertEqual(fetched.json()["interface"]["parameters"]["left_text"]["role"], "text")
        self.assertEqual(fetched.json()["parameterization"], asset["parameterization"])
        with materials.owner_context(self.alice):
            card = materials.card(materials.detail(self.alice, asset["id"]))
            self.assertNotIn("过程一", card["fixed_marks"])
        self.assertEqual((await self.request("POST", "/preview", body={"svg": template["svg"],
            "parameterization": template["parameterization"], "params": {"fake": 1}})).status_code, 422)
        self.assertEqual((await self.request("POST", body={**self.body,
            "parameterization": {"parameters": {}, "bindings": [], "code": "eval(1)"}})).status_code, 422)

    async def test_purge_cancels_delayed_save_and_preserves_public(self):
        asset = await self.create()
        public = await self.request("POST", owner=self.admin, body={**self.body, "scope": "public"})
        pid = public.json()["id"]
        real_preview = materials.validate_preview
        def deleting_preview(*args, **kwargs):
            result = real_preview(*args, **kwargs)
            materials.purge(self.alice)
            return result
        with patch.object(materials, "validate_preview", side_effect=deleting_preview):
            response = await self.request("PUT", "/"+asset["id"], body={**self.body, "base_revision": 1})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(materials.owner_dir(self.alice).exists())
        self.assertEqual(materials.detail(self.alice, pid)["scope"], "public")
        with self.assertRaises(materials.MaterialError): materials.purge("public")
