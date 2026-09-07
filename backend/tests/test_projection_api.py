"""Tests for the read-only projection APIs (M2/M3 student, M5 knowledge, M6 memory).

Covers every endpoint's ok path (seeded fixtures in a temp students/ dir,
mirroring how the M2-M8 tests isolate state), the disabled contract (toggle
env monkeypatched), the not_found/empty degradations, and the defensive
contract (hostile extra query params are ignored, never a 500). Also asserts
the read-only guarantee: no fixture file changes after calling every endpoint.
"""
import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.agents.student_model import manager as sm_manager  # noqa: E402
from app.agents.knowledge import manager as kn_manager  # noqa: E402
from tests.storage_sandbox import patch_all_storage_roots, reset_shared_caches  # noqa: E402

# M0 identity threading (DESIGN §1.5): projection endpoints resolve the
# student namespace from Depends(resolve_student_id) -- guest mode always
# yields DEFAULT_STUDENT_ID and the legacy student_id query param is dead.
# Fixtures therefore seed under the default id.
SID = "student_default"

_BLOB = {
    "profile": {
        "id": SID, "grade": "高中", "subjects": ["物理"],
        "learning_style": {"preference": "balanced", "explanation_depth": "adaptive"},
        "goals": ["期末物理上90"], "weak_points": [], "strong_points": [],
        "created_at": 1000.0, "updated_at": 2000.0, "last_active": 2000.0,
        "events_processed": 5,
    },
    "mastery": {
        "physics.fluid.buoyancy": {
            "skill_id": "physics.fluid.buoyancy", "p_known": 0.42,
            "attempts": 3, "correct": 1, "last_review": 1500.0,
            "mistakes": ["公式记错"],
            "params": {"L0": 0.1, "T": 0.1, "S": 0.1, "G": 0.25},
        },
        "general.auto.惯性": {
            "skill_id": "general.auto.惯性", "p_known": 0.8,
            "attempts": 2, "correct": 2, "last_review": 1600.0, "mistakes": [],
            "params": {"L0": 0.1, "T": 0.1, "S": 0.1, "G": 0.25},
        },
    },
    "memory": {
        "physics.fluid.buoyancy": {
            "skill_id": "physics.fluid.buoyancy", "concept": "浮力",
            "state": "partial", "evidence": [], "misconceptions": [],
            "mistake_types": [], "attempts": 3, "correct": 1,
            "last_review": 1500.0,
        },
    },
}

_TEACHING = {
    "concepts": {
        "浮力": [
            {"mode": "introduction", "outcome": "engaged", "ts": 1100.0, "note": "浮力"},
            {"mode": "explanation", "outcome": "correct", "ts": 1200.0, "note": "浮力"},
        ],
    },
    "updated_at": 1200.0,
}

_SEMANTIC = {
    "semantic": [
        {"id": "sf_1", "fact": "物理基础薄弱", "category": "context",
         "confidence": 0.6, "evidence_count": 2, "created_ts": 1000.0,
         "updated_ts": 1100.0, "superseded_by": "sf_2",
         "scope": "global", "subject": ""},
        {"id": "sf_2", "fact": "物理基础中等", "category": "context",
         "confidence": 0.7, "evidence_count": 1, "created_ts": 1100.0,
         "updated_ts": 1100.0, "superseded_by": None,
         "scope": "global", "subject": ""},
    ],
    "procedural": [
        {"strategy": "remediation", "subject": "物理", "success_rate": 0.8,
         "trials": 5, "last_used_ts": 1200.0, "scope": "subject"},
    ],
    "updated_at": 1200.0,
    "consolidation": {"events_since_last": 0, "last_ts": 0.0},
}

_EPISODES = [
    {"id": "ep_1", "ts": 100.0, "summary": "学习了「浮力」（物理）",
     "event_type": "concept_taught", "concept": "浮力", "subject": "物理",
     "score": None, "emotion": "", "importance": 0.3, "scope": "concept"},
    {"id": "ep_2", "ts": 200.0, "summary": "完成了浮力测试，答错",
     "event_type": "quiz_graded", "concept": "浮力", "subject": "物理",
     "score": 0.2, "emotion": "frustrated", "importance": 0.8, "scope": "concept"},
    {"id": "ep_3", "ts": 300.0, "summary": "学习了「惯性」（物理）",
     "event_type": "concept_taught", "concept": "惯性", "subject": "物理",
     "score": None, "emotion": "", "importance": 0.3, "scope": "concept"},
]


def _seed(d: Path) -> None:
    (d / f"{SID}.json").write_text(
        json.dumps(_BLOB, ensure_ascii=False), encoding="utf-8")
    (d / f"{SID}.teaching.json").write_text(
        json.dumps(_TEACHING, ensure_ascii=False), encoding="utf-8")
    (d / f"{SID}.semantic.json").write_text(
        json.dumps(_SEMANTIC, ensure_ascii=False), encoding="utf-8")
    (d / f"{SID}.episodes.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in _EPISODES),
        encoding="utf-8")


def _seed_textbook_graph(d: Path) -> None:
    """P6-A2 起图谱只来自教材：投影测试的图节点改由教材图谱 fixture 提供
    （语义同旧 seed 的浮力节点：掌握度/教学日志/记忆 fixture 都锚定它）。"""
    from app.agents.knowledge import store as kn_store_mod
    payload = {
        "topic": "初中物理教材", "topic_key": "tb-proj", "subject": "物理",
        "level": "初中", "source": "textbook:f-proj",
        "nodes": [
            {"id": "ch.proj.buoyancy", "name": "浮力单元", "subject": "物理",
             "level": "初中", "difficulty": 1, "kind": "chapter",
             "origin": "material"},
            {"id": "physics.fluid.buoyancy", "name": "浮力", "subject": "物理",
             "level": "初中", "difficulty": 2, "kind": "concept",
             "origin": "material", "aliases": [], "description": "液体对物体的向上托力"},
        ],
        "edges": [{"source": "physics.fluid.buoyancy",
                   "target": "ch.proj.buoyancy", "type": "part_of"}],
        "contents": [{"concept_id": "physics.fluid.buoyancy",
                      "definition": "液体对浸在其中物体的向上托力。",
                      "formula": "", "example": "", "exercise_hint": "",
                      "source": "textbook:f-proj"}],
    }
    kn_store_mod.save_custom_graph(SID, "tb-proj", payload)


class _ProjectionTestBase(unittest.TestCase):
    """Shared isolation（AGENTS.md 测试规范）：全部存储根经
    patch_all_storage_roots 重定向进 TemporaryDirectory（tearDown 回收，
    不用裸 mkdtemp），fixture 落在 students/ 子目录；M2/M5 单例缓存每用例
    重置。访客模式（不设 AUTH_MODE）——resolve_student_id 恒为
    student_default，fixture 即锚定该 id。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="proj_api_test_")
        self._dir = Path(self._tmp.name)
        (self._dir / "students").mkdir()
        self._patches = patch_all_storage_roots(self._dir)
        sm_manager._CACHE.clear()
        kn_manager._INSTANCE = None
        _seed(self._dir / "students")
        _seed_textbook_graph(self._dir)
        self.client = TestClient(create_app())

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        sm_manager._CACHE.clear()
        kn_manager._INSTANCE = None
        reset_shared_caches()
        self._tmp.cleanup()

    def get(self, path, **kw):
        r = self.client.get(path, **kw)
        self.assertEqual(r.status_code, 200, f"{path} -> {r.status_code}: {r.text[:200]}")
        return r.json()


# ---------------------------------------------------------------------------
# /student (M2/M3 projections)
# ---------------------------------------------------------------------------

class TestStudentAPI(_ProjectionTestBase):

    def test_profile_ok(self):
        body = self.get("/api/v1/student/profile")
        self.assertEqual(body["status"], "ok")
        p = body["profile"]
        self.assertEqual(p["id"], SID)
        self.assertEqual(p["grade"], "高中")
        self.assertEqual(p["subjects"], ["物理"])
        self.assertEqual(p["goals"], ["期末物理上90"])
        self.assertEqual(p["events_processed"], 5)
        for k in ("learning_style", "weak_points", "strong_points",
                  "created_at", "updated_at", "last_active"):
            self.assertIn(k, p)

    def test_profile_disabled(self):
        with patch.dict(os.environ, {"STUDENT_MODEL_MODE": "0"}):
            body = self.get("/api/v1/student/profile")
        self.assertEqual(body, {"status": "disabled"})

    def test_profile_empty_when_no_data(self):
        # No seeded profile file -> the empty contract (not a 404/500).
        (self._dir / "students" / f"{SID}.json").unlink()
        sm_manager._CACHE.clear()
        body = self.get("/api/v1/student/profile")
        self.assertEqual(body, {"status": "empty", "profile": None})

    def test_mastery_ok(self):
        body = self.get("/api/v1/student/mastery")
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["count"], 2)
        by_id = {s["skill_id"]: s for s in body["skills"]}
        b = by_id["physics.fluid.buoyancy"]
        self.assertEqual(b["concept"], "浮力")          # joined from memory
        self.assertEqual(b["state"], "partial")          # joined from memory
        self.assertEqual(b["subject"], "physics")        # first dotted segment
        self.assertAlmostEqual(b["p_known"], 0.42)
        self.assertEqual(b["attempts"], 3)
        self.assertEqual(b["correct"], 1)
        self.assertEqual(b["last_review"], 1500.0)
        self.assertEqual(b["mistakes"], ["公式记错"])
        # no memory record -> state falls back to "unknown"
        self.assertEqual(by_id["general.auto.惯性"]["state"], "unknown")
        # weakest first
        self.assertEqual(body["skills"][0]["skill_id"], "physics.fluid.buoyancy")

    def test_mastery_disabled(self):
        with patch.dict(os.environ, {"STUDENT_MODEL_MODE": "0"}):
            body = self.get("/api/v1/student/mastery")
        self.assertEqual(body, {"status": "disabled"})

    def test_teaching_log_ok(self):
        body = self.get("/api/v1/student/teaching-log")
        self.assertEqual(body["status"], "ok")
        c = body["concepts"]["浮力"]
        self.assertEqual(c["current_mode"], "explanation")
        self.assertEqual(c["current_outcome"], "correct")
        self.assertEqual(c["last_ts"], 1200.0)
        self.assertEqual(len(c["entries"]), 2)
        # newest first
        self.assertEqual(c["entries"][0]["ts"], 1200.0)
        self.assertEqual(c["entries"][0]["mode"], "explanation")

    def test_teaching_log_limit_per_concept(self):
        body = self.get("/api/v1/student/teaching-log",
                        params={"limit_per_concept": 1})
        self.assertEqual(len(body["concepts"]["浮力"]["entries"]), 1)

    def test_teaching_log_disabled(self):
        with patch.dict(os.environ, {"TEACHING_ENGINE_MODE": "0"}):
            body = self.get("/api/v1/student/teaching-log")
        self.assertEqual(body, {"status": "disabled"})

    def test_learning_path_ok(self):
        body = self.get("/api/v1/student/learning-path")
        self.assertEqual(body["status"], "ok")
        self.assertIsInstance(body["next_to_learn"], list)
        self.assertIsInstance(body["review"], list)
        self.assertTrue(1 <= body["difficulty"] <= 5)
        # buoyancy: middling mastery + very stale last_review -> review node
        review_ids = [n["skill_id"] for n in body["review"]]
        self.assertIn("physics.fluid.buoyancy", review_ids)
        for n in body["next_to_learn"] + body["review"]:
            for k in ("name", "skill_id", "difficulty", "reason"):
                self.assertIn(k, n)

    def test_learning_path_disabled(self):
        with patch.dict(os.environ, {"TEACHING_ENGINE_MODE": "0"}):
            body = self.get("/api/v1/student/learning-path")
        self.assertEqual(body, {"status": "disabled"})


# ---------------------------------------------------------------------------
# /knowledge (M5 projections)
# ---------------------------------------------------------------------------

class TestKnowledgeAPI(_ProjectionTestBase):

    def test_graph_ok(self):
        body = self.get("/api/v1/knowledge/graph")
        self.assertEqual(body["status"], "ok")
        self.assertGreater(len(body["nodes"]), 0)
        self.assertGreater(len(body["edges"]), 0)
        n0 = body["nodes"][0]
        for k in ("id", "name", "subject", "level", "difficulty",
                  "description", "aliases", "common_errors", "mastery"):
            self.assertIn(k, n0)
        e0 = body["edges"][0]
        self.assertEqual(set(e0.keys()), {"from", "to", "type"})
        # isolated learned-edges file -> seed only
        self.assertEqual(body["learned_edges"], 0)
        # mastery overlay joined for the seeded skill
        by_id = {n["id"]: n for n in body["nodes"]}
        m = by_id["physics.fluid.buoyancy"]["mastery"]
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m["p_known"], 0.42)
        self.assertEqual(m["state"], "partial")

    def test_graph_mastery_null_when_m2_off(self):
        with patch.dict(os.environ, {"STUDENT_MODEL_MODE": "0"}):
            body = self.get("/api/v1/knowledge/graph")
        self.assertEqual(body["status"], "ok")
        self.assertTrue(all(n["mastery"] is None for n in body["nodes"]))

    def test_graph_disabled(self):
        with patch.dict(os.environ, {"KNOWLEDGE_INTELLIGENCE_MODE": "0"}):
            body = self.get("/api/v1/knowledge/graph")
        self.assertEqual(body, {"status": "disabled"})

    def test_concept_ok(self):
        body = self.get("/api/v1/knowledge/concepts/physics.fluid.buoyancy")
        self.assertEqual(body["status"], "ok")
        c = body["concept"]
        self.assertEqual(c["id"], "physics.fluid.buoyancy")
        self.assertEqual(c["name"], "浮力")
        self.assertIn("content", c)
        self.assertIn("definition", c["content"])
        for k in ("prerequisites", "unlocks", "related",
                  "applications", "misconceptions"):
            self.assertIn(k, body["edges"])
        self.assertAlmostEqual(body["mastery"]["p_known"], 0.42)
        # teaching log joined via the display name ("浮力")
        self.assertEqual(len(body["teaching_log"]), 2)
        self.assertEqual(body["teaching_log"][0]["ts"], 1200.0)  # newest first
        # M6 episodes mentioning 浮力 (2 of the 3 seeded)
        self.assertEqual(len(body["memories"]), 2)
        self.assertEqual(body["memories"][0]["id"], "ep_2")      # newest first

    def test_concept_part_of_parents_children(self):
        # PART_OF 上下行（课文/单元详情面板的数据源）：概念 → 所属章节；章节 → 成员
        body = self.get("/api/v1/knowledge/concepts/physics.fluid.buoyancy")
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["edges"]["parents"],
                         [{"id": "ch.proj.buoyancy", "name": "浮力单元"}])
        self.assertEqual(body["edges"]["children"], [])
        ch = self.get("/api/v1/knowledge/concepts/ch.proj.buoyancy")
        self.assertEqual(ch["status"], "ok")
        self.assertEqual(ch["edges"]["parents"], [])
        self.assertEqual(ch["edges"]["children"],
                         [{"id": "physics.fluid.buoyancy", "name": "浮力"}])

    def test_concept_fuzzy_name(self):
        body = self.get("/api/v1/knowledge/concepts/浮力")
        self.assertEqual(body["status"], "ok")
        # P6-A2：图谱来自教材 fixture，同名命中确定
        self.assertEqual(body["concept"]["id"], "physics.fluid.buoyancy")

    def test_concept_not_found(self):
        body = self.get("/api/v1/knowledge/concepts/nope.nothing.here")
        self.assertEqual(body["status"], "not_found")
        self.assertIsNone(body["concept"])

    def test_concept_disabled(self):
        with patch.dict(os.environ, {"KNOWLEDGE_INTELLIGENCE_MODE": "0"}):
            body = self.get("/api/v1/knowledge/concepts/physics.fluid.buoyancy")
        self.assertEqual(body, {"status": "disabled"})


# ---------------------------------------------------------------------------
# /memory (M6 projections)
# ---------------------------------------------------------------------------

class TestMemoryAPI(_ProjectionTestBase):

    def test_episodes_ok(self):
        body = self.get("/api/v1/memory/episodes")
        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["episodes"]), 3)
        self.assertFalse(body["has_more"])
        # newest first
        self.assertEqual([e["id"] for e in body["episodes"]],
                         ["ep_3", "ep_2", "ep_1"])

    def test_episodes_pagination(self):
        body = self.get("/api/v1/memory/episodes", params={"limit": 2})
        self.assertEqual(len(body["episodes"]), 2)
        self.assertTrue(body["has_more"])
        oldest_ts = body["episodes"][-1]["ts"]
        page2 = self.get("/api/v1/memory/episodes",
                         params={"limit": 2, "before": oldest_ts})
        self.assertEqual([e["id"] for e in page2["episodes"]], ["ep_1"])
        self.assertFalse(page2["has_more"])

    def test_episodes_empty_when_no_data(self):
        # No seeded episodes file -> ok with an empty page (not a 500).
        (self._dir / "students" / f"{SID}.episodes.jsonl").unlink()
        body = self.get("/api/v1/memory/episodes")
        self.assertEqual(body, {"status": "ok", "episodes": [], "has_more": False})

    def test_episodes_disabled(self):
        with patch.dict(os.environ, {"MEMORY_INTELLIGENCE_MODE": "0"}):
            body = self.get("/api/v1/memory/episodes")
        self.assertEqual(body, {"status": "disabled"})

    def test_semantic_ok(self):
        body = self.get("/api/v1/memory/semantic")
        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["facts"]), 2)
        by_id = {f["id"]: f for f in body["facts"]}
        # superseded facts stay visible with their audit pointer
        self.assertEqual(by_id["sf_1"]["superseded_by"], "sf_2")
        self.assertIsNone(by_id["sf_2"]["superseded_by"])

    def test_semantic_disabled(self):
        with patch.dict(os.environ, {"MEMORY_INTELLIGENCE_MODE": "0"}):
            body = self.get("/api/v1/memory/semantic")
        self.assertEqual(body, {"status": "disabled"})

    def test_procedural_ok(self):
        body = self.get("/api/v1/memory/procedural")
        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["strategies"]), 1)
        s = body["strategies"][0]
        self.assertEqual(s["strategy"], "remediation")
        self.assertEqual(s["subject"], "物理")
        self.assertEqual(s["scope"], "subject")
        self.assertAlmostEqual(s["success_rate"], 0.8)
        self.assertEqual(s["trials"], 5)
        self.assertEqual(s["last_used_ts"], 1200.0)

    def test_procedural_disabled(self):
        with patch.dict(os.environ, {"MEMORY_INTELLIGENCE_MODE": "0"}):
            body = self.get("/api/v1/memory/procedural")
        self.assertEqual(body, {"status": "disabled"})


# ---------------------------------------------------------------------------
# cross-cutting: defensive + read-only contracts
# ---------------------------------------------------------------------------

_ALL_ENDPOINTS = [
    "/api/v1/student/profile",
    "/api/v1/student/mastery",
    "/api/v1/student/teaching-log",
    "/api/v1/student/learning-path",
    "/api/v1/knowledge/graph",
    "/api/v1/knowledge/concepts/physics.fluid.buoyancy",
    "/api/v1/memory/episodes",
    "/api/v1/memory/semantic",
    "/api/v1/memory/procedural",
]


class TestDefensiveContract(_ProjectionTestBase):

    def test_extra_query_params_never_500(self):
        # The student namespace comes from the JWT/default, never from the
        # client. Unknown or hostile query params must be ignored, not 500.
        for bad in ("../etc/passwd", "bad id with spaces", "😀", "x" * 500):
            for path in _ALL_ENDPOINTS:
                r = self.client.get(path, params={"student_id": bad})
                self.assertEqual(r.status_code, 200,
                                 f"{path} with {bad!r} -> {r.status_code}")
                self.assertIn("status", r.json())

    def test_endpoints_are_read_only(self):
        # fixture 落在 students/ 子目录（与 sm_store._STUDENTS_DIR 的沙箱口径一致）
        files = [f for f in (self._dir / "students").iterdir() if f.is_file()]
        before = {f.name: f.read_bytes() for f in files}
        for path in _ALL_ENDPOINTS:
            self.get(path)
        files = [f for f in (self._dir / "students").iterdir() if f.is_file()]
        after = {f.name: f.read_bytes() for f in files}
        self.assertEqual(before, after, "projection endpoints must not write")


if __name__ == "__main__":
    unittest.main()
