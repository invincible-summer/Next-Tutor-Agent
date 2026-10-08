"""Chem-lab content gate: catalog loads and every shipped replay vector
matches the Python engine bit-for-bit (shared with the TS engine gate)."""
from __future__ import annotations

import json
import unittest

from app.chem_lab.catalog import Catalog
from app.chem_lab.engine import replay


class CatalogLoadTest(unittest.TestCase):
    def test_catalog_loads_six_experiments(self) -> None:
        catalog = Catalog()
        self.assertEqual(len(catalog.experiments), 6)
        self.assertEqual(len(catalog.species), 13)
        self.assertEqual(len(catalog.rules), 3)
        self.assertEqual(len(catalog.equipment), 12)
        self.assertEqual(len(catalog.concepts), 10)

    def test_pack_hash_stable_and_distinct(self) -> None:
        catalog = Catalog()
        hashes = [catalog.pack_hash(catalog.get(key.split("@")[0])) for key in catalog.experiment_keys()]
        self.assertEqual(len(hashes), len(set(hashes)))
        again = Catalog()
        for key in catalog.experiment_keys():
            experiment_id = key.split("@")[0]
            self.assertEqual(catalog.pack_hash(catalog.get(experiment_id)),
                             again.pack_hash(again.get(experiment_id)))

    def test_public_projection_hides_answers_and_rules(self) -> None:
        catalog = Catalog()
        projection = catalog.public_projection("chem.acid_base_indicator")
        self.assertFalse(any(key.startswith("_") for key in projection))
        for prediction in projection["predictions"]:
            for option in prediction["options"]:
                self.assertNotIn("correct", option)


class ReplayVectorTest(unittest.TestCase):
    """Hard gate: every shipped vector must replay identically."""

    def test_all_vectors_verify(self) -> None:
        catalog = Catalog()
        total = 0
        for key in catalog.experiment_keys():
            experiment_id = key.split("@")[0]
            pack = catalog.build_pack(experiment_id)
            files = catalog.vector_files(experiment_id)
            self.assertGreaterEqual(len(files), 4, f"{experiment_id} 至少需要 4 条向量")
            for path in files:
                total += 1
                vector = json.loads(path.read_text(encoding="utf-8"))
                problems = replay.verify_vector(pack, vector)
                self.assertEqual(problems, [], f"{path.name}: {problems}")
        self.assertGreaterEqual(total, 24)


if __name__ == "__main__":
    unittest.main()
