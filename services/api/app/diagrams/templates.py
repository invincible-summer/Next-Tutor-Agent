"""Composed teaching scenes with deliberate proportions and named attachments."""
from __future__ import annotations

import math

from .drawing import Drawing
from .schema import DiagramError

# Each recipe is real geometry, not a title card. Positions are in a 640×400 sheet.
RECIPES = {
    "heating_beaker": [("apparatus.tripod", 244, 152, 1.2), ("apparatus.alcohol_lamp", 244, 170, 1.2), ("apparatus.wire_mesh", 240, 88, 1.2), ("vessel.beaker", 244, 20, 1.2)],
    "heating_tube": [("apparatus.stand", 160, 135, 1.4), ("apparatus.alcohol_lamp", 296, 180, 1.2), ("vessel.hard_test_tube", 250, 43, 1.4)],
    "filtration": [("apparatus.stand", 146, 110, 1.6), ("apparatus.funnel", 248, 85, 1.1), ("vessel.beaker", 294, 176, 1.2)],
    "evaporation": [("apparatus.tripod", 238, 140, 1.25), ("apparatus.alcohol_lamp", 240, 170, 1.25), ("vessel.evaporating_dish", 235, 79, 1.3)],
    "crystallization": [("vessel.beaker", 75, 122, 1.3), ("vessel.evaporating_dish", 279, 119, 1.3), ("chemistry.crystal", 456, 147, .8)],
    "distillation": [("apparatus.stand", 33, 112, 1.4), ("apparatus.alcohol_lamp", 106, 196, 1), ("vessel.distilling_flask", 113, 83, 1.1), ("apparatus.condenser", 279, 64, 1.5), ("vessel.conical_flask", 454, 205, 1)],
    "separation": [("apparatus.stand", 139, 105, 1.7), ("apparatus.separatory_funnel", 290, 20, 1.25), ("vessel.conical_flask", 290, 192, 1.25)],
    "extraction": [("vessel.conical_flask", 64, 142, 1.1), ("apparatus.separatory_funnel", 286, 27, 1.25), ("vessel.beaker", 277, 203, 1.25)],
    "titration": [("apparatus.stand", 232, 12, 2.3), ("apparatus.burette", 390, 4, 1.65), ("vessel.conical_flask", 426, 227, 1.2)],
    "gas_wash": [("vessel.conical_flask", 80, 174, 1.2), ("vessel.gas_wash_bottle", 288, 161, 1.2), ("vessel.gas_jar", 473, 166, 1.2)],
    "gas_dry": [("vessel.conical_flask", 82, 175, 1.2), ("apparatus.drying_tube", 290, 70, 1.2), ("vessel.gas_jar", 475, 175, 1.2)],
    "gas_water": [("vessel.conical_flask", 60, 177, 1.2), ("vessel.tank", 384, 161, 1.5), ("vessel.gas_jar", 408, 105, 1.2)],
    "gas_up": [("vessel.conical_flask", 91, 165, 1.2), ("vessel.gas_jar", 381, 140, 1.5)],
    "gas_down": [("vessel.conical_flask", 91, 165, 1.2), ("vessel.gas_jar", 381, 140, 1.5)],
    "gas_preparation": [("vessel.conical_flask", 158, 120, 1.5), ("apparatus.thistle_funnel", 158, 17, 1.3), ("vessel.gas_jar", 413, 163, 1.3)],
    "galvanic_cell": [("vessel.beaker", 129, 101, 2.4), ("circuit.voltmeter", 270, 10, .8)],
    "electrolysis": [("vessel.beaker", 129, 101, 2.4), ("circuit.battery", 270, 10, .8)],
    "chromatography": [("vessel.beaker", 202, 107, 1.8), ("objects.paper", 255, 80, .9)],
    "flame_test": [("apparatus.alcohol_lamp", 213, 147, 1.5), ("apparatus.glass_rod", 286, 10, 1.25)],
    "horizontal_block": [("mechanics.plane", 158, 136, 2), ("mechanics.block", 250, 150, 1.35)],
    "pulley_blocks": [("mechanics.pulley", 247, 16, 1.25), ("mechanics.block", 158, 203, 1), ("mechanics.weight", 350, 207, 1)],
    "spring_horizontal": [("mechanics.wall", 64, 116, 1.3), ("mechanics.spring", 171, 140, 1.3), ("mechanics.block", 323, 139, 1.3), ("mechanics.plane", 141, 176, 2.5)],
    "spring_vertical": [("mechanics.vertical_spring", 250, 26, 1.5), ("mechanics.weight", 250, 175, 1.5)],
    "pendulum": [("mechanics.pendulum", 189, 16, 2.2)],
    "lever_balance": [("mechanics.lever", 148, 158, 2.2), ("mechanics.weight", 130, 15, 1.1), ("mechanics.weight", 350, 15, 1.1)],
    "collision": [("mechanics.cart", 75, 113, 1.4), ("mechanics.cart", 332, 113, 1.4), ("mechanics.plane", 45, 130, 3.1)],
    "projectile": [("mechanics.table", 66, 120, 1.4), ("mechanics.ball", 176, 134, .55)],
    "circular_motion": [("mechanics.circular_track", 190, 68, 1.7), ("mechanics.ball", 353, 115, .55)],
    "buoyancy": [("vessel.beaker", 111, 43, 2.4), ("mechanics.ball", 225, 181, .8), ("measurement.dynamometer", 265, 9, .8)],
    "overflow": [("vessel.overflow_cup", 106, 89, 1.8), ("vessel.cylinder", 375, 139, 1.5), ("mechanics.ball", 186, 83, .7)],
    "manometer": [("waves.manometer", 157, 26, 2.1), ("vessel.beaker", 378, 103, 1.5)],
    "communicating": [("waves.communicating_vessels", 166, 47, 2.2)],
    "hydraulic": [("waves.hydraulic_press", 161, 51, 2.2)],
    "piston": [("waves.piston", 115, 75, 2.5)],
    "thermal": [("measurement.hotplate", 240, 164, 1.25), ("vessel.beaker", 238, 18, 1.3), ("apparatus.thermometer", 272, 5, .95)],
    "conduction": [("apparatus.alcohol_lamp", 105, 186, 1), ("mechanics.rod", 179, 89, 2.2)],
    "lens_bench": [("optics.candle", 77, 109, 1.2), ("optics.convex_lens", 258, 74, 1.45), ("optics.screen", 465, 74, 1.45)],
    "concave_lens": [("optics.concave_lens", 246, 78, 1.5)],
    "pinhole": [("optics.candle", 66, 111, 1.2), ("optics.pinhole", 258, 74, 1.45), ("optics.screen", 465, 74, 1.45)],
    "prism": [("optics.prism", 178, 44, 2)],
    "double_slit": [("optics.laser", 58, 107, 1), ("optics.double_slit", 248, 44, 1.8), ("optics.screen", 443, 44, 1.8)],
    "microscopy": [("biology.microscope", 54, 39, 1.9), ("biology.slide", 352, 152, 1.3)],
    "germination": [("biology.germination", 61, 48, 1.8), ("biology.germination", 329, 48, 1.8)],
    "respiration": [("vessel.conical_flask", 125, 149, 1.3), ("waves.manometer", 343, 86, 1.7)],
    "transpiration": [("biology.germination", 120, 82, 1.8), ("vessel.bell_jar", 122, 15, 2.2)],
    "osmosis": [("vessel.beaker", 148, 22, 2.4), ("biology.dialysis_bag", 211, 81, 1.6)],
    "plasmolysis": [("biology.osmosis", 63, 64, 1.7), ("biology.plasmolysis", 348, 64, 1.7)],
    "enzyme": [("biology.enzyme", 58, 64, 1.6), ("biology.enzyme", 352, 64, 1.6)],
    "seasons": [("earth.seasons", 136, 10, 2.3)],
    "water_cycle": [("earth.mountain", 79, 158, 1.3), ("earth.cloud", 262, 8, 1.35), ("earth.pool", 405, 166, 1.3)],
}

GRAPH_TEMPLATES = {"pedigree": "pedigree", "food_web": "directed", "probability": "probability_tree",
                   "binary_tree": "binary_tree", "flowchart": "flow", "experiment_control": "comparison"}
CHART_TEMPLATES = {"bar_table": "bar", "pie_table": "pie", "box_comparison": "box",
                   "scatter_fit": "scatter", "histogram": "histogram"}
MATH_TEMPLATES = {"triangle_angles": "triangle", "inscribed_triangle": "circumcircle",
                  "tangent_radius": "tangent", "two_circles": "intersecting_circles",
                  "similar_triangles": "triangle", "solid_section": "section", "solid_net": "net"}


def parameter_schema(variant: str) -> dict:
    from .catalog import catalog
    assets = catalog()[1]
    if variant in GRAPH_TEMPLATES:
        return assets[f"graph.{GRAPH_TEMPLATES[variant]}"].parameter_schema()
    if variant in CHART_TEMPLATES:
        return assets[f"chart.{CHART_TEMPLATES[variant]}"].parameter_schema()
    if variant in MATH_TEMPLATES:
        return assets[f"geometry.{MATH_TEMPLATES[variant]}"].parameter_schema()
    if variant in {"function_tangent", "integral"}:
        return assets[f"function.{'tangent_line' if variant == 'function_tangent' else 'integral'}"].parameter_schema()
    schema = {}
    if variant in {"heating_beaker", "heating_tube", "filtration", "separation", "titration", "buoyancy", "overflow", "osmosis"}:
        schema["fill"] = {"type": "number", "minimum": 0, "maximum": .4 if variant == "heating_tube" else 1, "default": .65 if variant == "buoyancy" else .2 if variant == "overflow" else 0}
    if variant in {"heating_beaker", "heating_tube", "evaporation"}:
        schema["lit"] = {"type": "boolean", "default": False}
    if variant in {"pendulum", "inclined_block"}:
        schema["angle"] = {"type": "number", "minimum": -30 if variant == "pendulum" else 0, "maximum": 30, "default": 25}
    if variant == "pendulum":
        schema["show_angle"] = {"type": "boolean", "default": False}
    return schema


def template(variant: str, p: dict, mono=False) -> Drawing:
    from .catalog import catalog
    assets = catalog()[1]
    d = Drawing(640, 400, mono)
    if variant in GRAPH_TEMPLATES:
        drawing = assets[f"graph.{GRAPH_TEMPLATES[variant]}"].draw(p, monochrome=mono)
        d.add(drawing, 50, 28, 1.12)
    elif variant in CHART_TEMPLATES:
        drawing = assets[f"chart.{CHART_TEMPLATES[variant]}"].draw(p, monochrome=mono)
        d.add(drawing, 49, 10, 1.1)
    elif variant in MATH_TEMPLATES:
        drawing = assets[f"geometry.{MATH_TEMPLATES[variant]}"].draw(p, monochrome=mono)
        d.add(drawing, 176, 14, 2.25)
        if variant == "tangent_radius":
            d.line(356, 194, 356, 72.5, color=d.blue)
            d.poly([(356, 91), (374, 91), (374, 72.5)], color=d.blue, width=1)
        if variant == "triangle_angles":
            points = [drawing.anchors[f"vertex_{i}"] for i in range(3)]
            for i, (x, y) in enumerate(points):
                a = math.atan2(points[(i+1)%3][1]-y, points[(i+1)%3][0]-x)
                b = math.atan2(points[(i+2)%3][1]-y, points[(i+2)%3][0]-x)
                sweep = int((b-a)%(2*math.pi) < math.pi)
                px, py = 176+x*2.25, 14+y*2.25
                d.path(f"M {px+27*math.cos(a):.3f} {py+27*math.sin(a):.3f} A 27 27 0 0 {sweep} {px+27*math.cos(b):.3f} {py+27*math.sin(b):.3f}", color=d.blue, width=1.4)
        if variant == "similar_triangles":
            d.add(drawing, 430, 181, .85)
    elif variant in {"function_tangent", "integral"}:
        d.add(assets[f"function.{'tangent_line' if variant == 'function_tangent' else 'integral'}"].draw(p, monochrome=mono), 49, 24, 1.12)
    elif variant == "inclined_block":
        angle = math.radians(abs(p.get("angle", 25)))
        plane = assets["mechanics.incline"].draw({"angle": abs(p.get("angle", 25))}, monochrome=mono)
        d.add(plane, 158, 9, 2.15)
        target = (158+2.15*78, 9+2.15*(139-63*math.tan(angle)))
        block = assets["mechanics.block"].draw(monochrome=mono)
        scale = .85
        ox = target[0]-scale*(80*math.cos(-angle)-109*math.sin(-angle))
        oy = target[1]-scale*(80*math.sin(-angle)+109*math.cos(-angle))
        d.add(block, ox, oy, scale, -abs(p.get("angle", 25)))
    elif variant in {"series_circuit", "parallel_circuit", "resistance", "lamp_power"}:
        def component(asset_id, cx, cy, scale=.8):
            part = assets[asset_id].draw(monochrome=mono)
            d.add(part, cx-80*scale, cy-80*scale, scale)
            return cx-68*scale, cx+68*scale
        a, b = component("circuit.battery", 320, 312)
        d.poly([(92, 145), (92, 312), (a, 312)])
        d.poly([(b, 312), (548, 312), (548, 145)])
        if variant in {"resistance", "lamp_power"}:
            a, b = component("circuit.lamp_symbol" if variant == "lamp_power" else "circuit.resistor", 285, 145)
            d.line(92, 145, a, 145)
            c, e = component("circuit.ammeter", 435, 145, .55)
            d.line(b, 145, c, 145)
            d.line(e, 145, 548, 145)
            c, e = component("circuit.voltmeter", 285, 65, .6)
            d.poly([(a, 145), (a, 65), (c, 65)])
            d.poly([(e, 65), (b, 65), (b, 145)])
        else:
            a, b = component("circuit.resistor", 250, 145)
            c, e = component("circuit.resistor", 424, 145)
            d.line(92, 145, a, 145)
            d.line(b, 145, c, 145)
            d.line(e, 145, 548, 145)
            if variant == "parallel_circuit":
                # Two independent branches between the same pair of junctions.
                d.parts.clear()
                a, b = component("circuit.battery", 320, 312)
                d.poly([(92, 100), (92, 312), (a, 312)])
                d.poly([(b, 312), (548, 312), (548, 100)])
                for y in [100, 200]:
                    a, b = component("circuit.resistor", 320, y)
                    d.line(92, y, a, y)
                    d.line(b, y, 548, y)
                for x in [92, 548]:
                    d.circle(x, 200, 3, fill=d.ink)
        d.facts["topology"] = variant
    elif variant in {"spring_horizontal", "spring_vertical", "lever_balance", "collision", "pulley_blocks", "buoyancy", "overflow", "heating_tube"}:
        if variant == "spring_horizontal":
            d.line(120, 92, 120, 280, width=3)
            d.hatch(100, 92, 18, 188)
            d.line(119, 218, 535, 218, width=3)
            d.hatch(119, 221, 416, 14)
            d.poly([(120, 182), (147, 182)]+[(147+i*10, 182+(12 if i%2 else -12)) for i in range(1,20)]+[(347,182),(376,182)])
            d.rect(376, 148, 108, 70, fill=d.surface, radius=4)
        elif variant == "spring_vertical":
            d.rect(224, 38, 192, 12, fill=d.surface)
            d.hatch(224, 38, 192, 12)
            d.poly([(320, 50), (320, 77)]+[(320+(12 if i%2 else -12),77+i*8) for i in range(1,20)]+[(320,237),(320,260)])
            d.rect(285, 260, 70, 78, fill=d.surface, radius=3)
        elif variant == "lever_balance":
            d.rect(99, 155, 442, 15, fill=d.surface, radius=4)
            d.poly([(320, 172), (284, 238), (356, 238)], closed=True, fill=d.surface)
            for x in [162, 475]:
                d.line(x, 170, x, 239)
                d.rect(x-26, 239, 52, 60, fill=d.surface, radius=3)
        elif variant == "collision":
            d.line(56, 262, 584, 262, width=3)
            d.hatch(56, 265, 528, 16)
            for x in [110, 380]:
                d.add(assets["mechanics.cart"].draw(monochrome=mono), x, 106, 1.2)
        elif variant == "pulley_blocks":
            d.rect(235, 25, 170, 12, fill=d.surface)
            d.hatch(235, 25, 170, 12)
            d.line(320, 37, 320, 95)
            d.circle(320, 145, 50, fill=d.surface)
            d.circle(320, 145, 41, color=d.muted)
            d.circle(320, 145, 6, fill=d.ink)
            d.path("M 270 290 L 270 145 A 50 50 0 0 1 370 145 L 370 290", color=d.muted, width=2)
            for x in [270, 370]:
                d.rect(x-29, 290, 58, 64, fill=d.surface, radius=3)
        elif variant == "buoyancy":
            d.add(assets["vessel.beaker"].draw({"fill": p.get("fill", .65)}, monochrome=mono), 112, 14, 2.3)
            d.add(assets["measurement.dynamometer"].draw(monochrome=mono), 270, 3, .8)
            d.line(334, 123, 334, 230)
            d.circle(334, 258, 28, fill=d.surface)
        elif variant == "overflow":
            d.add(assets["vessel.overflow_cup"].draw({"fill": .75}, monochrome=mono), 90, 55, 1.8)
            d.add(assets["vessel.cylinder"].draw({"fill": p.get("fill", .2)}, monochrome=mono), 368, 150, 1.5)
            d.poly([(351,202.6),(488,202.6),(488,236)], color=d.blue, width=3)
            d.circle(234, 209, 23, fill=d.surface)
        else:
            d.add(assets["apparatus.stand"].draw(monochrome=mono), 90, 145, 1.25)
            from .instruments import _liquid
            tube = assets["vessel.hard_test_tube"].draw(monochrome=mono)
            group = d.element("g", transform="translate(247 75) scale(1.65) rotate(45 80 80)")
            group.append(tube.parts[0])
            polygon = [(59, 22), (101, 22), (101, 119)]+[(80+21*math.cos(i*math.pi/16),119+21*math.sin(i*math.pi/16)) for i in range(17)]
            angle = math.pi/4
            polygon = [(247+1.65*(80+(x-80)*math.cos(angle)-(y-80)*math.sin(angle)),75+1.65*(80+(x-80)*math.sin(angle)+(y-80)*math.cos(angle))) for x,y in polygon]
            _liquid(d, polygon, p.get("fill", 0), d.blue)
            outline = d.element("g", transform="translate(247 75) scale(1.65) rotate(45 80 80)")
            outline.extend(tube.parts[1:])
            d.line(241, 217, 355, 217, color=d.muted, width=3)
            d.ellipse(369, 217, 25, 10, color=d.muted)
            d.add(assets["apparatus.alcohol_lamp"].draw({"lit": p.get("lit", True)}, monochrome=mono), 247, 245, .8)
        return d
    elif variant in {"mirror", "refraction", "supply_demand"}:
        if variant == "supply_demand":
            d.arrow(95, 325, 557, 325)
            d.arrow(95, 325, 95, 47)
            d.line(125, 71, 528, 295, color=d.blue, width=3)
            d.line(125, 295, 528, 71, color=d.red, width=3)
        elif variant == "mirror":
            d.line(78, 215, 562, 215, width=3)
            d.hatch(78, 218, 484, 11)
            d.line(320, 36, 320, 369, dashed=True, color=d.muted)
            d.arrow(157, 56, 320, 215, color=d.blue)
            d.arrow(320, 215, 483, 56, color=d.blue)
        else:
            d.rect(78, 125, 484, 180, fill=d.glass, color=d.blue, width=1.5)
            d.line(274, 36, 274, 244, color=d.muted, dashed=True)
            d.line(365, 224, 365, 376, color=d.muted, dashed=True)
            d.arrow(178, 27, 274, 125, color=d.blue)
            d.arrow(274, 125, 365, 305, color=d.blue)
            d.arrow(365, 305, 428, 369, color=d.blue)
    elif variant in RECIPES:
        placed = {}
        for i, (asset_id, x, y, scale) in enumerate(RECIPES[variant]):
            if asset_id == "earth.pool":
                asset_id = "objects.pool"
            asset = assets[asset_id]
            params = {}
            if asset.renderer == "vessel" and p.get("fill", 0):
                params["fill"] = p["fill"]
            if asset_id in {"apparatus.alcohol_lamp", "apparatus.bunsen_burner"}:
                params["lit"] = p.get("lit", False)
            if "pendulum" in asset_id:
                params.update({"angle": p.get("angle", 25), "show_angle": p.get("show_angle", False)})
            drawing = asset.draw(params, monochrome=mono)
            d.add(drawing, x, y, scale)
            placed[i] = {a: (x+xx*scale, y+yy*scale) for a, (xx, yy) in drawing.anchors.items()}
        if variant == "pulley_blocks":
            d.line(*placed[0]["rope_left"], *placed[1]["top"])
            d.line(*placed[0]["rope_right"], *placed[2]["rope_attach"])
        elif variant == "filtration":
            d.poly([(293, 120), (337, 185), (381, 120)], color=d.surface, width=4)
            d.poly([(293, 120), (337, 185), (381, 120)], color=d.muted, width=1)
        elif variant in {"gas_wash", "gas_dry", "gas_up", "gas_down", "gas_water", "gas_preparation", "distillation"}:
            vessels = [i for i, item in enumerate(RECIPES[variant]) if item[0].startswith("vessel.")]
            for start, end in zip(vessels, vessels[1:]):
                a = placed[start].get("tube_outlet", placed[start].get("mouth", placed[start]["top"]))
                b = placed[end].get("tube_inlet", placed[end].get("mouth", placed[end]["top"]))
                y = min(a[1], b[1])-24
                d.poly([a, (a[0], y), (b[0], y), b], color=d.muted, width=3)
        elif variant == "projectile":
            d.path("M 260 179 Q 430 173 540 321", color=d.muted, dashed=True)
        elif variant in {"galvanic_cell", "electrolysis"}:
            d.rect(262, 178, 14, 132, fill=d.gold)
            d.rect(385, 178, 14, 132, fill=d.muted)
            d.poly([(269, 178), (269, 74), (280, 74)])
            d.poly([(388, 74), (392, 74), (392, 178)])
        elif variant == "water_cycle":
            d.arrow(482, 177, 482, 65, color=d.blue)
            d.arrow(444, 64, 382, 64, color=d.blue)
            for x in [302, 330, 358]:
                d.line(x, 140, x-8, 169, color=d.blue)
            d.arrow(220, 293, 395, 318, color=d.blue)
    else:
        raise DiagramError("diagram_unknown_template")
    return d
