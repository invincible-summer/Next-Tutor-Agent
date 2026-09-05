"""Task launch binding regressions (W4/A12, updatePlan.md §12.2 W4 行).

Pins the server-side launch contract: POST /orchestration/task/{id}/launch
(singular, matching the router's task family; updatePlan.md §9.2 spells the
design target in plural) validates ownership, pre-creates a chat session
carrying task_binding and an
episode; relaunch is idempotent; graded answers in the bound session complete
EXACTLY that task (只更新绑定任务) while a same-concept sibling stays
untouched; manual completion is stamped self_report and writes zero mastery;
the new episodes storage root is swept by account purge and the orphan scan
(AGENTS：新存储根必须被清理路径覆盖).

All storage goes through StorageSandboxTestCase — no production root is ever
touched.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402
from app.agents.learning_orchestration import (  # noqa: E402
    get_orchestration_service)
from app.agents.learning_orchestration import store as orch_store  # noqa: E402
from app.agents.learning_orchestration.schema import (  # noqa: E402
    DailyTask, DailyTaskStatus, TaskKind)
from app.agents.learning_orchestration.task_executor import _day_str  # noqa: E402
from app.core import learning_episodes  # noqa: E402
from app.core.session import load_session, save_session, TutorSession  # noqa: E402
from app.identity import store as id_store  # noqa: E402
from app.identity.models import UserProfile  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402
from app.main import create_app  # noqa: E402

_STEM = "已知数列 a_n=2n，求前 10 项和。"
_MC = {"id": "1", "type": "multiple_choice", "stem": _STEM,
       "options": {"A": "100", "B": "110", "C": "55", "D": "105"},
       "answer": "B", "explanation": "等差数列求和。",
       "knowledge_point": "等差数列", "difficulty": 3}


class LaunchTestBase(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        self.alice = id_store.create_user(
            email="alice@example.com", username="",
            password_hash=hash_password("secret123"),
            profile=UserProfile(name="alice"))
        self.headers = {"Authorization": f"Bearer {create_token(self.alice.id)}"}
        self.client = TestClient(create_app())
        self.svc = get_orchestration_service()

    def _seed_tasks(self, *task_ids: str) -> None:
        """Today's tasks; every one shares the same concept (A12's trap)."""
        day = _day_str(time.time())
        state = orch_store.load_state(self.alice.id)
        state.daily_tasks = [
            DailyTask(id=tid, day=day, concept_id="math.sequence.arithmetic",
                      concept_name="等差数列", kind=TaskKind.PRACTICE,
                      status=DailyTaskStatus.PENDING)
            for tid in task_ids]
        orch_store.save_state(self.alice.id, state)

    def _launch(self, task_id: str):
        return self.client.post(f"/api/v1/orchestration/task/{task_id}/launch",
                                headers=self.headers)

    def _put_quiz_in_session(self, session_id: str) -> None:
        s = load_session(session_id)
        s.quiz_history = [{"topic": "等差数列", "grade": "本科", "difficulty": 3,
                           "questions": [dict(_MC)],
                           "verification": {"mode": "critic",
                                            "answer_verified": True}}]
        save_session(s)

    def _record_answer(self, session_id: str, *, answer: str = "B"):
        return self.client.post(
            "/api/v1/quiz/record",
            json={"stem": _STEM, "q_type": "multiple_choice",
                  "student_answer": answer, "correct_answer": "B",
                  "options": _MC["options"], "explanation": "ignored",
                  "knowledge_point": "等差数列", "session_id": session_id,
                  "difficulty": 3},
            headers=self.headers)

    def _tasks(self) -> dict[str, DailyTask]:
        state = orch_store.load_state(self.alice.id)
        return {t.id: t for t in state.daily_tasks}


class TestLaunchEndpoint(LaunchTestBase):

    def test_launch_creates_episode_session_and_binding(self):
        self._seed_tasks("t_bound")
        r = self._launch("t_bound")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["episode_id"].startswith("ep_"))
        self.assertTrue(body["launch_url"].endswith(body["session_id"]))
        # 预创建的会话携带归属与绑定（判分端点据此归因）。
        s = load_session(body["session_id"])
        self.assertEqual(s.student_id, self.alice.id)
        self.assertEqual(s.task_binding.get("task_id"), "t_bound")
        self.assertEqual(s.task_binding.get("episode_id"), body["episode_id"])
        # 任务被盖上 episode/session；task_launched 事件落盘。
        task = self._tasks()["t_bound"]
        self.assertEqual(task.session_id, body["session_id"])
        self.assertEqual(task.episode_id, body["episode_id"])
        types = [e.type for e in orch_store.read_events(self.alice.id)]
        self.assertIn("task_launched", types)

    def test_relaunch_resumes_same_episode_and_session(self):
        self._seed_tasks("t_bound")
        first = self._launch("t_bound").json()
        second = self._launch("t_bound").json()
        self.assertTrue(second["resumed"])
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(second["episode_id"], first["episode_id"])
        eps = learning_episodes._load_all(self.alice.id)
        self.assertEqual(len(eps), 1)

    def test_launch_unknown_or_foreign_task_404(self):
        self._seed_tasks("t_bound")
        self.assertEqual(self._launch("t_nope").status_code, 404)
        # 另一账号名下不存在的任务同样 404（资源不可见语义）。
        bob = id_store.create_user(
            email="bob@example.com", username="",
            password_hash=hash_password("secret123"),
            profile=UserProfile(name="bob"))
        r = self.client.post("/api/v1/orchestration/task/t_bound/launch",
                             headers={"Authorization":
                                      f"Bearer {create_token(bob.id)}"})
        self.assertEqual(r.status_code, 404)

    def test_launch_completed_task_404(self):
        self._seed_tasks("t_done")
        state = orch_store.load_state(self.alice.id)
        state.daily_tasks[0].status = DailyTaskStatus.COMPLETED
        orch_store.save_state(self.alice.id, state)
        self.assertEqual(self._launch("t_done").status_code, 404)


class TestBoundAttribution(LaunchTestBase):

    def test_bound_evidence_completes_only_the_bound_task(self):
        # A12 核心：同概念两个任务，只有绑定的那个被作答证据完成。
        self._seed_tasks("t_bound", "t_sibling")
        sid = self._launch("t_bound").json()["session_id"]
        self._put_quiz_in_session(sid)
        r = self._record_answer(sid)
        self.assertEqual(r.json()["status"], "ok")
        att = r.json()["attempt_id"]
        tasks = self._tasks()
        bound = tasks["t_bound"]
        self.assertEqual(bound.status, DailyTaskStatus.COMPLETED)
        self.assertEqual(bound.completion_source, "quiz_evidence")
        self.assertEqual(bound.evidence_attempt_id, att)
        self.assertEqual(tasks["t_sibling"].status, DailyTaskStatus.PENDING)
        self.assertEqual(tasks["t_sibling"].completed_at, 0.0)
        # episode 随任务完成推进。
        ep = learning_episodes.get_episode(self.alice.id, bound.episode_id)
        self.assertEqual(ep.status, learning_episodes.EPISODE_COMPLETED)
        # SRS 复习键 = 任务的规范概念 id（不是聊天概念串）。
        state = orch_store.load_state(self.alice.id)
        self.assertIn("math.sequence.arithmetic", state.review_queue)
        self.assertNotIn("等差数列", state.review_queue)

    def test_wrong_answer_still_completes_bound_task(self):
        # 做完任务≠会了：wrong 也是「做了该任务的练习」；达标是 M2 的事。
        self._seed_tasks("t_bound")
        sid = self._launch("t_bound").json()["session_id"]
        self._put_quiz_in_session(sid)
        self._record_answer(sid, answer="A")
        self.assertEqual(self._tasks()["t_bound"].status,
                         DailyTaskStatus.COMPLETED)
        card = orch_store.load_state(self.alice.id).review_queue[
            "math.sequence.arithmetic"]
        self.assertEqual(card.repetitions, 0)  # wrong 复位，间隔不增长

    def test_unbound_session_evidence_does_not_complete(self):
        self._seed_tasks("t_free")
        s = TutorSession(session_id="sess_free", student_id=self.alice.id)
        save_session(s)
        self._put_quiz_in_session("sess_free")
        self._record_answer("sess_free")
        self.assertEqual(self._tasks()["t_free"].status, DailyTaskStatus.PENDING)


class TestManualCompletion(LaunchTestBase):

    def test_manual_complete_self_report_zero_mastery_writes(self):
        self._seed_tasks("t_manual")
        ok, emitted = self.svc.complete_task(self.alice.id, "t_manual")
        self.assertTrue(ok)
        task = self._tasks()["t_manual"]
        self.assertEqual(task.completion_source, "self_report")
        # 手动完成≠掌握：students/ 下不产生 M2 能力档案（.json 主档案）。
        students_dir = orch_store._STUDENTS_DIR
        m2_blob = students_dir / f"{self.alice.id}.json"
        self.assertFalse(m2_blob.exists())

    def test_manual_complete_advances_bound_episode(self):
        self._seed_tasks("t_bound")
        sid = self._launch("t_bound").json()["session_id"]
        self.svc.complete_task(self.alice.id, "t_bound")
        task = self._tasks()["t_bound"]
        ep = learning_episodes.get_episode(self.alice.id, task.episode_id)
        self.assertEqual(ep.status, learning_episodes.EPISODE_COMPLETED)


class TestCapacityFeasibility(LaunchTestBase):
    """W4 容量可行性：确定性按日负载 vs 时间预算（advisory，不阻断）。"""

    def test_capacity_report_flags_overloaded_days(self):
        from app.agents.learning_orchestration import schedule_engine
        day = _day_str(time.time())
        state = orch_store.load_state(self.alice.id)
        state.schedule.daily_minutes = 60
        state.daily_tasks = [
            DailyTask(id="a", day=day, concept_id="c1", estimate_minutes=30),
            DailyTask(id="b", day=day, concept_id="c2", estimate_minutes=40),
            DailyTask(id="c", day="2099-01-01", concept_id="c3",
                      estimate_minutes=15),
        ]
        report = schedule_engine.capacity_report(state)
        self.assertEqual(report["daily_minutes"], 60)
        by_day = {d["day"]: d for d in report["days"]}
        self.assertEqual(by_day[day]["planned_minutes"], 70)
        self.assertTrue(by_day[day]["overload"])
        self.assertEqual(by_day["2099-01-01"]["planned_minutes"], 15)
        self.assertFalse(by_day["2099-01-01"]["overload"])
        self.assertEqual(report["overload_days"], [day])

    def test_plan_summary_carries_capacity(self):
        r = self.client.get("/api/v1/orchestration/plan", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("capacity", body)
        self.assertIn("daily_minutes", body["capacity"])
        self.assertEqual(body["capacity"]["overload_days"], [])

    def test_add_task_response_warns_on_overload(self):
        day = _day_str(time.time())
        r = self.client.post(
            "/api/v1/orchestration/task",
            json={"day": day, "title": "长任务", "estimate_minutes": 90},
            headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # 默认预算 45 分钟 < 90 → advisory 载荷返回但任务照常创建。
        self.assertTrue(body["ok"])
        self.assertEqual(body["capacity_warning"]["day"], day)
        self.assertEqual(body["capacity_warning"]["daily_minutes"], 45)
        self.assertEqual(body["capacity_warning"]["planned_minutes"], 90)

    def test_schedule_patch_recomputes_capacity(self):
        day = _day_str(time.time())
        self.client.post(
            "/api/v1/orchestration/task",
            json={"day": day, "title": "t", "estimate_minutes": 30},
            headers=self.headers)
        r = self.client.patch(
            "/api/v1/orchestration/schedule",
            json={"daily_minutes": 20},
            headers=self.headers)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertIn(day, body["capacity"]["overload_days"])


class TestStorageRegistration(StorageSandboxTestCase):
    """AGENTS 铁律：新存储根必须被账号清除与孤儿扫描覆盖（students/ 前缀
    通用清扫已覆盖，这里钉死证据）。"""

    def test_account_purge_removes_episodes_file(self):
        from app.core.account_data import purge_account
        uid = self._mk_user()
        learning_episodes.create_episode(
            uid, task_id="t1", session_id="s1", concept_id="c1")
        self.assertTrue(learning_episodes._path(uid).exists())
        purge_account(uid)
        self.assertFalse(learning_episodes._path(uid).exists())

    def test_orphan_scan_collects_synthetic_episodes_file(self):
        from app.core.orphan_cleanup import _collect_orphans
        uid = self._mk_user()
        target = learning_episodes._path("rs_synthetic")
        learning_episodes._STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
        out = _collect_orphans([uid])
        self.assertIn(target, out["students"])

    def _mk_user(self) -> str:
        user = id_store.create_user(
            email="purge@example.com", username="",
            password_hash=hash_password("secret123"),
            profile=UserProfile(name="p"))
        return user.id


if __name__ == "__main__":
    unittest.main()
