"""Chem-lab closed-set DSL validation tests."""
from __future__ import annotations

import unittest

from app.chem_lab import dsl, pack_schema as ps


class DslValidationTest(unittest.TestCase):
    def _rule(self, **overrides) -> ps.RuleDef:
        base = {
            "id": "rule.x",
            "priority": 10,
            "triggers": ["on_pour"],
            "when": [],
            "then": [{"op": "produce", "vessel": "$target", "species": "water", "amount_umol": 1}],
            "limits": {"max_firings_per_evaluation": 4},
            "public_note": {"zh": "x", "en": "x"},
        }
        base.update(overrides)
        return ps.RuleDef.model_validate(base)

    def test_unknown_species_in_condition_rejected(self) -> None:
        rule = self._rule(when=[{"op": "species_at_least", "vessel": "$target",
                                 "species": "unobtanium", "amount_umol": 1}])
        errors = dsl.validate_rule(rule, species_ids={"water"}, equipment_ids=set(),
                                   step_ids=set(), observation_keys=set())
        self.assertTrue(any("unobtanium" in error for error in errors))

    def test_bad_consume_pairs_rejected(self) -> None:
        rule = self._rule(then=[{"op": "consume_min_ratio", "vessel": "$target",
                                 "pairs": [["water", 0]]}])
        errors = dsl.validate_rule(rule, species_ids={"water"}, equipment_ids=set(),
                                   step_ids=set(), observation_keys=set())
        self.assertTrue(errors)

    def test_duplicate_rule_ids_rejected(self) -> None:
        rule = self._rule()
        errors = dsl.validate_rule_set([rule, rule], species_ids={"water"},
                                       equipment_ids=set(), step_ids=set(),
                                       observation_keys=set())
        self.assertTrue(any("重复" in error for error in errors))

    def test_mark_step_requires_declared_step(self) -> None:
        rule = self._rule(then=[{"op": "mark_step", "vessel": "$target", "key": "step.ghost"}])
        errors = dsl.validate_rule(rule, species_ids={"water"}, equipment_ids=set(),
                                   step_ids={"step.real"}, observation_keys=set())
        self.assertTrue(any("step.ghost" in error for error in errors))

    def test_closed_set_ops_rejected_by_schema(self) -> None:
        with self.assertRaises(ValueError):
            ps.RuleCondition.model_validate({"op": "exec_python", "vessel": "$target"})
        with self.assertRaises(ValueError):
            ps.RuleEffect.model_validate({"op": "eval", "vessel": "$target"})


if __name__ == "__main__":
    unittest.main()
