"""Original complete experimental arrangements with explicit physical connections."""

import math
import statistics

from .extended_common import (
    canvas,
    text,
    ball,
    vessel,
    stand,
    burner,
    pipe,
    plant,
    axes,
    panel,
    node,
    connect,
    numeric,
)
from .instruments import vessel as glassware
from .extended_deferred import pedigree, parameters as component_parameters
from .schema import DiagramError

VARIANTS = {
    "distillation",
    "gas_water",
    "gas_up",
    "gas_down",
    "gas_preparation",
    "gas_wash",
    "gas_dry",
    "galvanic_cell",
    "electrolysis",
    "chromatography",
    "flame_test",
    "extraction",
    "crystallization",
    "manometer",
    "thermal",
    "conduction",
    "rheostat_wiring",
    "induction",
    "transformer",
    "lens_bench",
    "concave_lens",
    "pinhole",
    "prism",
    "double_slit",
    "germination",
    "respiration",
    "transpiration",
    "enzyme",
    "pedigree",
    "experiment_control",
    "box_comparison",
    "scatter_fit",
}


def parameters(v):
    if v == "pedigree":
        return component_parameters(v)
    if v == "experiment_control":
        return {"items": {"type": "list", "required": True}}
    if v == "box_comparison":
        return {
            "groups": {"type": "list", "required": True},
            "labels": {"type": "list", "default": []},
        }
    if v == "scatter_fit":
        return {"points": {"type": "list", "required": True}}
    if v == "manometer":
        return numeric("difference", 40, -70, 70)
    if v == "lens_bench":
        return numeric("object_distance", 150, 110, 180) | numeric(
            "focal_length", 65, 40, 85
        )
    return {}


def _flask(d, x, y, scale=0.8, fill=0.3):
    d.add(glassware("conical_flask", {"fill": fill}, d.monochrome), x, y, scale)


def _reaction(d):
    _flask(d, 25, 148, 0.9, 0.28)
    d.rect(82, 174, 30, 8, fill=d.muted)
    pipe(d, [(97, 174), (97, 91), (220, 91)])


def _inverted_jar(d, x, y, w=84, h=143):
    d.path(
        f"M {x} {y+h} L {x} {y+12} Q {x+w/2} {y-10} {x+w} {y+12} L {x+w} {y+h}",
        fill=d.glass,
    )
    d.line(x - 5, y + h, x + w + 5, y + h, width=3)


def draw(v, p, mono=False):
    d = canvas(mono)
    if v == "distillation":
        stand(d, 64, 70, 215)
        d.add(glassware("distilling_flask", {"fill": 0.32}, mono), 93, 117, 0.78)
        burner(d, 156, 250)
        pipe(d, [(156, 145), (156, 83), (215, 83), (356, 180)])
        d.poly(
            [(207, 88), (221, 68), (351, 163), (337, 183)], closed=True, fill=d.glass
        )
        pipe(d, [(338, 178), (372, 155), (398, 155)])
        pipe(d, [(225, 92), (209, 119), (229, 132)])
        d.arrow(395, 153, 367, 155, color=d.blue)
        d.arrow(217, 115, 226, 129, color=d.blue)
        _flask(d, 310, 178, 0.75, 0.2)
        d.line(155, 50, 155, 101, color=d.red, width=3)
        text(d, "coolant in", 410, 133, size=11)
        text(d, "out", 243, 141, size=11)
    elif v in {
        "gas_water",
        "gas_up",
        "gas_down",
        "gas_preparation",
        "gas_wash",
        "gas_dry",
    }:
        _reaction(d)
        if v == "gas_water":
            vessel(d, 256, 177, 175, 103, 0.72)
            _inverted_jar(d, 300, 87, 80, 160)
            pipe(d, [(220, 91), (239, 91), (239, 260), (330, 260), (330, 230)])
            for y in [221, 208, 194]:
                d.circle(330, y, 3, fill=d.surface, color=d.blue, width=1)
            d.facts["collection"] = "water_displacement_opening_below_water"
        elif v == "gas_up":
            vessel(d, 300, 103, 100, 160, 0)
            pipe(d, [(220, 91), (350, 91), (350, 242)])
            d.arrow(375, 235, 375, 80, color=d.gold)
            text(d, "denser than air", 350, 302, size=12)
            d.facts["collection"] = "upright_jar_inlet_near_bottom_air_exits_top"
        elif v == "gas_down":
            _inverted_jar(d, 300, 73, 100, 190)
            pipe(d, [(220, 91), (247, 91), (247, 282), (351, 282), (351, 90)])
            d.arrow(377, 110, 377, 277, color=d.gold)
            text(d, "lighter than air", 350, 310, size=12)
            d.facts["collection"] = "inverted_jar_inlet_near_top_air_exits_bottom"
        elif v == "gas_preparation":
            # Addition tube passes the stopper and its lower end is below liquid.
            d.path("M 69 82 L 61 105 L 78 105 L 70 82 Z", fill=d.glass)
            pipe(d, [(70, 105), (70, 245)])
            vessel(d, 307, 137, 90, 133, 0)
            pipe(d, [(220, 91), (350, 91), (350, 249)])
            text(d, "liquid-sealed addition tube", 150, 305, size=11)
        elif v == "gas_wash":
            vessel(d, 260, 127, 107, 143, 0.57)
            d.rect(254, 119, 119, 13, fill=d.muted)
            pipe(d, [(220, 91), (284, 91), (284, 246)])
            pipe(d, [(343, 154), (343, 89), (435, 89)])
            for y in [226, 207, 188]:
                d.circle(284, y, 4, fill=d.surface, color=d.blue, width=1)
            d.arrow(384, 89, 428, 89, color=d.gold)
            d.facts["wash_connections"] = "inlet_submerged_outlet_above_liquid"
        else:
            d.path(
                "M 256 69 L 286 69 L 286 99 Q 327 116 286 231 L 286 266 L 256 266 L 256 231 Q 215 116 256 99 Z",
                fill=d.glass,
            )
            for i in range(20):
                d.circle(
                    252 + (i * 13) % 37, 116 + (i * 23) % 100, 4, fill=d.gold, width=1
                )
            pipe(d, [(220, 91), (237, 91), (237, 50), (271, 50), (271, 69)])
            pipe(d, [(271, 266), (271, 285), (419, 285)])
            d.arrow(380, 285, 420, 285, color=d.gold)
            text(d, "drying agent", 375, 165, size=12)
    elif v == "galvanic_cell":
        for x in [55, 310]:
            vessel(d, x, 152, 115, 124, 0.64)
        d.rect(93, 116, 17, 125, fill=d.muted)
        d.rect(350, 116, 17, 125, fill=d.gold)
        pipe(d, [(150, 221), (150, 132), (333, 132), (333, 221)])
        d.poly([(101, 116), (101, 58), (358, 58), (358, 116)])
        d.circle(240, 58, 22, fill=d.surface)
        text(d, "V", 240, 65, size=20)
        text(d, "Zn", 75, 106)
        text(d, "Cu", 391, 106)
        d.arrow(127, 58, 193, 58, color=d.blue)
        text(d, "e⁻", 157, 40, size=13)
        text(d, "salt bridge", 242, 116, size=12)
    elif v == "electrolysis":
        vessel(d, 110, 130, 260, 151, 0.62)
        for x, col in [(178, d.red), (300, d.blue)]:
            d.rect(x, 100, 14, 149, fill=col)
        d.poly([(185, 100), (185, 53), (221, 53)])
        d.poly([(307, 100), (307, 53), (259, 53)])
        d.line(230, 30, 230, 78, width=3)
        d.line(250, 40, 250, 68, width=3)
        text(d, "+", 205, 80)
        text(d, "−", 278, 80)
        for x in [171, 321]:
            for i in range(5):
                d.circle(
                    x + (i % 2) * 4,
                    233 - i * 17,
                    3,
                    fill=d.surface,
                    color=d.blue,
                    width=0.8,
                )
        text(d, "electrode products depend on electrolyte", 240, 309, size=11)
    elif v == "chromatography":
        vessel(d, 130, 79, 220, 204, 0.13)
        d.rect(205, 71, 70, 191, fill=d.surface)
        d.line(211, 236, 268, 236, color=d.muted, width=1)
        for y, col in [(205, d.blue), (153, d.red), (116, d.gold)]:
            d.ellipse(240, y, 13, 5, fill=col, width=1)
        d.line(210, 97, 270, 97, color=d.blue, dashed=True)
        text(d, "baseline above solvent", 240, 311, size=12)
        d.facts["baseline_above_solvent"] = True
    elif v == "flame_test":
        burner(d, 210, 229)
        d.line(246, 204, 390, 80, color=d.muted, width=3)
        d.circle(237, 211, 10, color=d.muted, width=2)
        panel(d, 70, 40, 144, 50, d.surface)
        text(d, "clean wire loop", 142, 70, size=13)
        d.arrow(153, 94, 226, 195, color=d.gold)
        d.facts["flame_color"] = "generic_not_element_identification"
    elif v == "extraction":
        _flask(d, 20, 150, 0.85, 0.4)
        d.path(
            "M 269 64 L 269  90 Q 211 119 269 197 L 269 248 L 282 248 L 282 197 Q 340 119 282 90 L 282 64 Z",
            fill=d.glass,
        )
        d.path("M 242 137 Q 240 163 271 191 L 280 191 Q 309 163 309 137 Z", fill=d.blue)
        d.path("M 243 132 Q 240 111 270 99 L 280 99 Q 308 111 308 132 Z", fill=d.gold)
        d.line(253, 223, 300, 223, width=4)
        vessel(d, 330, 220, 90, 60, 0.28)
        d.arrow(142, 171, 217, 151, color=d.gold)
        text(d, "immiscible layers", 285, 30, size=12)
    elif v == "crystallization":
        vessel(d, 35, 122, 100, 140, 0.55)
        d.path("M 204 154 Q 256 238 308 154 Z", fill=d.glass)
        d.ellipse(256, 154, 52, 10, fill=d.blue)
        for x, y in [(368, 180), (408, 226), (363, 257)]:
            d.poly(
                [(x, y - 17), (x + 18, y), (x + 9, y + 21), (x - 16, y + 10)],
                closed=True,
                fill=d.glass,
                color=d.blue,
            )
        connect(d, (146, 185), (191, 185))
        connect(d, (316, 185), (342, 185))
        text(d, "concentrate → cool → crystals", 240, 306, size=13)
    elif v == "manometer":
        diff = p["difference"]
        left = 187 - diff / 2
        right = 187 + diff / 2
        d.path(
            "M 143 55 L 143 245 Q 143 284 194 284 Q 245 284 245 245 L 245 55",
            color=d.glass,
            width=8,
        )
        d.path(
            f"M 143 {left} L 143 245 Q 143 284 194 284 Q 245 284 245 245 L 245 {right}",
            color=d.blue,
            width=6,
        )
        for y in [left, right]:
            d.line(130, y, 330, y, color=d.muted, dashed=True, width=1)
        d.arrow(312, left, 312, right, color=d.gold, double=True)
        text(d, "Δh", 346, 193)
        text(d, "Δp = ρgΔh", 362, 70)
        d.facts["height_difference"] = diff
    elif v == "thermal":
        panel(d, 136, 240, 208, 40, d.muted)
        vessel(d, 165, 112, 150, 126, 0.65)
        d.rect(225, 40, 12, 169, fill=d.glass)
        d.circle(231, 210, 9, fill=d.red)
        d.line(231, 202, 231, 90, color=d.red, width=3)
        for y in range(50, 135, 10):
            d.line(240, y, 250, y, width=1)
        d.circle(309, 260, 8, fill=d.gold)
        text(d, "controlled heating + temperature probe", 240, 310, size=12)
    elif v == "conduction":
        burner(d, 112, 229)
        d.rect(90, 165, 340, 17, fill=d.muted)
        for i in range(6):
            x = 147 + i * 40
            d.circle(x, 182, 4, fill=d.gold)
            d.line(x, 186, x, 212, width=2)
        d.arrow(150, 145, 371, 145, color=d.red, width=3)
        text(d, "heat along the rod", 282, 90)
    elif v == "rheostat_wiring":
        d.poly([(60, 90), (60, 245), (421, 245), (421, 90), (350, 90)])
        d.rect(152, 74, 196, 32, fill=d.glass)
        for x in range(160, 344, 10):
            d.line(x, 77, x + 5, 103, color=d.muted, width=1)
        d.line(60, 90, 152, 90)
        d.arrow(269, 40, 269, 73, color=d.blue)
        d.poly([(269, 40), (421, 40), (421, 90)], color=d.blue)
        d.circle(243, 245, 22, fill=d.surface)
        d.line(229, 230, 258, 259)
        d.line(258, 230, 229, 259)
        d.line(60, 166, 60, 190, color="#fff", width=8)
        d.line(40, 171, 80, 171, width=3)
        d.line(48, 186, 72, 186, width=3)
        d.facts["rheostat_terminals"] = "one_end_and_slider"
    elif v == "induction":
        for y in [100, 228]:
            d.line(65, y, 425, y, width=4)
        d.line(65, 100, 65, 228)
        d.circle(65, 164, 22, fill=d.surface)
        text(d, "G", 65, 171, size=20)
        d.line(304, 91, 304, 237, color=d.gold, width=7)
        d.arrow(330, 162, 420, 162, color=d.red, width=3)
        for x in [137, 201, 263, 371]:
            for y in [135, 193]:
                d.line(x - 4, y - 4, x + 4, y + 4, color=d.blue, width=1)
                d.line(x - 4, y + 4, x + 4, y - 4, color=d.blue, width=1)
        text(d, "B into page", 240, 40)
    elif v == "transformer":
        d.rect(136, 60, 208, 202, fill=d.muted)
        d.rect(173, 97, 134, 128, fill="#fff")
        for side, count in [(136, 5), (344, 10)]:
            for i in range(count):
                y = 104 + i * 119 / max(1, count - 1)
                d.path(
                    f"M {side-18} {y} Q {side} {y-14} {side+18} {y} Q {side} {y+14} {side-18} {y}",
                    color=d.gold,
                    width=2,
                )
        d.line(118, 104, 60, 104)
        d.line(118, 223, 60, 223)
        d.circle(60, 164, 27, fill=d.surface)
        text(d, "~", 60, 173, size=25)
        text(d, "N₁", 90, 60)
        text(d, "N₂", 390, 60)
        d.facts["turns_ratio"] = 2
    elif v == "lens_bench":
        u, f = p["object_distance"], p["focal_length"]
        if u <= f:
            raise DiagramError("diagram_invalid_lens_distances")
        image = f * u / (u - f)
        scale = min(1, 180 / image)
        lens = 225
        axis = 166
        height = 50
        obj = lens - u * scale
        screen = lens + image * scale
        d.line(30, axis, 450, axis, color=d.muted, dashed=True)
        d.ellipse(lens, axis, 10, 108, fill=d.glass)
        d.arrow(obj, axis, obj, axis - height, color=d.red, width=3)
        d.line(screen, 50, screen, 282, width=4)
        d.arrow(screen, axis, screen, axis + height * image / u, color=d.blue, width=3)
        d.poly(
            [
                (obj, axis - height),
                (lens, axis - height),
                (screen, axis + height * image / u),
            ],
            color=d.gold,
        )
        d.line(
            obj, axis - height, screen, axis + height * image / u, color=d.gold, width=1
        )
        d.facts.update(
            object_distance=u,
            focal_length=f,
            image_distance=image,
            magnification=-image / u,
        )
    elif v == "concave_lens":
        d.line(30, 160, 450, 160, color=d.muted, dashed=True)
        d.path("M 227  60 Q 252 160 227 260 L 253 260 Q 228 160 253 60 Z", fill=d.glass)
        d.arrow(75, 160, 75, 90, color=d.red, width=3)
        d.poly([(75, 90), (240, 90), (430, 40)], color=d.gold)
        d.line(75, 90, 430, 240, color=d.gold)
        d.line(140, 117, 240, 90, color=d.blue, dashed=True)
        d.arrow(140, 160, 140, 117, color=d.blue, width=3)
        text(d, "virtual upright reduced image", 240, 305, size=12)
    elif v == "pinhole":
        d.line(240, 40, 240, 156, width=5)
        d.line(240, 164, 240, 280, width=5)
        d.arrow(75, 220, 75, 90, color=d.red, width=4)
        d.line(411, 42, 411, 278, width=5)
        d.line(75, 90, 405, 230, color=d.gold)
        d.line(75, 220, 405, 100, color=d.gold)
        d.arrow(405, 100, 405, 230, color=d.blue, width=4)
        text(d, "inverted image", 359, 305)
    elif v == "prism":
        d.poly([(180, 236), (276, 60), (375, 236)], closed=True, fill=d.glass)
        d.arrow(30, 140, 225, 153, color=d.gold, width=4)
        for i, col in enumerate([d.red, d.gold, d.green, d.blue]):
            d.poly(
                [(225, 153), (318, 178 + i * 3), (447, 218 + i * 13)],
                color=col,
                width=2,
            )
        d.facts["dispersion"] = "schematic_wavelength_dependent_refraction"
    elif v == "double_slit":
        panel(d, 25, 141, 60, 38, d.surface)
        d.arrow(85, 160, 192, 160, color=d.red)
        for a, b in [(45, 130), (138, 182), (190, 276)]:
            d.line(198, a, 198, b, width=5)
        for sy in [134, 186]:
            for r in [40, 70, 100, 130]:
                d.path(
                    f"M 208 {sy-r*.6} A {r} {r} 0 0 1 208 {sy+r*.6}",
                    color=d.blue,
                    width=1,
                )
        d.line(412, 30, 412, 285, width=4)
        for y in range(45, 276, 24):
            d.line(405, y, 420, y, color=d.red, width=6)
    elif v == "germination":
        for i, x in enumerate([75, 240, 405]):
            vessel(d, x - 40, 173, 80, 94, 0.12)
            d.ellipse(x, 227, 13, 8, fill=d.gold)
            if i:
                plant(d, x, 227, 50 if i == 1 else 135)
            text(d, ["seed", "root emerges", "seedling"][i], x, 303, size=12)
            if i:
                connect(d, (x - 118, 180), (x - 50, 180))
        d.facts["sequence"] = "germination_morphology"
    elif v == "respiration":
        _flask(d, 45, 124, 1, 0.05)
        for i in range(7):
            d.ellipse(
                103 + (i * 13) % 48, 240 - (i % 2) * 10, 6, 4, fill=d.gold, width=1
            )
        d.rect(107, 149, 37, 12, fill=d.muted)
        pipe(d, [(126, 149), (126, 70), (288, 70), (288, 231), (345, 231), (345, 90)])
        d.path("M 288 181 L 288 231 L 345 231 L 345 151", color=d.blue, width=4)
        text(d, "sealed respirometer", 240, 303)
        text(d, "CO₂ absorbent", 75, 110, size=11)
        d.facts["control_requirement"] = "matched_temperature_and_non_respiring_control"
    elif v == "transpiration":
        vessel(d, 90, 177, 113, 104, 0.7)
        plant(d, 147, 209, 130)
        d.path(
            "M 65 198 Q 50 36 149 34 Q 245 36 227 198 Z",
            fill="none",
            color=d.blue,
            width=2,
        )
        for x, y in [(96, 70), (195, 90), (212, 150)]:
            d.path(
                f"M {x} {y} q -9 16 0 17 q 9 -1 0 -17 Z",
                fill=d.glass,
                color=d.blue,
                width=1,
            )
        d.line(94, 207, 197, 207, color=d.gold, width=5)
        text(d, "sealed soil surface", 345, 208, size=12)
        d.line(205, 208, 267, 208, color=d.muted, width=1)
        text(d, "condensation from transpired water", 240, 309, size=12)
    elif v == "enzyme":
        for x, denatured in [(132, False), (360, True)]:
            if not denatured:
                d.path(
                    f"M {x-55} 180 Q {x- 80}  100 {x- 20} 103 L {x} 135 L {x+ 20} 103 Q {x+80} 100 {x+55} 180 Q {x} 241 {x-55} 180 Z",
                    fill=d.glass,
                )
            else:
                d.path(
                    f"M {x-55} 181 Q {x-75} 114 {x-18} 118 Q {x+8} 82 {x+24} 136 Q {x+83} 120 {x+55} 181 Q {x} 235 {x-55} 181 Z",
                    fill=d.glass,
                )
            d.poly([(x - 18, 50), (x + 18, 50), (x, 80)], closed=True, fill=d.gold)
            text(d, "active site" if not denatured else "altered shape", x, 278)
        d.facts["comparison"] = "active_site_shape_not_quantitative_activity"
    elif v == "pedigree":
        pedigree(d, p)
        d.rect(32, 293, 11, 11, fill=d.ink, width=1)
        text(d, "affected", 82, 303, size=11)
        d.circle(180, 299, 6, fill="#fff", width=1)
        text(d, "unaffected", 230, 303, size=11)
    elif v == "experiment_control":
        items = p["items"]
        if len(items) != 3 or any(not isinstance(s, str) or len(s) > 18 for s in items):
            raise DiagramError("diagram_invalid_controls")
        for x, label, col in [(137, items[0], d.blue), (343, items[1], d.gold)]:
            vessel(d, x - 50, 111, 100, 140, 0.5, color=col)
            text(d, label, x, 90, size=12)
            text(d, items[2], x, 286, size=12)
        text(d, "one variable · matched conditions", 240, 30, size=12)
    elif v == "box_comparison":
        groups = p["groups"]
        labels = p.get("labels") or [chr(65 + i) for i in range(len(groups))]
        if (
            not 2 <= len(groups) <= 4
            or len(labels) != len(groups)
            or any(not isinstance(s, str) or len(s) > 10 for s in labels)
            or any(
                not isinstance(a, list)
                or not 4 <= len(a) <= 30
                or any(type(n) not in {int, float} for n in a)
                for a in groups
            )
        ):
            raise DiagramError("diagram_invalid_groups")
        lo = min(min(a) for a in groups)
        hi = max(max(a) for a in groups)
        if lo == hi:
            lo -= 1
            hi += 1
        coord = lambda q: 65 + (q - lo) / (hi - lo) * 350
        for i, (row, label) in enumerate(zip(groups, labels)):
            y = 70 + i * 180 / (len(groups) - 1)
            q1, med, q3 = statistics.quantiles(row, n=4, method="inclusive")
            low = min(row)
            high = max(row)
            d.line(coord(low), y, coord(high), y)
            for q in [low, high]:
                d.line(coord(q), y - 13, coord(q), y + 13)
            d.rect(coord(q1), y - 20, coord(q3) - coord(q1), 40, fill=d.glass)
            d.line(coord(med), y - 20, coord(med), y + 20, color=d.red, width=3)
            text(d, label, 34, y + 4, size=12)
        d.facts["whisker_rule"] = "minimum_maximum"
    elif v == "scatter_fit":
        pts = p["points"]
        if not 3 <= len(pts) <= 40 or any(
            not isinstance(a, list)
            or len(a) != 2
            or any(type(n) not in {int, float} for n in a)
            for a in pts
        ):
            raise DiagramError("diagram_invalid_points")
        xs = [a for a, b in pts]
        ys = [b for a, b in pts]
        mx = statistics.mean(xs)
        my = statistics.mean(ys)
        ss = sum((a - mx) ** 2 for a in xs)
        if ss == 0:
            raise DiagramError("diagram_degenerate_regression")
        slope = sum((a - mx) * (b - my) for a, b in pts) / ss
        intercept = my - slope * mx
        lo, hi = min(xs), max(xs)
        ylo = min(ys + [slope * lo + intercept, slope * hi + intercept])
        yhi = max(ys + [slope * lo + intercept, slope * hi + intercept])
        if ylo == yhi:
            ylo -= 1
            yhi += 1
        xy = axes(d)
        coord = lambda x, y: xy(
            0.05 + 0.9 * (x - lo) / (hi - lo), 0.05 + 0.9 * (y - ylo) / (yhi - ylo)
        )
        for x, y in pts:
            d.circle(*coord(x, y), 4, fill=d.blue, width=1)
        d.line(
            *coord(lo, slope * lo + intercept),
            *coord(hi, slope * hi + intercept),
            color=d.red,
        )
        d.facts.update(slope=slope, intercept=intercept, data_source="task_parameters")
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
