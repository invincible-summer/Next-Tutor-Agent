"""教材组（多卷合一知识谱系）测试：记录模型 + 统一构建 + 概念索引 + API。

验收：
- 记录：create_group/sanitize 兼容（旧 single 零影响）/textbook_for_file 卷反查命中组。
- 构建：两卷 spec 合并为**一个**图谱 payload；跨卷同名概念合并为一个节点；
  跨卷 prereq 按名成边；章节名带卷前缀；概念索引 chunk_ids 跨卷混合；
  一卷失败另一卷仍成图；rebuild 走归档。
- API：upload 带 group → 单组多卷；group_id 追加；删卷自动重建/删空删组；
  删组级联（卷文件+记录）；public 组非 admin 写 403。

No LLM, no network. Data dirs redirected to temp dirs.
"""
import asyncio
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.core import library as library_mod  # noqa: E402
from app.core import textbook as tb_mod  # noqa: E402
from app.agents.knowledge import store as kgs_mod  # noqa: E402


class _TmpStore:
    """Patch textbook + library + kg storage dirs to a temp root."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def setUp(self):
        self._orig = (tb_mod._LIBRARY_DIR, library_mod._LIBRARY_DIR,
                      kgs_mod._KG_DIR, kgs_mod._CUSTOM_DIR)
        tb_mod._LIBRARY_DIR = self.root / "library"
        library_mod._LIBRARY_DIR = self.root / "library"
        kgs_mod._KG_DIR = self.root / "knowledge"
        kgs_mod._CUSTOM_DIR = self.root / "knowledge" / "custom"

    def tearDown(self):
        (tb_mod._LIBRARY_DIR, library_mod._LIBRARY_DIR,
         kgs_mod._KG_DIR, kgs_mod._CUSTOM_DIR) = self._orig
        self.tmp.cleanup()


def _seed_volume(student_id: str, file_id: str, filename: str, text: str) -> None:
    """Write a volume's extracted text + register in the library index
    （load_library 增量：新建 Library() 会覆盖索引丢掉先前卷）。"""
    from app.core.library import load_library, save_library, library_data_dir
    lib = load_library(student_id)
    (library_data_dir(student_id) / f"{file_id}.txt").write_text(text, encoding="utf-8")
    lib.files.append({"id": file_id, "filename": filename, "folder_id": "",
                      "char_count": len(text), "chunk_count": 1,
                      "orig_ext": "", "kind": "textbook"})
    save_library(lib)


class _GroupMockLLM:
    """两卷 mock：prompt 含「力学」出力学期 spec，含「电磁」出电磁学期 spec。

    跨卷设计：同名概念「速度」两卷都出现（应合并为一个节点）；电磁卷
    「加速度」prereq 引用力学卷的概念名「速度」（应跨卷成边）。
    """

    async def complete(self, messages, **kw):
        prompt = messages[0]["content"]
        if "力学" in prompt:
            return (json.dumps({"subject": "物理", "level": "本科", "chapters": [
                {"name": "第一章", "concepts": [
                    {"name": "速度", "difficulty": 2, "description": "运动快慢"}]}]},
                ensure_ascii=False), {})
        if "电磁" in prompt:
            return (json.dumps({"subject": "物理", "level": "本科", "chapters": [
                {"name": "第一章", "concepts": [
                    {"name": "速度", "difficulty": 2},
                    {"name": "加速度", "difficulty": 3, "prerequisites": ["速度"]}]}]},
                ensure_ascii=False), {})
        return ('{"subject": "物理", "level": "本科", "chapters": []}', {})


class TestGroupRecords(unittest.TestCase):
    def setUp(self):
        self._s = _TmpStore()
        self._s.setUp()

    def tearDown(self):
        self._s.tearDown()

    def test_create_group_and_sanitize_roundtrip(self):
        rec = tb_mod.create_group("stu1", file_ids=["f1", "f2"], title="大物", level="本科")
        self.assertEqual(rec["kind"], "group")
        self.assertEqual(rec["file_id"], "")
        self.assertEqual(rec["file_ids"], ["f1", "f2"])
        self.assertTrue(rec["topic_key"].startswith("tb-"))
        out = tb_mod.find_textbook("stu1", rec["id"])
        self.assertEqual(out["kind"], "group")
        self.assertEqual(out["file_ids"], ["f1", "f2"])
        self.assertEqual(out["group_name"], "大物")
        self.assertEqual(out["group_note"], "")

    def test_single_record_untouched_by_kind_default(self):
        rec = tb_mod.create_textbook("stu1", file_id="f1", title="单本")
        out = tb_mod.find_textbook("stu1", rec["id"])
        self.assertEqual(out["kind"], "single")
        self.assertEqual(out["file_id"], "f1")
        self.assertEqual(out["file_ids"], ["f1"])

    def test_textbook_for_file_hits_group_volume(self):
        grp = tb_mod.create_group("stu1", file_ids=["fa", "fb"], title="大物")
        hit = tb_mod.textbook_for_file("stu1", "fb")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], grp["id"])
        self.assertEqual(hit["title"], "大物")

    def test_add_and_remove_group_files(self):
        grp = tb_mod.create_group("stu1", file_ids=["f1"], title="G")
        tb_mod.add_group_files("stu1", grp["id"], ["f2", "f1", "f3"])  # f1 去重
        out = tb_mod.find_textbook("stu1", grp["id"])
        self.assertEqual(out["file_ids"], ["f1", "f2", "f3"])
        tb_mod.remove_group_file("stu1", grp["id"], "f2")
        out = tb_mod.find_textbook("stu1", grp["id"])
        self.assertEqual(out["file_ids"], ["f1", "f3"])


class TestGroupBuild(unittest.TestCase):
    def setUp(self):
        self._s = _TmpStore()
        self._s.setUp()

    def tearDown(self):
        self._s.tearDown()

    def _build(self, llm=None, file_ids=("f1", "f2"), title="大学物理"):
        from app.agents.knowledge import textbook_builder
        grp = tb_mod.create_group("stu1", file_ids=list(file_ids), title=title)
        asyncio.run(textbook_builder.build_group_graph(
            "stu1", grp["id"], llm or _GroupMockLLM()))
        return grp, tb_mod.find_textbook("stu1", grp["id"])

    def test_two_volumes_merge_into_one_graph(self):
        _seed_volume("stu1", "f1", "力学.pdf", "第一章 力学基础。速度是描述运动快慢的物理量。力学基础内容。" * 3)
        _seed_volume("stu1", "f2", "电磁学.pdf", "第一章 电磁学基础。加速度是速度变化率。电磁学基础内容。" * 3)
        grp, out = self._build()
        self.assertEqual(out["status"], "ready")
        # 仅存一份图谱 payload（组 topic_key）
        payload = kgs_mod.load_custom_graph("stu1", grp["topic_key"])
        self.assertIsNotNone(payload)
        concepts = [n for n in payload["nodes"] if n.get("kind") == "concept"]
        chapters = [n for n in payload["nodes"] if n.get("kind") == "chapter"]
        # 跨卷同名概念「速度」合并为一个节点
        speed = [n for n in concepts if n.get("name") == "速度"]
        self.assertEqual(len(speed), 1)
        # 章节显示名保持干净；卷归属仅存在结构化 metadata 中
        ch_names = {c.get("name") for c in chapters}
        self.assertEqual(ch_names, {"第一章"})
        self.assertEqual(
            {c.get("metadata", {}).get("volume_id") for c in chapters},
            {"f1", "f2"},
        )
        self.assertTrue(all(".pdf" not in n for n in ch_names))
        # 跨卷 prereq 按名成边：速度 → 加速度
        sid = speed[0]["id"]
        accel = next(n for n in concepts if n.get("name") == "加速度")
        pre = [e for e in payload["edges"]
               if str(e.get("type", "")).upper() == "PREREQUISITE"
               and e.get("source") == sid and e.get("target") == accel["id"]]
        self.assertTrue(pre)

    def test_chapter_order_offset_per_volume(self):
        """组级章序：跨卷统一排序键 = 卷序号*1000 + 卷内章序，同名 chapter_order
        不再跨卷冲突（大学物理两分册同有「第一章」实测问题）。"""
        _seed_volume("stu1", "f1", "力学.pdf", "第一章 力学基础。速度是描述运动快慢的物理量。" * 3)
        _seed_volume("stu1", "f2", "电磁学.pdf", "第一章 电磁学基础。加速度是速度变化率。" * 3)
        grp, out = self._build()
        self.assertEqual(out["status"], "ready")
        payload = kgs_mod.load_custom_graph("stu1", grp["topic_key"])
        chapters = [n for n in payload["nodes"] if n.get("kind") == "chapter"]
        by_vol = {c.get("metadata", {}).get("volume_id"): c.get("metadata", {}).get("chapter_order")
                  for c in chapters}
        self.assertEqual(by_vol["f1"], 1001)
        self.assertEqual(by_vol["f2"], 2001)

    def test_concept_index_spans_volumes(self):
        _seed_volume("stu1", "f1", "力学.pdf", "第一章 力学基础。速度是描述运动快慢的物理量。力学基础内容。" * 3)
        _seed_volume("stu1", "f2", "电磁学.pdf", "第一章 电磁学基础。加速度是速度变化率。电磁学基础内容。" * 3)
        grp, out = self._build()
        self.assertEqual(out["status"], "ready")
        idx = kgs_mod.load_concept_chunks("stu1", grp["topic_key"])
        self.assertIsNotNone(idx)
        all_ids = [cid for e in idx["concepts"].values()
                   for cid in (e.get("chunk_ids") or [])]
        # chunk_id = "<file_id>#<idx>"：索引跨两卷文件
        self.assertTrue(any(i.startswith("f1#") for i in all_ids))
        self.assertTrue(any(i.startswith("f2#") for i in all_ids))

    def test_one_volume_fails_other_still_builds(self):
        _seed_volume("stu1", "f1", "力学.pdf", "速度是描述运动快慢的物理量。" * 3)

        class HalfLLM:
            async def complete(self, messages, **kw):
                if "力学" in messages[0]["content"]:
                    return (json.dumps({"subject": "物理", "level": "本科", "chapters": [
                        {"name": "第一章", "concepts": [{"name": "速度", "difficulty": 2}]}]},
                        ensure_ascii=False), {})
                raise RuntimeError("LLM down")

        grp, out = self._build(llm=HalfLLM())
        self.assertEqual(out["status"], "partial")
        self.assertGreaterEqual(out["concept_count"], 1)
        self.assertTrue(any("已跳过" in w for w in out["warnings"]))

    def test_rebuild_replaces_group_without_hidden_archive(self):
        _seed_volume("stu1", "f1", "力学.pdf", "速度是描述运动快慢的物理量。" * 3)
        _seed_volume("stu1", "f2", "电磁学.pdf", "加速度是速度变化率。" * 3)
        grp, out = self._build()
        self.assertEqual(kgs_mod.archive_count("stu1", grp["topic_key"]), 0)
        from app.agents.knowledge import textbook_builder
        asyncio.run(textbook_builder.build_group_graph("stu1", grp["id"], _GroupMockLLM()))
        self.assertEqual(kgs_mod.archive_count("stu1", grp["topic_key"]), 0)
        self.assertEqual(kgs_mod.load_custom_graph("stu1", grp["topic_key"])["version"], 2)


class TestGroupParallelVolumes(unittest.TestCase):
    """组内多卷并行：卷间重叠抽取 + 按卷序后处理（chapter_order / subject
    首卷优先），OCR 延迟卷不阻断兄弟卷（其 spec 落缓存，重试轮复用）。"""

    def setUp(self):
        self._s = _TmpStore()
        self._s.setUp()
        from app.core import textbook_pipeline
        self._pipeline = textbook_pipeline
        self._old_policy = textbook_pipeline._RUNTIME.policy
        textbook_pipeline._RUNTIME.policy = {
            "mode": "parallel", "build_concurrency": 2, "volume_concurrency": 2,
            "llm_concurrency": 4, "updated_at": 0.0, "version": 1}

    def tearDown(self):
        self._pipeline._RUNTIME.policy = self._old_policy
        self._s.tearDown()

    def test_volumes_overlap_and_merge_in_order(self):
        from app.agents.knowledge import textbook_builder
        _seed_volume("stu1", "f1", "力学.pdf", "速度内容。" * 10)
        _seed_volume("stu1", "f2", "电磁.pdf", "加速度内容。" * 10)
        grp = tb_mod.create_group("stu1", file_ids=["f1", "f2"], title="大物")
        active = 0
        peak = 0

        async def fake_volume_spec(student_id, rec_id, file_id, title, level,
                                   llm, warnings, **kw):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.08)
            active -= 1
            concepts = ([{"name": "速度", "difficulty": 2}] if file_id == "f1" else
                        [{"name": "加速度", "difficulty": 3, "prerequisites": ["速度"]}])
            return ("正文", {"subject": "物理甲" if file_id == "f1" else "物理乙",
                            "level": "本科",
                            "chapters": [{"name": "第一章", "concepts": concepts}]})

        async def drive():
            with patch.object(textbook_builder, "_volume_spec",
                              side_effect=fake_volume_spec):
                await textbook_builder.build_group_graph("stu1", grp["id"], object())
        asyncio.run(drive())
        out = tb_mod.find_textbook("stu1", grp["id"])
        self.assertEqual(out["status"], "ready")
        self.assertEqual(peak, 2)  # 两卷确实同时抽取
        self.assertEqual(out["subject"], "物理甲")  # 首卷 subject 优先（按序后处理）
        payload = kgs_mod.load_custom_graph("stu1", grp["topic_key"])
        by_vol = {c.get("metadata", {}).get("volume_id"):
                  c.get("metadata", {}).get("chapter_order")
                  for c in payload["nodes"] if c.get("kind") == "chapter"}
        # 卷序排序键不受并行影响
        self.assertEqual(by_vol, {"f1": 1001, "f2": 2001})

    def test_deferred_volume_lets_siblings_finish(self):
        from app.agents.knowledge import textbook_builder
        from app.core.textbook_ocr import TextbookOCRDeferred
        _seed_volume("stu1", "f1", "力学.pdf", "速度内容。" * 10)
        _seed_volume("stu1", "f2", "电磁.pdf", "加速度内容。" * 10)
        grp = tb_mod.create_group("stu1", file_ids=["f1", "f2"], title="大物")
        seen: list[str] = []

        async def fake_volume_spec(student_id, rec_id, file_id, title, level,
                                   llm, warnings, **kw):
            seen.append(file_id)
            await asyncio.sleep(0.02)
            if file_id == "f2":
                raise TextbookOCRDeferred("waiting")
            return ("正文", {"subject": "物理", "level": "本科",
                            "chapters": [{"name": "第一章",
                                          "concepts": [{"name": "速度", "difficulty": 2}]}]})

        async def drive():
            with patch.object(textbook_builder, "_volume_spec",
                              side_effect=fake_volume_spec):
                await textbook_builder.build_group_graph("stu1", grp["id"], object())
        # 模拟真实流程：OCR 轮 deferred 前已把卷级 waiting 状态落盘
        # （waiting 卷先写 ocr_waiting、兄弟卷完成结算可能覆盖回 building）。
        tb_mod.update_textbook(
            "stu1", grp["id"], status="building",
            ocr_state={"version": 1, "volumes": {
                "f2": {"status": "waiting", "pending_pages": [3],
                       "target_pages": [3], "successful_pages": [],
                       "next_retry_at": time.time() + 60,
                       "last_error_summary": "多模态 OCR 返回空内容"}}})
        asyncio.run(drive())
        # 两卷都被抽取（延迟不阻断兄弟卷）；f1 spec 落缓存供重试轮复用。
        self.assertEqual(sorted(seen), ["f1", "f2"])
        self.assertIsNotNone(kgs_mod.load_volume_spec("stu1", grp["topic_key"], "f1"))
        self.assertIsNone(kgs_mod.load_volume_spec("stu1", grp["topic_key"], "f2"))
        out = tb_mod.find_textbook("stu1", grp["id"])
        # 延迟 ≠ 失败：deferred 出口按卷级状态权威结算为 ocr_waiting
        # （绝不维持被兄弟卷覆盖出的 building，也绝不落 graph_failed）。
        self.assertEqual(out["status"], "ocr_waiting")
        self.assertEqual(out["progress"]["stage"], "ocr_waiting")


class TestHarvestModeContract(unittest.TestCase):
    """刷新模式清理契约：graph_only 不得经原生收割改写 .txt（RAG 事实源与
    索引完全不动）；rag_graph/full_ocr 才允许收割并入表格/插图标记。"""

    def setUp(self):
        self._s = _TmpStore()
        self._s.setUp()

    def tearDown(self):
        self._s.tearDown()

    def _seed_pdf_volume(self, file_id: str = "f1") -> str:
        import fitz
        from app.core.library import library_data_dir
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "Chapter 1 content")
        raw = doc.tobytes()
        doc.close()
        (library_data_dir("stu1") / f"{file_id}.txt").write_text(
            "原文内容，不含任何图表标记。", encoding="utf-8")
        (library_data_dir("stu1") / f"{file_id}.orig.pdf").write_bytes(raw)
        return raw

    def _call_volume_spec(self, skip_harvest: bool):
        import io as _io
        from unittest.mock import AsyncMock, patch
        from app.core import textbook as tb
        from app.agents.knowledge import textbook_builder as b
        from app.core import figure_harvest
        self._seed_pdf_volume()
        lib = tb_mod.load_library("stu1") if hasattr(tb_mod, "load_library") else None
        from app.core.library import load_library, save_library
        lib = load_library("stu1")
        lib.files.append({"id": "f1", "filename": "scan.pdf", "folder_id": "",
                          "char_count": 0, "chunk_count": 0, "orig_ext": ".pdf",
                          "kind": "textbook"})
        save_library(lib)
        rec = tb.create_textbook("stu1", file_id="f1", title="扫描教材")
        warnings: list[str] = []
        fake_harvested = [{"block_type": "table", "text": "| a | b |"}]
        with patch.object(figure_harvest, "harvest_native_blocks",
                          AsyncMock(return_value=fake_harvested)) as harvest, \
                patch.object(figure_harvest, "merge_harvest_into_text",
                             side_effect=lambda text, _h: text + "\n[表|样例]") as merge:
            text, spec = asyncio.run(b._volume_spec(
                "stu1", rec["id"], "f1", "扫描教材", "高中", llm=None,
                warnings=warnings, ocr_parallel=False, skip_ocr=True,
                skip_harvest=skip_harvest))
        return text, harvest, merge

    def test_graph_only_skips_harvest_and_keeps_txt(self):
        from app.core.library import library_data_dir
        text, harvest, merge = self._call_volume_spec(skip_harvest=True)
        harvest.assert_not_awaited()
        merge.assert_not_called()
        on_disk = (library_data_dir("stu1") / "f1.txt").read_text(encoding="utf-8")
        self.assertEqual(on_disk, "原文内容，不含任何图表标记。")
        self.assertNotIn("[表|", text)

    def test_rag_path_harvest_merges_into_txt(self):
        from app.core.library import library_data_dir
        text, harvest, merge = self._call_volume_spec(skip_harvest=False)
        harvest.assert_awaited_once()
        merge.assert_called_once()
        on_disk = (library_data_dir("stu1") / "f1.txt").read_text(encoding="utf-8")
        self.assertIn("[表|样例]", on_disk)


class TestGroupAPI(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        from app.api.v1 import textbook as tb_api
        self._tb_api = tb_api
        self._spawn_patch = patch.object(tb_api, "_spawn_build", lambda *a, **k: None)
        self._spawn_patch.start()
        from app.core.ratelimit import reset_rate_limits
        reset_rate_limits()
        root = Path(self._tmp.name)
        (root / "users").mkdir()
        from app.identity import config as id_config
        from app.identity import store as id_store
        from tests.storage_sandbox import patch_all_storage_roots
        # 完整存储根隔离：旧清单漏了 trash（删除教材组 → 软删除归档直落
        # 生产目录）。
        self._patches = patch_all_storage_roots(root)
        self._patches += [
            patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"),
            patch.object(id_store, "_ACCOUNTS_FILE", root / "users" / "accounts.json"),
        ]
        for p in self._patches[-2:]:
            p.start()
        self.client = TestClient(create_app())
        from app.identity.security import create_token, hash_password
        self._id_store = id_store
        self.admin = id_store.create_user(
            email="admin@example.com", username="",
            password_hash=hash_password("secret123"), role="admin")
        self.user = id_store.create_user(
            email="u1@example.com", username="",
            password_hash=hash_password("secret123"))
        self.admin_h = {"Authorization": f"Bearer {create_token(self.admin.id)}"}
        self.user_h = {"Authorization": f"Bearer {create_token(self.user.id)}"}

    def tearDown(self):
        self._spawn_patch.stop()
        for p in reversed(self._patches):
            p.stop()
        from tests.storage_sandbox import reset_shared_caches
        reset_shared_caches()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._env_old
        self._tmp.cleanup()

    def _upload_group(self, headers, group="大学物理", scope=None,
                      files=(("力学.txt", "力学内容 速度 位移"), ("电磁学.txt", "电磁学内容 加速度"))):
        data = {"level": "本科", "group": group}
        if scope:
            data["scope"] = scope
        return self.client.post(
            "/api/v1/textbooks/upload", data=data,
            files=[("files", (n, io.BytesIO(c.encode() * 10), "text/plain")) for n, c in files],
            headers=headers)

    def test_upload_with_group_creates_single_record(self):
        r = self._upload_group(self.user_h)
        self.assertEqual(r.status_code, 200)
        results = r.json()["results"]
        self.assertEqual(len(results), 2)
        gid = results[0].get("group_id")
        self.assertTrue(gid)
        rec = tb_mod.find_textbook(self.user.id, gid)
        self.assertEqual(rec["kind"], "group")
        self.assertEqual(len(rec["file_ids"]), 2)
        self.assertEqual(rec["title"], "大学物理")
        # list 返回组（kind/volumes 透出）
        lst = self.client.get("/api/v1/textbooks", headers=self.user_h).json()["textbooks"]
        g = next(t for t in lst if t["id"] == gid)
        self.assertEqual(g["kind"], "group")
        self.assertEqual(len(g["volumes"]), 2)

    def test_append_volume_to_group(self):
        gid = self._upload_group(self.user_h).json()["results"][0]["group_id"]
        r = self.client.post(
            "/api/v1/textbooks/upload",
            data={"level": "本科", "group_id": gid},
            files=[("files", ("热学.txt", io.BytesIO("热学内容 熵 温度".encode() * 10), "text/plain"))],
            headers=self.user_h)
        self.assertEqual(r.status_code, 200)
        rec = tb_mod.find_textbook(self.user.id, gid)
        self.assertEqual(len(rec["file_ids"]), 3)
        # 不存在的组 404
        r = self.client.post(
            "/api/v1/textbooks/upload",
            data={"level": "本科", "group_id": "tb_missing"},
            files=[("files", ("x.txt", io.BytesIO(b"x"), "text/plain"))],
            headers=self.user_h)
        self.assertEqual(r.status_code, 404)

    def test_delete_volume_rebuilds_and_empty_deletes_group(self):
        gid = self._upload_group(self.user_h).json()["results"][0]["group_id"]
        rec = tb_mod.find_textbook(self.user.id, gid)
        victim = rec["file_ids"][0]
        r = self.client.delete(f"/api/v1/textbooks/{gid}/volumes/{victim}",
                               headers=self.user_h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "building")  # 剩余卷自动重建
        rec = tb_mod.find_textbook(self.user.id, gid)
        self.assertEqual(len(rec["file_ids"]), 1)
        # 库文件已删
        from app.core.library import load_library
        self.assertIsNone(load_library(self.user.id).find_file(victim))
        # 删空 → 整组删除
        last = rec["file_ids"][0]
        r = self.client.delete(f"/api/v1/textbooks/{gid}/volumes/{last}",
                               headers=self.user_h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("empty"))
        self.assertIsNone(tb_mod.find_textbook(self.user.id, gid))

    def test_delete_group_cascades_volumes(self):
        gid = self._upload_group(self.user_h).json()["results"][0]["group_id"]
        rec = tb_mod.find_textbook(self.user.id, gid)
        fids = list(rec["file_ids"])
        r = self.client.delete(f"/api/v1/textbooks/{gid}", headers=self.user_h)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(tb_mod.find_textbook(self.user.id, gid))
        from app.core.library import load_library
        lib = load_library(self.user.id)
        for fid in fids:
            self.assertIsNone(lib.find_file(fid))

    def test_public_group_write_requires_admin(self):
        gid = self._upload_group(self.admin_h, scope="public").json()["results"][0]["group_id"]
        rec = tb_mod.find_textbook("public", gid)
        self.assertIsNotNone(rec)
        # 普通用户可见（list 合并公用）
        lst = self.client.get("/api/v1/textbooks", headers=self.user_h).json()["textbooks"]
        self.assertIn(gid, {t["id"] for t in lst})
        # 但不可写：PATCH / 删卷 / 删组 全部 403
        self.assertEqual(self.client.patch(
            f"/api/v1/textbooks/{gid}", json={"title": "x"},
            headers=self.user_h).status_code, 403)
        victim = rec["file_ids"][0]
        self.assertEqual(self.client.delete(
            f"/api/v1/textbooks/{gid}/volumes/{victim}",
            headers=self.user_h).status_code, 403)
        self.assertEqual(self.client.delete(
            f"/api/v1/textbooks/{gid}", headers=self.user_h).status_code, 403)


if __name__ == "__main__":
    unittest.main()
