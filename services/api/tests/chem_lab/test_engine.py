"""Chem-lab engine unit tests: integer primitives, transfers, reducer paths,
determinism and state hashing. Pure engine — no storage involved."""
from __future__ import annotations

import unittest

from app.chem_lab.engine import model as m
from app.chem_lab.engine import replay

from tests.chem_lab.support import build_test_pack


class PrimitiveTest(unittest.TestCase):
    def test_milli_log10_table_edges(self) -> None:
        self.assertEqual(m.milli_log10(1), 0)
        self.assertEqual(m.milli_log10(10), 1000)
        self.assertEqual(m.milli_log10(100), 2000)
        self.assertEqual(m.milli_log10(1000), 3000)
        # Monotonic and floor-semantics inside the interpolation range.
        self.assertLessEqual(m.milli_log10(55), m.milli_log10(56))
        self.assertEqual(m.milli_log10(2), 301)
        with self.assertRaises(ValueError):
            m.milli_log10(0)

    def test_fnv1a64_stable_hex(self) -> None:
        self.assertEqual(m.fnv1a64(""), "cbf29ce484222325")
        self.assertEqual(len(m.fnv1a64("化学")), 16)
        self.assertEqual(m.fnv1a64("abc"), m.fnv1a64("abc"))
        self.assertNotEqual(m.fnv1a64("abc"), m.fnv1a64("abd"))

    def test_canonical_rejects_floats_and_sorts_keys(self) -> None:
        with self.assertRaises(TypeError):
            m.canonical({"x": 1.5})
        self.assertEqual(m.canonical({"b": 1, "a": [True, None]}), '{"a":[true,null],"b":1}')

    def test_transfer_floor_semantics(self) -> None:
        contents = {"a": 101, "b": 3}
        moved = m.transfer_amounts(contents, 333, 1000)
        self.assertEqual(moved, {"a": 33})  # floor(101*333/1000); b floors to 0
        self.assertEqual(m.transfer_amounts(contents, 0, 1000), {})
        self.assertEqual(m.transfer_amounts(contents, 100, 0), {})

    def test_add_amount_underflow_raises(self) -> None:
        amounts = {"x": 5}
        m.add_amount(amounts, "x", -5)
        self.assertEqual(amounts, {})
        with self.assertRaises(ValueError):
            m.add_amount(amounts, "x", -1)

    def test_interpolate_table(self) -> None:
        table = [[20000, 100], [40000, 300]]
        self.assertEqual(m.interpolate_table(table, 10000), 100)
        self.assertEqual(m.interpolate_table(table, 50000), 300)
        self.assertEqual(m.interpolate_table(table, 30000), 200)
        self.assertEqual(m.interpolate_table(table, 25000), 150)

    def test_temperature_models(self) -> None:
        vessel = {"volume_uL": 100000, "temperature_milli_c": 25000}
        self.assertEqual(m.temperature_delta_milli_c(vessel, 418000000), 1000)
        self.assertEqual(m.temperature_delta_milli_c({"volume_uL": 0}, 10**9), 0)
        self.assertEqual(m.cooling_drop_milli_c(vessel, 25000), 0)
        vessel["temperature_milli_c"] = 35000
        self.assertEqual(m.cooling_drop_milli_c(vessel, 25000), 60)


class ReducerDeterminismTest(unittest.TestCase):
    def test_state_hash_order_independent(self) -> None:
        pack = build_test_pack()
        state_a, _e, _h, _a = replay.run_script(pack, [{"kind": "wait", "duration_ms": 1000}])
        state_b, _e2, _h2, _a2 = replay.run_script(pack, [{"kind": "wait", "duration_ms": 1000}])
        self.assertEqual(state_a["state_hash"], state_b["state_hash"])
        self.assertEqual(_h, _h2)

    def test_rejection_advances_revision_but_not_chemistry(self) -> None:
        pack = build_test_pack()
        state, events, hashes, accepted = replay.run_script(pack, [
            {"kind": "aspirate", "source_id": "ghost", "instrument_id": "pip-1", "amount_uL": 100},
        ])
        self.assertEqual(accepted, [False])
        self.assertEqual(events[0]["kind"], "command_rejected")
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["vessels"]["acid"]["contents"], {"h_plus": 1000, "cl_minus": 1000})

    def test_neutralization_stoichiometry_and_ph(self) -> None:
        pack = build_test_pack()
        state, events, _h, accepted = replay.run_script(pack, [
            {"kind": "aspirate", "source_id": "base", "instrument_id": "pip-1", "amount_uL": 5000},
            {"kind": "dispense", "instrument_id": "pip-1", "target_id": "acid", "amount_uL": 5000},
        ])
        self.assertEqual(accepted, [True, True])
        # 500 umol OH⁻ neutralizes half of the 1000 umol H⁺.
        self.assertEqual(state["vessels"]["acid"]["contents"].get("h_plus"), 500)
        self.assertNotIn("oh_minus", state["vessels"]["acid"]["contents"])
        # pH = -(log10(500) - log10(20000)) with the milli-log table.
        expected_ph = m.milli_log10(20000) - m.milli_log10(500)
        self.assertEqual(state["vessels"]["acid"]["ph_milli"], expected_ph)
        kinds = [event["kind"] for event in events]
        self.assertIn("species_consumed", kinds)
        self.assertIn("temperature_changed", kinds)

    def test_measure_requires_modeled_quantity(self) -> None:
        pack = build_test_pack()
        # The probe does not measure mass at all → incompatible device.
        _s, events, _h, accepted = replay.run_script(pack, [
            {"kind": "measure", "instrument_id": "probe-1", "vessel_id": "acid", "quantity": "mass"},
        ])
        self.assertEqual(accepted, [False])
        self.assertEqual(events[0]["data"]["reason"], "incompatible_device")
        # With ph_model "none" the vessel offers no pH → not_modeled.
        pack["ph_model"] = "none"
        _s, events, _h, accepted = replay.run_script(pack, [
            {"kind": "measure", "instrument_id": "probe-1", "vessel_id": "acid", "quantity": "ph"},
        ])
        self.assertEqual(accepted, [False])
        self.assertEqual(events[0]["data"]["reason"], "not_modeled")

    def test_pipette_capacity_enforced(self) -> None:
        pack = build_test_pack()
        _s, _e, _h, accepted = replay.run_script(pack, [
            {"kind": "aspirate", "source_id": "base", "instrument_id": "pip-1", "amount_uL": 20000},
        ])
        self.assertEqual(accepted, [False])


if __name__ == "__main__":
    unittest.main()
