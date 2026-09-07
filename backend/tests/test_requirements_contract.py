"""Dependency-file contract for BM25 production versus optional vector lanes."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _requirement_names(filename: str) -> set[str]:
    names: set[str] = set()
    for raw in (BACKEND / filename).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
        names.add(name.lower().replace("_", "-"))
    return names


class RequirementsContractTest(unittest.TestCase):
    def test_bm25_base_excludes_vector_and_model_runtime(self):
        text = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("-c constraints.txt", text)
        names = _requirement_names("requirements.txt")
        self.assertTrue({"fastapi", "openai", "pillow", "pytesseract"} <= names)
        self.assertTrue(
            names.isdisjoint({
                "chromadb", "numpy", "torch", "transformers",
                "sentence-transformers",
            })
        )

    def test_optional_files_own_vector_and_local_model_dependencies(self):
        self.assertTrue({"chromadb", "numpy"} <= _requirement_names("requirements-vector.txt"))
        # The bundled local RAG embedding-model runtime was removed: only the
        # model-agnostic interface remains, so no requirement file may (re)pin
        # sentence-transformers, transformers, scikit-learn, or torch.
        self.assertFalse((BACKEND / "requirements-local-rag.txt").exists())
        self.assertFalse((BACKEND / "requirements-cpu.txt").exists())
        constraints = (BACKEND / "constraints.txt").read_text(encoding="utf-8")
        for banned in ("sentence-transformers==", "transformers==", "scikit-learn==", "torch=="):
            self.assertNotIn(banned, constraints)
        test_requirements = (BACKEND / "requirements-test.txt").read_text(encoding="utf-8")
        self.assertIn("-r requirements-vector.txt", test_requirements)

    def test_constraints_pin_python311_production_set(self):
        constraints = (BACKEND / "constraints.txt").read_text(encoding="utf-8")
        for expected in (
            "fastapi==0.140.0",
            "openai==2.48.0",
            "pymupdf==1.28.0",
            "pillow==12.3.0",
            "pytesseract==0.3.13",
            "chromadb==1.5.9",
            "numpy==2.4.6",
        ):
            self.assertIn(expected, constraints)
        self.assertFalse((BACKEND / "requirements.lock").exists())


if __name__ == "__main__":
    unittest.main()
