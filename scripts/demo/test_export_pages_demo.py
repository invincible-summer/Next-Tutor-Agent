"""Fixture contract regressions for the synthetic GitHub Pages demo.

Validates ``fixtures/demo/**`` (the only content source for
``scripts/demo/export_pages_demo.py``): shapes the exporter relies on,
cross-references, and the synthetic-only rule — no real textbook identifiers,
publisher names or provenance keys. Pure file checks: no application storage,
no network, no backend import.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "demo"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_pages_demo import public_payload  # noqa: E402

FORBIDDEN_PROVENANCE_KEYS = {"file_id", "volume_id", "page", "text_sha256",
                             "chunk_id", "source_path", "file_ids"}
REAL_WORLD_MARKERS = ("usr_12e410b4e2", "人民教育出版社", "山东科学技术出版社",
                      "普通高中教科书", "人教A版", "tb_")
VALID_KEY = re.compile(r"^[a-z0-9_-]+$")
VALID_CONCEPT_KEY = re.compile(r"^[a-z0-9_]+$")
VALID_EDGE_TYPE = {"prerequisite", "related", "application", "part_of"}
VALID_STATES = {"supported_in_scope", "emerging", "fragile"}


def load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FixtureContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = {name: load(name) for name in
                  ("profile", "textbooks", "knowledge_graph", "workspaces",
                   "conversations", "notes", "learner_state", "classroom")}
        cls.fx["textbooks"] = cls.fx["textbooks"]["textbooks"]
        cls.fx["workspaces"] = cls.fx["workspaces"]["workspaces"]
        cls.fx["conversations"] = cls.fx["conversations"]["conversations"]

    def test_readme_declares_synthetic_provenance(self):
        text = (FIXTURES / "README.md").read_text(encoding="utf-8")
        self.assertIn("synthetic", text.lower())
        self.assertIn("合成", text)

    def test_no_provenance_keys_or_real_world_markers(self):
        def walk(obj, where):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    self.assertNotIn(k, FORBIDDEN_PROVENANCE_KEYS, where)
                    walk(v, where)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item, where)
            elif isinstance(obj, str):
                for marker in REAL_WORLD_MARKERS:
                    self.assertNotIn(marker, obj, f"{where}: {obj[:60]}")

        for name, data in self.fx.items():
            walk(data, name)

    def test_textbooks_shape(self):
        self.assertGreaterEqual(len(self.fx["textbooks"]), 3)
        for tb in self.fx["textbooks"]:
            for field in ("key", "title", "filename", "subject", "level", "chapters"):
                self.assertIn(field, tb, tb.get("key"))
            self.assertTrue(VALID_CONCEPT_KEY.match(tb["key"]), tb["key"])
            self.assertTrue(tb["filename"].endswith(".txt"), tb["filename"])
            self.assertGreaterEqual(len(tb["chapters"]), 1)
            for chapter in tb["chapters"]:
                self.assertIn("title", chapter)
                self.assertGreaterEqual(len(chapter["concepts"]), 1)
                for concept in chapter["concepts"]:
                    for field in ("key", "name", "definition"):
                        self.assertIn(field, concept,
                                      f"{tb['key']}/{chapter['title']}")
                    self.assertTrue(VALID_CONCEPT_KEY.match(concept["key"]),
                                    concept["key"])
                    self.assertGreaterEqual(len(concept["definition"]), 8)
            for edge in tb.get("edges", []):
                self.assertEqual(set(edge), {"from", "to", "type"}, edge)
                self.assertIn(edge["type"], VALID_EDGE_TYPE, edge)

    def test_knowledge_graph_shape(self):
        graph = self.fx["knowledge_graph"]
        keys = {c["key"] for c in graph["concepts"]}
        self.assertGreaterEqual(len(keys), 6)
        for concept in graph["concepts"]:
            self.assertTrue(VALID_CONCEPT_KEY.match(concept["key"]), concept)
        for edge in graph["edges"]:
            self.assertIn(edge["type"], VALID_EDGE_TYPE, edge)
            self.assertIn(edge["from"], keys, edge)
            self.assertIn(edge["to"], keys, edge)

    def test_cross_references_resolve(self):
        tb_keys = {tb["key"] for tb in self.fx["textbooks"]}
        ws_keys = {ws["key"] for ws in self.fx["workspaces"]}
        concept_index = {
            tb["key"]: {c["key"] for chapter in tb["chapters"]
                        for c in chapter["concepts"]}
            for tb in self.fx["textbooks"]}
        for ws in self.fx["workspaces"]:
            self.assertTrue(VALID_KEY.match(ws["key"]), ws["key"])
            self.assertIn(ws["textbook"], tb_keys, ws["key"])
        for conv in self.fx["conversations"]:
            self.assertIn(conv.get("workspace", ""), ws_keys | {""}, conv["title"])
        for state in self.fx["learner_state"]["concept_states"]:
            self.assertIn(state["textbook"], tb_keys, state)
            self.assertIn(state["concept"], concept_index[state["textbook"]], state)
            self.assertIn(state["state"], VALID_STATES, state)
            self.assertGreaterEqual(len(state["statement"]), 6, state)
        for lesson in self.fx["classroom"]["lessons"]:
            self.assertIn(lesson["workspace"], ws_keys, lesson["topic"])

    def test_conversations_are_self_written_dialogues(self):
        self.assertGreaterEqual(len(self.fx["conversations"]), 4)
        for conv in self.fx["conversations"]:
            messages = conv["messages"]
            self.assertGreaterEqual(len(messages), 4, conv["title"])
            for pair in messages:
                self.assertEqual(len(pair), 2, pair)
                self.assertIn(pair[0], {"user", "assistant"}, pair)
                self.assertGreaterEqual(len(pair[1]), 4, pair)

    def test_notes_shape(self):
        notes = self.fx["notes"]
        folder_ids = {f["id"] for f in notes.get("folders", [])}
        for note in notes["notes"]:
            self.assertIn("title", note)
            self.assertGreaterEqual(len(note["content"]), 20, note["title"])
            self.assertIn(note.get("folder", ""), folder_ids | {""}, note["title"])
        self.assertGreaterEqual(len(notes["notes"]), 4)

    def test_classroom_slides_renderable(self):
        lessons = self.fx["classroom"]["lessons"]
        self.assertGreaterEqual(len(lessons), 2)
        for lesson in lessons:
            slides = lesson["slides"]
            self.assertGreaterEqual(len(slides), 2, lesson["topic"])
            for slide in slides:
                self.assertTrue(slide.get("para"), slide)
                self.assertTrue(slide.get("spoken"), slide)
                self.assertTrue(slide.get("formula") or slide.get("code"),
                                f"slide needs a formula or code block: {slide.get('title')}")

    def test_profile_shape(self):
        profile = self.fx["profile"]
        self.assertIn("name", profile)
        self.assertIn("grade", profile)
        self.assertIsInstance(profile.get("subjects", []), list)


class ExportBoundary(unittest.TestCase):
    def test_hidden_reasoning_is_removed_recursively(self):
        payload = {"messages": [{"content": "Visible teaching", "thinking": "hidden",
                                 "toolCalls": [{"result": {"reasoning_content": "hidden",
                                                           "answer": "42"}}]}],
                   "password_hash": "credential", "api_key": "credential",
                   "trace_ids": ["private"]}
        self.assertEqual(
            public_payload(payload),
            {"messages": [{"content": "Visible teaching",
                           "toolCalls": [{"result": {"answer": "42"}}]}]})


if __name__ == "__main__":
    unittest.main()
