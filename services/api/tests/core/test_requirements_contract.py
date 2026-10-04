"""Dependency-file contract for BM25 production versus optional lanes."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[2]

REQUIREMENT_FILES = (
    "requirements.txt",
    "requirements-test.txt",
    "requirements-vector.txt",
    "requirements-observability.txt",
)


def _requirement_names(filename: str) -> set[str]:
    names: set[str] = set()
    for raw in (BACKEND / filename).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
        names.add(name.lower().replace("_", "-"))
    return names


def _pinned_names() -> set[str]:
    pins: set[str] = set()
    for raw in (BACKEND / "constraints.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, version = line.partition("==")
        if version.strip():
            pins.add(name.lower().replace("_", "-"))
    return pins


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
        self.assertNotIn("-r requirements-vector.txt", test_requirements)
        self.assertIn("httpx2", _requirement_names("requirements-test.txt"))

    def test_enterprise_persistence_lane_lives_in_base_requirements(self):
        names = _requirement_names("requirements.txt")
        self.assertTrue({
            "sqlalchemy", "asyncpg", "greenlet", "alembic", "redis", "cryptography",
        } <= names)

    def test_observability_lane_is_optional_and_pinned(self):
        observability = _requirement_names("requirements-observability.txt")
        self.assertTrue({
            "opentelemetry-sdk",
            "opentelemetry-instrumentation-fastapi",
            "opentelemetry-instrumentation-httpx",
        } <= observability)
        # The base production install stays lean: no OTel packages reachable
        # from requirements.txt, and the test suite never forces the lane.
        self.assertTrue(_requirement_names("requirements.txt").isdisjoint(observability))
        test_requirements = (BACKEND / "requirements-test.txt").read_text(encoding="utf-8")
        self.assertNotIn("-r requirements-observability.txt", test_requirements)
        # The optional lane must still resolve against the pinned set.
        self.assertTrue(observability <= _pinned_names())

    def test_every_direct_requirement_has_an_exact_pin(self):
        pins = _pinned_names()
        for filename in REQUIREMENT_FILES:
            missing = _requirement_names(filename) - pins
            self.assertEqual(set(), missing, f"{filename} declares unpinned packages")

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
            "SQLAlchemy==2.0.54",
            "alembic==1.20.0",
            "asyncpg==0.31.0",
            "redis==8.1.0",
            "cryptography==50.0.2",
        ):
            self.assertIn(expected, constraints)
        self.assertFalse((BACKEND / "requirements.lock").exists())


if __name__ == "__main__":
    unittest.main()
