"""Whole-catalogue metadata coverage and actual parameter-driven instances."""
from tests.storage_sandbox import StorageSandboxTestCase
from app.diagrams import adapters
from app.diagrams.catalog import catalog
from app.illustration.contracts import literal_source_span


class DiagramAdaptersTest(StorageSandboxTestCase):
    def test_every_registered_asset_has_closed_actual_card(self):
        for asset in catalog()[1].values():
            with self.subTest(asset=asset.id):
                card = adapters.asset_card(asset.id, audit=True)
                self.assertEqual(card["asset_id"], asset.id)
                self.assertEqual(set(card["parameters"]), set(asset.parameter_schema()))
                self.assertGreater(card["nominal_geometry"]["size"][0], 0)
                self.assertTrue(card["review"]["source_hash"])
                self.assertFalse("sample_params" in card)

    def test_instances_do_not_use_gallery_values(self):
        asset = catalog()[1]["chart.bar"]
        first = adapters.instantiate_asset(asset.id, asset.version, {"values": [2, 5], "labels": ["A", "B"]}, audit=True)
        second = adapters.instantiate_asset(asset.id, asset.version, {"values": [4, 10], "labels": ["A", "B"]}, audit=True)
        self.assertEqual(first.params["values"], [2, 5])
        self.assertEqual(second.params["values"], [4, 10])
        self.assertNotEqual(first.derived_facts, second.derived_facts)

    def test_position_anchors_are_not_physical_ports(self):
        geometry = adapters.instantiate_asset("biology.microscope", 1, {}, audit=True)
        self.assertEqual(geometry.ports["center"]["kind"], "position")
        self.assertNotIn("wire_terminal", adapters.capabilities("biology.microscope"))

    def test_quote_alignment_cannot_insert_conditions_negation_or_numbers(self):
        self.assertEqual(literal_source_span("导管末端紧贴烧杯内壁", "导管末端要紧贴烧杯内壁"), "导管末端要紧贴烧杯内壁")
        for source in ["导管末端不紧贴烧杯内壁", "导管末端3紧贴烧杯内壁"]:
            self.assertEqual(literal_source_span("导管末端紧贴烧杯内壁", source), "导管末端紧贴烧杯内壁")
