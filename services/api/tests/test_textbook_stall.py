"""教材解析卡死回归（线上形态：构建图谱中·抽取概念 3/3 三小时不动）。

三环根因的回归锚点：
  1. 并行建卷时书级 status 后写者赢：waiting 卷的 ocr_waiting 结算被兄弟卷
     完成结算覆盖回 building → TextbookOCRDeferred 出口必须按卷级状态权威
     重结算（_settle_deferred_book_status），不能再裸 return。
  2. 重试驱动只认书级 ocr_waiting 会漏驱动：_wait_book_terminal 必须按卷级
     waiting + next_retry_at 驱动，与书级 status 无关。
  3. 僵尸 building 没有运行期兜底：停滞看门狗在无在途构建、无计划内重试且
     BUILD_STALL_SECONDS 零写入时结算 graph_failed，释放并发名额。

另覆盖 PDF 探针失败（损坏/0 页）必须落卷级 failed 状态——此前无痕返回让
deferred 结算无据可依。
"""
from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.knowledge import textbook_builder as builder
from app.core import textbook as tb_store
from app.core import textbook_ocr


def _waiting_volume(next_retry_at: float, *, pages: tuple[int, ...] = (105, 107, 109)) -> dict:
    return {
        "status": "waiting",
        "total_pages": 110,
        "target_pages": list(pages),
        "successful_pages": [],
        "pending_pages": list(pages),
        "next_retry_at": next_retry_at,
        "last_error_code": "empty_content",
        "last_error_summary": "多模态 OCR 返回空内容",
    }


class TextbookStallTestCase(StorageSandboxTestCase):
    owner = "stu_stall"

    def setUp(self) -> None:
        super().setUp()
        self._old_poll = builder.QUEUE_POLL_SECONDS
        self._old_stall = builder.BUILD_STALL_SECONDS
        builder.QUEUE_POLL_SECONDS = 0.02
        builder.BUILD_STALL_SECONDS = 0.1
        builder._BUILD_QUEUES.clear()
        builder._BUILD_LOCKS.clear()
        # _LOCKS_GUARD 绑定首个使用它的事件循环：逐测试换新锁避免跨
        # asyncio.run 循环复用报错。
        builder._LOCKS_GUARD = asyncio.Lock()

    def tearDown(self) -> None:
        builder.QUEUE_POLL_SECONDS = self._old_poll
        builder.BUILD_STALL_SECONDS = self._old_stall
        builder._BUILD_QUEUES.clear()
        builder._BUILD_LOCKS.clear()
        builder._LOCKS_GUARD = asyncio.Lock()
        super().tearDown()

    def _group(self, *, status: str = "building",
               volume_states: dict | None = None,
               progress: dict | None = None) -> dict:
        grp = tb_store.create_group(self.owner, file_ids=["v1", "v2", "v3"],
                                    title="政治选择性必修")
        fields: dict = {"progress": progress or
                        {"stage": "chapters", "done": 3, "total": 3}}
        if status:
            fields["status"] = status
        if volume_states is not None:
            fields["ocr_state"] = {"version": 1, "volumes": volume_states}
        tb_store.update_textbook(self.owner, grp["id"], **fields)
        return tb_store.find_textbook(self.owner, grp["id"])

    # -- 1. deferred 出口按卷级状态权威结算 -------------------------------

    def test_settle_waiting_volume_overrides_building(self):
        """竞态定格态（building+chapters 3/3+waiting 卷）→ 必须重结算为
        ocr_waiting，而不是维持 building。"""
        rec = self._group(volume_states={"v2": _waiting_volume(time.time() + 60)})
        builder._settle_deferred_book_status(self.owner, rec["id"], "waiting")
        out = tb_store.find_textbook(self.owner, rec["id"])
        self.assertEqual(out["status"], "ocr_waiting")
        self.assertEqual(out["progress"]["stage"], "ocr_waiting")
        self.assertEqual(out["progress"]["done"], 0)
        self.assertEqual(out["progress"]["total"], 3)
        self.assertIn("多模态", out["error"])

    def test_settle_failed_volume_lands_graph_failed(self):
        vol = _waiting_volume(0.0)
        vol.update({"status": "failed", "pending_pages": [],
                    "last_error_code": "probe_failed",
                    "last_error_summary": "PDF 无法解析或页数为 0，OCR 未执行"})
        rec = self._group(volume_states={"v1": vol})
        builder._settle_deferred_book_status(self.owner, rec["id"], "failed")
        out = tb_store.find_textbook(self.owner, rec["id"])
        self.assertEqual(out["status"], "graph_failed")
        self.assertIn("PDF 无法解析", out["error"])

    def test_settle_without_volume_state_lands_graph_failed(self):
        """deferred 但无任何卷状态（探针无痕失败的旧形态）→ 终态失败，
        绝不留 building。"""
        rec = self._group(volume_states={})
        builder._settle_deferred_book_status(self.owner, rec["id"], "failed")
        out = tb_store.find_textbook(self.owner, rec["id"])
        self.assertEqual(out["status"], "graph_failed")
        self.assertIn("重建图谱", out["error"])

    def test_group_build_deferred_settles_ocr_waiting(self):
        """组构建出口：deferred 后按 ocr_state 落 ocr_waiting（原为静默
        return，记录定格 building+chapters 3/3）。"""
        from app.core.textbook_ocr import TextbookOCRDeferred
        rec = self._group(volume_states={"v1": _waiting_volume(time.time() + 60)})

        async def _deferred_volume(*args, **kwargs):
            raise TextbookOCRDeferred("waiting")

        async def drive():
            with patch.object(builder, "_load_or_extract_group_volume",
                              side_effect=_deferred_volume):
                await builder.build_group_graph(self.owner, rec["id"], None)
        asyncio.run(drive())
        out = tb_store.find_textbook(self.owner, rec["id"])
        self.assertEqual(out["status"], "ocr_waiting")
        self.assertEqual(out["progress"]["stage"], "ocr_waiting")

    def test_group_build_deferred_without_state_lands_graph_failed(self):
        from app.core.textbook_ocr import TextbookOCRDeferred
        rec = self._group(volume_states={})

        async def _deferred_volume(*args, **kwargs):
            raise TextbookOCRDeferred("failed")

        async def drive():
            with patch.object(builder, "_load_or_extract_group_volume",
                              side_effect=_deferred_volume):
                await builder.build_group_graph(self.owner, rec["id"], None)
        asyncio.run(drive())
        out = tb_store.find_textbook(self.owner, rec["id"])
        self.assertEqual(out["status"], "graph_failed")

    # -- 2. 重试驱动按卷级 waiting（与书级 status 解耦）--------------------

    def test_gate_drives_retry_while_building_with_due_waiting_volume(self):
        """书级被覆盖回 building + waiting 卷已到点 → 重试必须驱动（原只认
        书级 ocr_waiting，永不重试）。"""
        rec = self._group(volume_states={"v1": _waiting_volume(time.time() - 10)})
        calls: list[dict] = []

        async def fake_build(student_id, tb_id, **kwargs):
            calls.append(dict(kwargs, _id=tb_id))
            tb_store.update_textbook(
                student_id, tb_id, status="ready",
                progress={"stage": "merge", "done": 1, "total": 1})

        async def _noop(*args, **kwargs):
            return None

        async def drive():
            with patch.object(builder, "run_textbook_build", side_effect=fake_build), \
                 patch.object(textbook_ocr, "_post_ready_rag", side_effect=_noop):
                await asyncio.wait_for(
                    builder._wait_book_terminal(self.owner, rec["id"]), timeout=5)
        asyncio.run(drive())
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["auto_retry"])
        self.assertFalse(calls[0]["force_reextract"])

    def test_gate_dormant_scheduled_retry_survives_watchdog(self):
        """waiting 卷的 next_retry_at 在未来（重试间隔可达小时级）＝计划内
        休眠：看门狗不得误杀。"""
        rec = self._group(status="ocr_waiting",
                          volume_states={"v1": _waiting_volume(time.time() + 3600)})

        async def drive():
            task = asyncio.create_task(
                builder._wait_book_terminal(self.owner, rec["id"]))
            await asyncio.sleep(0.3)  # 远超 BUILD_STALL_SECONDS
            self.assertEqual(
                tb_store.find_textbook(self.owner, rec["id"])["status"],
                "ocr_waiting")
            tb_store.update_textbook(
                self.owner, rec["id"], status="ready",
                progress={"stage": "merge", "done": 1, "total": 1})
            await asyncio.wait_for(task, timeout=5)
        asyncio.run(drive())

    # -- 3. 停滞看门狗 ----------------------------------------------------

    def test_stalled_building_settles_graph_failed(self):
        """僵尸 building（无卷状态、无写入、无在途构建）超窗 → graph_failed
        释放名额，错误信息可引导重建。"""
        rec = self._group(volume_states={})

        async def drive():
            await asyncio.wait_for(
                builder._wait_book_terminal(self.owner, rec["id"]), timeout=5)
        asyncio.run(drive())
        out = tb_store.find_textbook(self.owner, rec["id"])
        self.assertEqual(out["status"], "graph_failed")
        self.assertIn("停滞", out["error"])

    def test_stall_watchdog_spares_active_build(self):
        """看门狗触发窗口内有在途构建（per-book 锁被持有，如手动刷新）：
        不结算——慢 LLM 调用链静默一小时以上是合法的。"""
        rec = self._group(volume_states={})

        async def drive():
            lock = await builder._lock_for(self.owner, rec["id"])
            async with lock:
                task = asyncio.create_task(
                    builder._wait_book_terminal(self.owner, rec["id"]))
                await asyncio.sleep(0.3)  # 超窗但构建在途
                self.assertEqual(
                    tb_store.find_textbook(self.owner, rec["id"])["status"],
                    "building")
                tb_store.update_textbook(
                    self.owner, rec["id"], status="graph_failed", error="构建完成")
                await asyncio.wait_for(task, timeout=5)
        asyncio.run(drive())


class ProbeFailureTestCase(StorageSandboxTestCase):
    """PDF 探针失败（损坏/0 页）：必须落卷级 failed 状态供 deferred 结算。"""

    owner = "stu_probe"

    def test_probe_failure_writes_state_and_settles_failed(self):
        tb = tb_store.create_textbook(self.owner, file_id="badf", title="损坏PDF")

        async def drive():
            return await textbook_ocr.process_textbook_ocr_round(
                self.owner, tb["id"], "badf", b"not a pdf", "")
        result = asyncio.run(drive())
        self.assertEqual(result.status, "failed")
        rec = tb_store.find_textbook(self.owner, tb["id"])
        vol = rec["ocr_state"]["volumes"]["badf"]
        self.assertEqual(vol["status"], "failed")
        self.assertEqual(vol["last_error_code"], "probe_failed")
        self.assertEqual(vol["pending_pages"], [])
        # deferred 出口结算：无 waiting/paused → graph_failed 且带错误摘要
        builder._settle_deferred_book_status(self.owner, tb["id"], "failed")
        out = tb_store.find_textbook(self.owner, tb["id"])
        self.assertEqual(out["status"], "graph_failed")
        self.assertIn("PDF 无法解析", out["error"])


if __name__ == "__main__":
    unittest.main()
