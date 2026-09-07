"""Unit tests for the skill graph (DAG traversal + fuzzy match)."""
import unittest
from app.agents.student_model.skill_graph import SkillGraph, SkillNode


class TestSkillGraph(unittest.TestCase):
    def setUp(self):
        self.g = SkillGraph()

    def test_fuzzy_match_exact_and_synonym(self):
        self.assertEqual(self.g.match_concept("导数").id, "math.calculus.derivative")
        self.assertEqual(self.g.match_concept("牛顿第二定律").id, "physics.dynamics.newton_second")
        self.assertEqual(self.g.match_concept("浮力").id, "physics.fluid.buoyancy")
        self.assertIsNone(self.g.match_concept("完全不相关的概念xyz"))

    def test_transitive_prerequisites(self):
        # integral -> derivative -> {monotonicity, limit} -> function.definition
        prereqs = self.g.prerequisites_of("math.calculus.integral")
        self.assertIn("math.calculus.derivative", prereqs)
        self.assertIn("math.function.definition", prereqs)

    def test_unmet_prerequisites_shrinks_with_mastery(self):
        cold = self.g.unmet_prerequisites("math.calculus.derivative", {})
        cold_ids = {n.id for n in cold}
        self.assertEqual(cold_ids, {"math.function.monotonicity", "math.calculus.limit",
                                    "math.function.definition"})
        mastery = {"math.function.monotonicity": {"p_known": 0.9}}
        warmer = self.g.unmet_prerequisites("math.calculus.derivative", mastery)
        self.assertNotIn("math.function.monotonicity", {n.id for n in warmer})

    def test_next_learnable_cold_starts_foundational(self):
        nxt = self.g.next_learnable("物理", {})
        self.assertEqual(nxt[0].id, "physics.kinematics.velocity")

    def test_auto_node_for_unseeded_concept(self):
        node = self.g.ensure_node_for("量子纠缠", subject="物理")
        self.assertTrue(node.id.endswith("量子纠缠") or "量子纠缠" in node.id)
        # idempotent: same concept -> same node
        node2 = self.g.ensure_node_for("量子纠缠", subject="物理")
        self.assertEqual(node.id, node2.id)

    def test_descendants(self):
        desc = self.g.descendants_of("math.function.definition")
        self.assertIn("math.function.monotonicity", desc)
        self.assertIn("math.calculus.derivative", desc)


if __name__ == "__main__":
    unittest.main()
