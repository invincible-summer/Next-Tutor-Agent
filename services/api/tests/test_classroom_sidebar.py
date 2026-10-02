"""Sidebar 课堂批量摘要回归（plan.md §3.2.7 / E01）。

workspace_summary 只读可重建索引（不 mkdir、不逐课解析讲稿）；
GET /sidebar 的 payload 带 classroom_summaries 且计入 ETag；
功能关闭时整个键缺省。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.core import classroom_store as store  # noqa: E402
from app.core import workspace as ws_mod  # noqa: E402
from app.schemas import classroom as sc  # noqa: E402
from app.classroom import service as svc  # noqa: E402

OWNER = "usr_sidebarn1"
WS = "ws_side_物理区"


class WorkspaceSummaryTests(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        from unittest.mock import patch
        from app.core.config import settings
        self._enabled = patch.object(settings, "classroom_enabled", True)
        self._enabled.start()
        self.addCleanup(self._enabled.stop)
        ws_mod.save_workspace(ws_mod.Workspace(
            workspace_id=WS, name="物理", student_id=OWNER))

    def test_empty_workspace_returns_zeroes_without_mkdir(self) -> None:
        summary = svc.workspace_summary(OWNER, WS)
        self.assertEqual(summary, {"lesson_count": 0, "active_job_count": 0,
                                   "last_lesson_id": None})
        # 读路径绝不创建课堂目录
        self.assertFalse(store.workspace_root(OWNER, WS).exists())

    def test_counts_active_lessons_and_unfinished_jobs(self) -> None:
        now = store.utcnow()
        ids = []
        for i in range(3):
            lesson_id = store.new_id("les")
            job_id = store.new_id("job")
            store.save_lesson(sc.Lesson(
                lesson_id=lesson_id, owner_id=OWNER, workspace_id=WS,
                title=f"课{i}", created_at=now, updated_at=now,
                latest_job_id=job_id))
            job = sc.GenerationJob(
                job_id=job_id, owner_id=OWNER, workspace_id=WS,
                lesson_id=lesson_id, target_revision=1,
                brief_hash="0" * 64,
                created_at=now, updated_at=now)
            store.save_job(job)
            store.index_upsert_lesson(
                OWNER, WS, store.load_lesson(OWNER, WS, lesson_id), job=job)
            ids.append(lesson_id)
        # 归档一课：不计入 lesson_count
        store.update_lesson(OWNER, WS, ids[0],
                            lambda les: setattr(les, "lifecycle",
                                                sc.LessonLifecycle.archived))
        store.index_upsert_lesson(OWNER, WS,
                                  store.load_lesson(OWNER, WS, ids[0]))

        summary = svc.workspace_summary(OWNER, WS)
        self.assertEqual(summary["lesson_count"], 2)
        self.assertIn(summary["last_lesson_id"], ids[1:])

        # update_job 状态迁移同步索引：两门活跃课的 queued job 计为 active
        self.assertEqual(summary["active_job_count"], 2)
        job2 = store.load_job(OWNER, WS, ids[1],
                              store.load_lesson(OWNER, WS, ids[1]).latest_job_id)
        store.update_job(OWNER, WS, ids[1], job2.job_id,
                         lambda j: setattr(j, "state", sc.JobState.succeeded))
        summary = svc.workspace_summary(OWNER, WS)
        self.assertEqual(summary["active_job_count"], 1)

    def test_sidebar_payload_contains_summaries_and_etag_tracks(self) -> None:
        from app.api.v1.sidebar import sidebar_snapshot

        resp = sidebar_snapshot(student_id=OWNER, if_none_match=None)
        body = resp.body if isinstance(resp.body, dict) else None
        if body is None:  # JSONResponse：解析
            import json as _json
            body = _json.loads(resp.body)
        self.assertIn("classroom_summaries", body)
        self.assertEqual(body["classroom_summaries"][WS]["lesson_count"], 0)

        etag1 = resp.headers["ETag"]
        lesson_id = store.new_id("les")
        now = store.utcnow()
        store.save_lesson(sc.Lesson(
            lesson_id=lesson_id, owner_id=OWNER, workspace_id=WS,
            title="新课", created_at=now, updated_at=now))
        store.index_upsert_lesson(OWNER, WS,
                                  store.load_lesson(OWNER, WS, lesson_id))
        resp2 = sidebar_snapshot(student_id=OWNER, if_none_match=None)
        import json as _json
        body2 = _json.loads(resp2.body)
        self.assertEqual(body2["classroom_summaries"][WS]["lesson_count"], 1)
        self.assertNotEqual(resp2.headers["ETag"], etag1)

    def test_sidebar_omits_summaries_when_classroom_disabled(self) -> None:
        from unittest.mock import patch
        from app.api.v1.sidebar import sidebar_snapshot

        with patch("app.core.config.settings.classroom_enabled", False):
            resp = sidebar_snapshot(student_id=OWNER, if_none_match=None)
            import json as _json
            body = _json.loads(resp.body)
            self.assertNotIn("classroom_summaries", body)


if __name__ == "__main__":
    unittest.main()
