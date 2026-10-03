"""Scientific invariants and closed controls of the new public compositions."""
import math

from app.diagrams import adapters
from app.diagrams.catalog import catalog
from app.diagrams.curriculum_expansion import entries
from app.diagrams.schema import DiagramError
from tests.support.storage_sandbox import StorageSandboxTestCase


class DiagramExpansionTest(StorageSandboxTestCase):
    def test_redshift_stretches_all_wavelengths_by_the_same_ratio(self):
        asset = catalog()[1]["astronomy_extended.redshift_lines"]
        self.assertEqual(asset.version, 2)
        for shift in [0, 35, 60]:
            drawing = asset.draw({"shift": shift})
            lines = [part for part in drawing.parts if part.tag.endswith("line")
                and part.get("stroke") == drawing.blue and part.get("x1") == part.get("x2")]
            reference, observed = lines[:4], lines[4:]
            ratios = [(float(second.get("x1"))-60)/(float(first.get("x1"))-60)
                for first, second in zip(reference, observed)]
            self.assertEqual(len(ratios), 4)
            for ratio in ratios:
                self.assertAlmostEqual(ratio, 1+shift/229, places=4)
            self.assertAlmostEqual(float(observed[-1].get("x1"))-float(reference[-1].get("x1")), shift, places=2)

    def test_intrinsic_labels_are_reused_without_bypassing_target_validation(self):
        from xml.etree import ElementTree as ET
        from app.illustration.contracts import Annotation, IllustrationError
        from app.illustration.layout import compile_scene
        from tests.illustration.test_illustration_v2 import fixture
        contract, brief, bundle, scene = fixture("physics_extended.closed_pipe_modes", parameters={"mode": (2, "")})
        contract.required_marks = ["闭端", "开端", "位移包络"]
        scene.annotations = [Annotation(annotation_id="closed", text="闭端", target={"instance": "main", "port": "left"})]
        compiled = compile_scene(scene, contract=contract, brief=brief, bundle=bundle)
        texts = [part.text for part in ET.fromstring(compiled.illustration.svg).iter() if part.tag.endswith("text")]
        self.assertEqual(texts.count("闭端"), 1)
        self.assertTrue(any(row["reason"] == "reuse_intrinsic_mark" for row in compiled.source.layout_report.adjustments))
        scene.annotations[0].target.port = "missing"
        with self.assertRaises(IllustrationError):
            compile_scene(scene, contract=contract, brief=brief, bundle=bundle)

    def test_inventory_is_original_registered_and_isolated(self):
        from app.diagrams.guidance import GUIDE_DIR, for_asset
        from app.diagrams.registry import extension
        rows = entries()
        self.assertEqual(len({row["id"] for row in rows}), 40)
        self.assertEqual(len({row["category"] for row in rows}), 10)
        for row in rows:
            with self.subTest(asset=row["id"]):
                self.assertIsNotNone(extension(row["id"]))
                self.assertTrue((GUIDE_DIR/row["id"]/"asset.svg").is_file())
                self.assertTrue(for_asset(row["id"])["hints"]["review"])

    def test_water_angle_is_actual_bond_geometry_and_stoichiometry(self):
        asset = catalog()[1]["chemistry.water"]
        drawing = asset.draw()
        circles = [part for part in drawing.parts if part.tag.endswith("circle")]
        center, *hydrogens = circles
        vectors = [(float(part.get("cx"))-float(center.get("cx")),
            float(part.get("cy"))-float(center.get("cy"))) for part in hydrogens]
        cosine = sum(a*b for a, b in zip(*vectors))/math.prod(math.hypot(*v) for v in vectors)
        self.assertAlmostEqual(math.degrees(math.acos(cosine)), 104.5, places=2)
        self.assertEqual(drawing.facts["atoms"], ["O", "H", "H"])
        self.assertEqual(asset.version, 2)

    def test_displacement_boundary_is_open_closed_and_odd_harmonics(self):
        asset = catalog()[1]["physics_extended.closed_pipe_modes"]
        self.assertEqual(asset.version, 2)
        for mode in [1, 2, 3]:
            d = asset.draw({"mode": mode})
            wave = [part for part in d.parts if part.tag.endswith("polyline")][1]
            points = [list(map(float, xy.split(","))) for xy in wave.get("points").split()]
            # Long paths split at 100 points; use all sections for the endpoint.
            sections = [part for part in d.parts if part.tag.endswith("polyline") and part.get("stroke") == d.blue]
            envelope = [list(map(float, xy.split(","))) for section in sections for xy in section.get("points").split()]
            self.assertTrue(all(y <= 160 for _, y in envelope))
            nodes = {round(x, 2) for x, y in envelope if abs(y-160) < .01}
            expected_nodes = {round(60+370*2*k/(2*mode-1), 2) for k in range(mode)}
            self.assertEqual(nodes, expected_nodes)
            end = list(map(float, sections[-1].get("points").split()[-1].split(",")))
            self.assertEqual(points[0], [60, 160])
            self.assertAlmostEqual(abs(end[1]-160), 54, places=2)
            self.assertEqual(d.facts["harmonic"], 2*mode-1)
        with self.assertRaises(DiagramError):
            asset.draw({"mode": 4})

    def test_daylight_equinox_and_hemisphere_symmetry(self):
        asset = catalog()[1]["geography_extended.latitude_daylight"]
        for latitude in [-60, -30, 0, 30, 60]:
            self.assertAlmostEqual(asset.draw({"latitude": latitude, "declination": 0}).facts["daylight_hours"], 12)
            north = asset.draw({"latitude": latitude, "declination": 23.44}).facts["daylight_hours"]
            south = asset.draw({"latitude": -latitude, "declination": 23.44}).facts["daylight_hours"]
            self.assertAlmostEqual(north+south, 24)
        with self.assertRaises(DiagramError):
            asset.draw({"latitude": 90})

    def test_parameter_boundaries_reject_unregistered_science_controls(self):
        for row in entries():
            asset = catalog()[1][row["id"]]
            with self.subTest(asset=asset.id):
                with self.assertRaises(DiagramError):
                    asset.draw({"fabricated_measurement": 5})
                for key, spec in asset.parameter_schema().items():
                    for value in [spec["minimum"], spec["maximum"]]:
                        asset.draw({key: value})
                    with self.assertRaises(DiagramError):
                        asset.draw({key: spec["maximum"]+1})

    def test_qualitative_pixel_control_does_not_erase_physical_units(self):
        shift = adapters.parameter_semantics("astronomy_extended.redshift_lines")["shift"]
        self.assertTrue(shift["non_quantitative_allowed"])
        self.assertEqual(shift["unit"], "diagram_px")
        latitude = adapters.parameter_semantics("geography_extended.latitude_daylight")["latitude"]
        self.assertFalse(latitude["non_quantitative_allowed"])
        self.assertEqual(latitude["unit"], "°")
        self.assertEqual(adapters.semantic_domain(catalog()[1]["mathematics_extended.unit_circle_projection"]), "geometry")

    def test_probability_branches_normalize_without_publishing_answer(self):
        asset = catalog()[1]["statistics_extended.conditional_probability_tree"]
        drawing = asset.draw({"p_a": .3, "p_b_given_a": .8})
        texts = [part.text for part in drawing.parts if part.text]
        self.assertTrue({"0.3", "0.7", "0.8", "0.2"} <= set(texts))
        self.assertNotIn("0.24", texts)
        with self.assertRaises(DiagramError):
            asset.draw({"p_a": 0})
