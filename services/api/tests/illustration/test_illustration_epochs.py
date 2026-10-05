"""Owner epoch 磁盘化（ADR-0013 C3）：跨进程 tombstone 与删除竞态补偿。

durable 模式下 API 与 worker 双进程共享同一 epoch——内存 dict 只在单进程
假设下成立。这里固定住磁盘标记的全部行为契约：purge bump、标记跨 rmtree
保留、迟到写被拒且不复活目录、损坏 fail-closed、best-effort 取消级联。
"""
from __future__ import annotations

import unittest
from unittest import mock

from tests.support.storage_sandbox import StorageSandboxTestCase


def _seed_job(owner: str, job_id: str, *, status="queued", epoch=None) -> dict:
    from app.illustration import persistence
    job = {"job_id": job_id, "run_id": "illrun_"+job_id, "status": status,
           "stage": "created", "owner_epoch":
               persistence.epoch(owner) if epoch is None else epoch,
           "question_id": "q_1", "question_revision": 1}
    persistence.write(owner, "jobs", job_id, job)
    return job


class EpochDiskTest(StorageSandboxTestCase):
    def test_epoch_defaults_to_zero(self) -> None:
        from app.illustration import persistence
        self.assertEqual(persistence.epoch("usr_new"), 0)

    def test_purge_bumps_and_marker_survives_rmtree(self) -> None:
        from app.illustration import persistence
        persistence.write("usr_purge", "jobs", "illjob_a", {"job_id": "illjob_a"})
        self.assertEqual(persistence.epoch("usr_purge"), 0)
        persistence.purge("usr_purge")
        self.assertFalse(persistence.owner_dir("usr_purge").exists())
        # 标记目录保留（tombstone），且不被 iter_owners 视作 owner。
        self.assertEqual(persistence.epoch("usr_purge"), 1)
        self.assertNotIn("usr_purge", persistence.iter_owners())
        self.assertNotIn(".epochs", persistence.iter_owners())

    def test_late_write_after_purge_never_resurrects(self) -> None:
        from app.illustration import persistence
        from app.illustration.contracts import IllustrationError
        job = _seed_job("usr_late", "illjob_b")
        old_epoch = job["owner_epoch"]
        persistence.purge("usr_late")
        # 迟到写携带旧 epoch：即使目录被外部重建，写入也必须被拒并补偿。
        persistence.owner_dir("usr_late").mkdir(parents=True, exist_ok=True)
        with self.assertRaises(IllustrationError):
            persistence.write("usr_late", "jobs", "illjob_b",
                              {**job, "status": "failed"},
                              expected_epoch=old_epoch)
        self.assertIsNone(persistence.read("usr_late", "jobs", "illjob_b"))
        self.assertFalse((persistence.owner_dir("usr_late") / "jobs").exists())

    def test_epoch_free_write_after_purge_still_allowed(self) -> None:
        from app.illustration import persistence
        persistence.purge("usr_reuse")
        # epoch 闸只在调用方携带 expected_epoch 时生效（重建账号正常写入）。
        persistence.write("usr_reuse", "sessions", "scene_x", {"session_id": "scene_x"})
        self.assertIsNotNone(persistence.read("usr_reuse", "sessions", "scene_x"))

    def test_write_compensates_when_epoch_races_the_commit(self) -> None:
        """跨进程删除竞态：epoch 在检查后、落盘前失效 → 补偿删除。"""
        from app.illustration import persistence
        from app.illustration.contracts import IllustrationError
        _seed_job("usr_race", "illjob_r")
        calls = {"count": 0}

        def racing_epoch(owner: str) -> int:
            calls["count"] += 1
            return 0 if calls["count"] == 1 else 1  # 检查时 0，提交后已删除

        with mock.patch.object(persistence, "epoch", racing_epoch):
            with self.assertRaises(IllustrationError):
                persistence.write("usr_race", "jobs", "illjob_r",
                                  {"job_id": "illjob_r", "status": "failed"},
                                  expected_epoch=0)
        self.assertEqual(calls["count"], 2)
        self.assertIsNone(persistence.read("usr_race", "jobs", "illjob_r"))

    def test_corrupt_marker_fails_closed(self) -> None:
        from app.illustration import persistence
        marker = persistence._epoch_marker("usr_corrupt")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{not-json", encoding="utf-8")
        self.assertEqual(persistence.epoch("usr_corrupt"), persistence._EPOCH_INVALID)
        # purge 从哨兵继续单调 bump，修复手段就是再次删除。
        persistence.purge("usr_corrupt")
        self.assertEqual(persistence.epoch("usr_corrupt"), persistence._EPOCH_INVALID + 1)


class SettleHelperTest(StorageSandboxTestCase):
    """崩溃结算助手：settle activity 与 worker 启动对账的域内实现。"""

    def test_quiz_settle_marks_interrupted(self) -> None:
        from app.illustration import orchestrator, persistence
        job = _seed_job("usr_settle_q", "illjob_c")
        self.assertEqual(
            orchestrator._settle_interrupted("usr_settle_q", "illjob_c"), "settled")
        current = persistence.read("usr_settle_q", "jobs", "illjob_c")
        self.assertEqual(current["status"], "failed")
        self.assertEqual(current["failure"]["code"], "run_interrupted")
        self.assertTrue(current["failure"]["retryable"])
        # 幂等：终态记录不再重复结算。
        self.assertEqual(
            orchestrator._settle_interrupted("usr_settle_q", "illjob_c"),
            "already-settled")

    def test_quiz_settle_respects_epoch_fence(self) -> None:
        from app.illustration import orchestrator
        job = _seed_job("usr_settle_f", "illjob_d")
        job["owner_epoch"] = 99
        from app.illustration import persistence
        persistence.write("usr_settle_f", "jobs", "illjob_d", job)
        self.assertEqual(
            orchestrator._settle_interrupted("usr_settle_f", "illjob_d"), "fenced")

    def test_scenario_settle_clears_active_job(self) -> None:
        from app.illustration import persistence, scenario
        session = {"session_id": "scene_s", "title": "t", "revision": 0,
                   "active_job_id": "scenejob_x", "turns": [
                       {"turn_id": "turn_1", "message": "m", "mode": "v1",
                        "selected_materials": [], "job_id": "scenejob_x",
                        "status": "queued", "revision": None,
                        "created_at": 0.0, "request_id": None,
                        "source_revision": 0}],
                   "revision_ids": [], "created_at": 0.0, "updated_at": 0.0}
        persistence.write("usr_settle_sc", "sessions", "scene_s", session)
        job = {"job_id": "scenejob_x", "session_id": "scene_s",
               "turn_id": "turn_1", "mode": "v1", "status": "queued",
               "stage": "preparing", "base_revision": 0, "revision": None,
               "artifact_id": None, "failure": None, "created_at": 0.0,
               "updated_at": 0.0,
               "owner_epoch": persistence.epoch("usr_settle_sc"), "attempt": 1}
        persistence.write("usr_settle_sc", "scenario_jobs", "scenejob_x", job)
        self.assertEqual(
            scenario._settle_interrupted("usr_settle_sc", "scene_s", "scenejob_x"),
            "settled")
        settled_job = persistence.read("usr_settle_sc", "scenario_jobs", "scenejob_x")
        self.assertEqual(settled_job["failure"]["code"], "run_interrupted")
        self.assertIsNone(
            persistence.read("usr_settle_sc", "sessions", "scene_s")["active_job_id"])

    def test_scenario_settle_missing_session_raises(self) -> None:
        from app.illustration import scenario
        with self.assertRaises(scenario.SceneError):
            scenario._settle_interrupted("usr_gone", "scene_none", "scenejob_y")


class PurgeCascadeTest(StorageSandboxTestCase):
    def test_purge_cancels_durable_workflows_best_effort(self) -> None:
        from app.illustration import persistence
        _seed_job("usr_cascade", "illjob_e", status="running")
        scenario_job = {"job_id": "scenejob_z", "session_id": "scene_c",
                        "status": "queued"}
        persistence.write("usr_cascade", "scenario_jobs", "scenejob_z", scenario_job)
        # 已终态的 job 不进入取消清单。
        _seed_job("usr_cascade", "illjob_done", status="ready")

        with mock.patch.dict("os.environ", {"TEMPORAL_ADDRESS": "127.0.0.1:7233"}), \
                mock.patch("app.workflows.illustration_common.cancel_owner_workflows") as cancel:
            persistence.purge("usr_cascade")
        cancel.assert_called_once_with(
            "usr_cascade", quiz_ids=["illjob_e"], scenario_ids=["scenejob_z"])
        self.assertEqual(persistence.epoch("usr_cascade"), 1)

    def test_purge_without_temporal_skips_cancel(self) -> None:
        from app.illustration import persistence
        _seed_job("usr_plain", "illjob_f")
        with mock.patch("app.workflows.illustration_common.cancel_owner_workflows") as cancel:
            persistence.purge("usr_plain")
        cancel.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
