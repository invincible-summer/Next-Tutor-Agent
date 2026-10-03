"""Material-independent layout constraints and real-terminal wire routing."""
from __future__ import annotations

import copy

from tests.support.storage_sandbox import StorageSandboxTestCase
from app.diagrams.legacy_layout import (
    crosses, fit_public_relations, public_relation_kinds, relation_issues, wire_networks, wire_route,
)


def spatial_fixture(kind="inside"):
    quote = "物体放在容器内部。" if kind == "inside" else "物体由支架支撑。"
    relation = {"type": kind, "source": {"node": "object", "region": "payload"},
                "target": {"node": "holder", "region": "space"}, "source_quote": quote[:-1]}
    raw = {"width": 640, "height": 400, "nodes": [
        {"id": "object", "x": 40, "y": 40, "scale": 1, "params": {"physical_value": 7}},
        {"id": "holder", "x": 200, "y": 120, "scale": 1, "params": {"appearance": "blue"}}],
        "layout_relations": [relation]}
    geometry = {"nodes": [
        {"id": "object", "bounds": [40, 40, 50, 50], "regions": {"payload": {"bounds": [50, 50, 20, 20]}},
            "anchors": {"bottom": [60, 70]}},
        {"id": "holder", "bounds": [200, 120, 160, 160], "regions": {"space": {"bounds": [220, 160, 100, 80]}},
            "anchors": {"top": [270, 160]}}], "connections": [], "layout_relations": [relation]}
    return raw, geometry, quote


class LegacyLayoutTest(StorageSandboxTestCase):
    def test_generic_regions_fit_without_changing_any_material_parameter(self):
        raw, geometry, quote = spatial_fixture()
        original = copy.deepcopy(raw)
        fitted = fit_public_relations(raw, geometry, quote)
        self.assertEqual(raw, original)
        self.assertNotEqual(fitted["nodes"][0]["x"], raw["nodes"][0]["x"])
        for before, after in zip(raw["nodes"], fitted["nodes"]):
            self.assertEqual(before["params"], after["params"])
            self.assertEqual(before["scale"], after["scale"])
        self.assertEqual(fitted["nodes"][1], raw["nodes"][1])

    def test_generic_anchor_support_and_unknown_regions(self):
        raw, geometry, quote = spatial_fixture("supported_by")
        relation = geometry["layout_relations"][0]
        relation["source"] = {"node": "object", "anchor": "bottom"}
        relation["target"] = {"node": "holder", "anchor": "top"}
        fitted = fit_public_relations(raw, geometry, quote)
        self.assertEqual(fitted["nodes"][0]["y"], 130)
        relation["target"] = {"node": "holder", "region": "invented"}
        self.assertEqual(fit_public_relations(raw, geometry, quote), raw)
        self.assertEqual(relation_issues(geometry, quote)[0]["code"], "unknown_relation_region")

    def test_negated_public_conditions_never_become_positive_layout(self):
        raw, geometry, _ = spatial_fixture()
        quote = "不要让这个物体放在容器内部。"
        geometry["layout_relations"][0]["source_quote"] = "放在容器内部"
        self.assertEqual(fit_public_relations(raw, geometry, quote), raw)
        self.assertNotIn("inside", public_relation_kinds(quote))
        self.assertEqual(relation_issues(geometry, quote)[0]["code"], "invalid_public_relation")
        from app.diagrams.legacy_layout import public_relation_supported
        quote = "使用测量部件观察，探头浸没在介质中。"
        self.assertTrue(public_relation_supported({"type": "immersed_in", "source_quote": quote}, quote))
        self.assertEqual(public_relation_kinds(quote), {"immersed_in"})
        self.assertEqual(public_relation_kinds("在实验中，观察部件的变化。"), set())

    def test_support_chain_is_independent_of_model_relation_order(self):
        quote = "上层由中层支撑，中层由底层支撑。"
        relations = [{"type": "supported_by", "source": {"node": source, "anchor": "bottom"},
                      "target": {"node": target, "anchor": "top"}, "source_quote": text}
                     for source, target, text in [("upper", "middle", "上层由中层支撑"),
                                                  ("middle", "base", "中层由底层支撑")]]
        raw = {"width": 640, "height": 400, "nodes": [
            {"id": name, "x": 40, "y": y, "params": {}, "scale": 1}
            for name, y in [("upper", 40), ("middle", 120), ("base", 240)]]}
        geometry = {"layout_relations": relations, "nodes": [
            {"id": row["id"], "bounds": [40, row["y"], 40, 40], "regions": {},
             "anchors": {"top": [60, row["y"]], "bottom": [60, row["y"]+40]}}
            for row in raw["nodes"]]}
        fitted = fit_public_relations(raw, geometry, quote)
        self.assertEqual([node["y"] for node in fitted["nodes"]], [160, 200, 240])
        geometry["layout_relations"].reverse()
        self.assertEqual(fit_public_relations(raw, geometry, quote), fitted)

    def test_missing_relation_and_impossible_containment_stay_blocked(self):
        raw, geometry, quote = spatial_fixture()
        geometry["layout_relations"] = []
        self.assertEqual(relation_issues(geometry, quote)[0]["code"], "missing_layout_relation")
        geometry["layout_relations"] = raw["layout_relations"]
        geometry["nodes"][1]["regions"]["space"]["bounds"] = [220, 160, 10, 10]
        self.assertEqual(fit_public_relations(raw, geometry, quote), raw)
        self.assertEqual(relation_issues(geometry, quote)[0]["code"], "layout_relation_mismatch")
        geometry["layout_relations"][0]["source"] = {"node": "object", "anchor": "bottom"}
        self.assertEqual(relation_issues(geometry, quote)[0]["code"], "relation_region_required")
        self.assertEqual(fit_public_relations(raw, geometry, quote), raw)

    def test_cyclic_support_cannot_collapse_into_a_false_success(self):
        raw, geometry, _ = spatial_fixture("supported_by")
        quote = "物体由支架支撑，支架由物体支撑。"
        geometry["layout_relations"] = [
            {"type": "supported_by", "source": {"node": source, "anchor": source_anchor},
             "target": {"node": target, "anchor": target_anchor}, "source_quote": text}
            for source, source_anchor, target, target_anchor, text in [
                ("object", "bottom", "holder", "top", "物体由支架支撑"),
                ("holder", "top", "object", "bottom", "支架由物体支撑")]]
        self.assertEqual(fit_public_relations(raw, geometry, quote), raw)
        self.assertIn("layout_relation_cycle", {issue["code"] for issue in relation_issues(geometry, quote)})

    def test_wire_avoids_ink_interior_and_stroke_edge(self):
        blocker = [80, 80, 60, 60]
        for y in (80, 100):
            points = wire_route((30, y), (200, y), route="orthogonal", blockers=[blocker], width=320, height=240)
            self.assertEqual(points[0], (30, y))
            self.assertEqual(points[-1], (200, y))
            self.assertTrue(all(not crosses(a, b, [77, 77, 66, 66]) for a, b in zip(points, points[1:])))

    def test_real_ports_escape_the_nearest_side_without_internal_bypass(self):
        left, right = [40, 120, 120, 120], [240, 240, 120, 120]
        protected = [{"node": "source", "region": "body", "bounds": left},
                     {"node": "target", "region": "body", "bounds": right}]
        points = wire_route((50, 180), (250, 300), route="orthogonal", blockers=protected,
            width=640, height=400, endpoint_bodies=[("source", left), ("target", right)])
        self.assertLess(points[1][0], points[0][0])
        self.assertLess(points[-2][0], points[-1][0])
        self.assertTrue(all(not crosses(a, b, left) for a, b in zip(points[1:], points[2:])))
        self.assertTrue(all(not crosses(a, b, right) for a, b in zip(points[:-2], points[1:-1])))

        # Adjacent bodies may already have facing, real lead terminals.
        touching = [160, 120, 120, 120]
        regions = [protected[0], {"node": "target", "region": "body", "bounds": touching}]
        self.assertEqual(wire_route((150, 180), (170, 180), route="orthogonal", blockers=regions,
            width=640, height=400, endpoint_bodies=[("source", left), ("target", touching)]),
            [(150, 180), (170, 180)])

    def test_position_anchors_cannot_become_electrical_terminals(self):
        from app.diagrams.catalog import catalog
        from app.diagrams.compiler import compile_scene
        from app.diagrams.schema import DiagramError
        raw = {"nodes": [{"id": name, "asset_id": "circuit.battery_pack", "version": catalog()[1]["circuit.battery_pack"].version,
                         "x": x, "y": 40, "params": {}} for name, x in [("a", 40), ("b", 300)]],
            "alt": "合成端子边界示意图", "connections": [{"kind": "wire",
                "start": {"node": "a", "anchor": "center"}, "end": {"node": "b", "anchor": "terminal_left"}}]}
        with self.assertRaises(DiagramError) as failure:
            compile_scene(raw)
        self.assertEqual(failure.exception.code, "diagram_anchor_missing")

    def test_independent_wires_do_not_create_undeclared_junctions(self):
        left, right = [40, 100, 40, 40], [280, 100, 40, 40]
        protected = [{"node": "source", "region": "body", "bounds": left},
                     {"node": "target", "region": "body", "bounds": right}]
        prior = {"network": ("a", "right"), "points": [(180, 80), (180, 200)]}
        points = wire_route((80, 120), (280, 120), route="orthogonal", blockers=protected,
            width=400, height=300, endpoint_bodies=[("source", left), ("target", right)],
            occupied=[prior], network=("b", "right"))
        self.assertTrue(all(not crosses(a, b, [176, 76, 8, 128]) for a, b in zip(points, points[1:])))
        same = wire_route((80, 120), (280, 120), route="orthogonal", blockers=protected,
            width=400, height=300, endpoint_bodies=[("source", left), ("target", right)],
            occupied=[prior], network=prior["network"])
        self.assertEqual(same, [(80, 120), (280, 120)])
        connections = [{"kind": "wire", "start": {"node": "a", "anchor": "right"},
                        "end": {"node": node, "anchor": "left"}} for node in ("b", "c")]
        networks = wire_networks(connections)
        self.assertEqual(networks[("b", "left")], networks[("c", "left")])

    def test_container_layers_do_not_depend_on_model_node_order(self):
        from app.diagrams.catalog import catalog
        from app.diagrams.compiler import compile_scene
        library = catalog()[1]
        raw = {"width": 640, "height": 400, "alt": "合成容器与测量部件",
               "nodes": [{"id": "container", "asset_id": "vessel.beaker",
                          "version": library["vessel.beaker"].version,
                          "x": 240, "y": 120, "params": {"fill": .7}},
                         {"id": "probe", "asset_id": "apparatus.thermometer",
                          "version": library["apparatus.thermometer"].version,
                          "x": 240, "y": 90, "params": {"reading": 35, "scale_labels": True}}],
               "layout_relations": [{"type": "immersed_in", "source": {"node": "probe", "region": "bulb"},
                                     "target": {"node": "container", "region": "liquid"},
                                     "source_quote": "测量部件浸没在液体中"}]}
        first = compile_scene(raw)
        raw["nodes"].reverse()
        second = compile_scene(raw)
        self.assertEqual(first.illustration.svg, second.illustration.svg)
        self.assertEqual(first.illustration.content_hash, second.illustration.content_hash)
