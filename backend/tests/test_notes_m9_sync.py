"""Tests for the M-Notes <-> M9 SM-2 review sync:

1. upsert_review_card creates a card (preserving nothing on first create)
   and is idempotent on scheduling state when re-upserted (e.g. rename).
2. submit_review applies canonical SM-2 (quality 5: interval 1 then 6)
   and appends an srs_review orchestration event.
3. remove_review_card is idempotent.
4. Note review lifecycle: enabling via PATCH registers the card; deleting
   the note (trash) removes it; restoring re-registers it.
5. due reviews: note cards surface in /notes/reviews/due and carry the note
   title as concept_name (they also flow into M9's generic due_cards pool).
6. quality mapping feedback loop: 记得(5) advances, 忘了(1) resets.

Pure local stores, no network. Data dirs are redirected to temp dirs.
"""
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
from app.agents.learning_orchestration import manager as m9_manager  # noqa: E402
from app.agents.learning_orchestration import store as orch_store  # noqa: E402
from app.core import notes as notes_mod  # noqa: E402
from app.core import trash as trash_mod  # noqa: E402


class TestNotesM9Sync(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="notes_m9_")
        root = Path(self._tmp.name)
        self._patches = [
            patch.object(notes_mod, "_NOTES_DIR", root / "notes"),
            patch.object(orch_store, "_STUDENTS_DIR", root / "students"),
            patch.object(trash_mod, "_TRASH_DIR", root / "trash"),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._cleanup)
        self.client = TestClient(create_app())
        self.service = m9_manager.LearningOrchestrationService()

    def _cleanup(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _make_review_note(self, title: str = "温故笔记") -> dict:
        r = self.client.post("/api/v1/notes/notes", json={
            "title": title, "template_id": "review_note"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["note"]

    # -- 1. upsert -----------------------------------------------------------

    def test_upsert_creates_idempotent_card(self):
        card = self.service.upsert_review_card(
            "student_default", concept_id="note:n1", concept_name="标题一")
        self.assertEqual(card["concept_id"], "note:n1")
        self.assertEqual(card["concept_name"], "标题一")
        self.assertEqual(card["repetitions"], 0)
        # advance the card, then re-upsert with a renamed title
        self.service.submit_review("student_default", concept_id="note:n1",
                                   quality=5)
        card2 = self.service.upsert_review_card(
            "student_default", concept_id="note:n1", concept_name="新标题")
        self.assertEqual(card2["concept_name"], "新标题")
        # scheduling state preserved (not reset by the re-upsert)
        self.assertEqual(card2["repetitions"], 1)
        self.assertGreater(card2["next_review"], 0)

    # -- 2. submit ------------------------------------------------------------

    def test_submit_review_canonical_sm2_and_event(self):
        self.service.upsert_review_card(
            "student_default", concept_id="note:n2", concept_name="t")
        first = self.service.submit_review(
            "student_default", concept_id="note:n2", quality=5)
        self.assertEqual(first["interval"], 1)   # repetitions 0 -> 1 day
        self.assertEqual(first["repetitions"], 1)
        second = self.service.submit_review(
            "student_default", concept_id="note:n2", quality=5)
        self.assertEqual(second["interval"], 6)  # repetitions 1 -> 6 days
        events = orch_store.read_events("student_default")
        srs_events = [e for e in events if e.type == "srs_review"]
        self.assertEqual(len(srs_events), 2)
        self.assertEqual(srs_events[0].payload["concept_id"], "note:n2")

    def test_submit_review_failure_resets_streak(self):
        self.service.upsert_review_card(
            "student_default", concept_id="note:n3", concept_name="t")
        self.service.submit_review("student_default", concept_id="note:n3",
                                   quality=5)
        failed = self.service.submit_review(
            "student_default", concept_id="note:n3", quality=1)
        self.assertEqual(failed["repetitions"], 0)
        self.assertEqual(failed["interval"], 1)

    # -- 3. remove -------------------------------------------------------------

    def test_remove_is_idempotent(self):
        self.service.upsert_review_card(
            "student_default", concept_id="note:n4", concept_name="t")
        self.assertTrue(self.service.remove_review_card(
            "student_default", concept_id="note:n4"))
        self.assertFalse(self.service.remove_review_card(
            "student_default", concept_id="note:n4"))
        self.assertEqual(
            self.service.submit_review("student_default",
                                       concept_id="note:n4", quality=5), {})

    # -- 4. note lifecycle --------------------------------------------------------

    def test_note_lifecycle_registers_and_removes_card(self):
        note = self._make_review_note()
        state = orch_store.load_state("student_default")
        self.assertIn(f"note:{note['id']}", state.review_queue)
        self.assertEqual(
            state.review_queue[f"note:{note['id']}"].concept_name, "温故笔记")
        # disable via PATCH removes the card
        r = self.client.patch(f"/api/v1/notes/notes/{note['id']}",
                              json={"review_enabled": False})
        self.assertEqual(r.status_code, 200)
        state = orch_store.load_state("student_default")
        self.assertNotIn(f"note:{note['id']}", state.review_queue)
        # re-enable, then delete via trash -> card removed
        self.client.patch(f"/api/v1/notes/notes/{note['id']}",
                          json={"review_enabled": True})
        self.client.delete(f"/api/v1/notes/notes/{note['id']}")
        state = orch_store.load_state("student_default")
        self.assertNotIn(f"note:{note['id']}", state.review_queue)
        # restore from trash -> card back
        items = self.client.get(
            "/api/v1/trash?resource_type=notes_note").json()["items"]
        self.client.post(f"/api/v1/trash/{items[0]['id']}/restore", json={})
        state = orch_store.load_state("student_default")
        self.assertIn(f"note:{note['id']}", state.review_queue)

    # -- 5. due reviews -------------------------------------------------------------

    def test_due_reviews_join_note_metadata(self):
        note = self._make_review_note("到期温故")
        # force the card overdue
        state = orch_store.load_state("student_default")
        card = state.review_queue[f"note:{note['id']}"]
        card.next_review = time.time() - 60
        state.review_queue[f"note:{note['id']}"] = card
        orch_store.save_state("student_default", state)
        # also add a non-note card that must NOT appear in /notes/reviews/due
        other = m9_manager.srs.create_card("plain_concept",
                                           concept_name="普通概念")
        other.next_review = time.time() - 60
        state.review_queue["plain_concept"] = other
        orch_store.save_state("student_default", state)

        r = self.client.get("/api/v1/notes/reviews/due")
        self.assertEqual(r.status_code, 200)
        due = r.json()["due"]
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["note"]["id"], note["id"])
        self.assertEqual(due[0]["card"]["concept_name"], "到期温故")
        # the generic M9 pool sees both (deep sync into M9 consumers)
        generic = self.service.due_reviews("student_default")
        self.assertEqual(len(generic), 2)

    # -- 6. quality feedback via API ------------------------------------------------

    def test_review_endpoint_advances_and_writes_back(self):
        note = self._make_review_note()
        r = self.client.post(f"/api/v1/notes/notes/{note['id']}/review",
                             json={"quality": 5})
        self.assertEqual(r.status_code, 200)
        review = r.json()["review"]
        self.assertEqual(review["repetitions"], 1)
        self.assertEqual(review["interval"], 1)
        r = self.client.post(f"/api/v1/notes/notes/{note['id']}/review",
                             json={"quality": 5})
        self.assertEqual(r.json()["review"]["interval"], 6)
        r = self.client.post(f"/api/v1/notes/notes/{note['id']}/review",
                             json={"quality": 1})
        self.assertEqual(r.json()["review"]["repetitions"], 0)
        # note index carries the mirrored scheduling fields
        detail = self.client.get(
            f"/api/v1/notes/notes/{note['id']}").json()["note"]
        self.assertEqual(detail["review"]["repetitions"], 0)

    def test_review_rejected_when_not_enabled(self):
        r = self.client.post("/api/v1/notes/notes", json={
            "title": "普通笔记", "template_id": "knowledge_summary"})
        note_id = r.json()["note"]["id"]
        r = self.client.post(f"/api/v1/notes/notes/{note_id}/review",
                             json={"quality": 5})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
