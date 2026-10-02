"""Original language learning scaffolds, historical objects and economics charts."""

import math

from .extended_common import (
    canvas,
    text,
    panel,
    node,
    connect,
    axes,
    curve,
    numeric,
    box,
    ball,
)
from .schema import DiagramError

LANGUAGE_TEXT = {
    "english_syllables",
    "affixes",
    "dependency",
    "agreement",
    "subordinate_clauses",
    "paragraph_support",
    "general_specific",
    "narrative_view",
    "dialogue_turns",
    "rhetorical_relations",
}
ECON_DATA = {
    "utility",
    "cost_curves",
    "profit",
    "compound_growth",
    "annuity",
    "inventory_model",
}


def parameters(v):
    if v in LANGUAGE_TEXT:
        return {
            "items": {"type": "list", "required": True},
            **(
                {"edges": {"type": "list", "required": True}}
                if v == "dependency"
                else {}
            ),
        }
    if v in {"chronology", "parallel_civilizations"}:
        return {"events": {"type": "list", "required": True}}
    if v in {"causality", "evidence_compare"}:
        return {"items": {"type": "list", "required": True}}
    if v in ECON_DATA:
        return {"points": {"type": "list", "required": True}}
    if v in {"budget_line", "indifference", "consumer_optimum"}:
        return (
            numeric("income", 100, 10, 1000)
            | numeric("price_x", 10, 1, 100)
            | numeric("price_y", 10, 1, 100)
            | numeric("alpha", 0.5, 0.1, 0.9)
        )
    if v in {"tax_wedge", "price_controls", "surplus", "externality"}:
        return (
            numeric("demand_intercept", 100, 40, 200)
            | numeric("supply_intercept", 10, 0, 30)
            | numeric("tax", 20, 1, 35)
        )
    if v == "break_even":
        return (
            numeric("fixed_cost", 50, 1, 100)
            | numeric("unit_cost", 3, 0.1, 5)
            | numeric("unit_price", 6, 5.1, 12)
        )
    if v in {"input_output", "balance_sheet"}:
        return {"values": {"type": "list", "required": True}}
    if v == "ppf":
        return numeric("capacity_x", 100, 1, 1000) | numeric("capacity_y", 80, 1, 1000)
    if v == "opportunity_cost":
        return {"choices": {"type": "list", "required": True}}
    return {}


def _items(p, minimum=2, maximum=6):
    rows = p["items"]
    if not minimum <= len(rows) <= maximum or any(
        not isinstance(a, str) or not 1 <= len(a) <= 14 for a in rows
    ):
        raise DiagramError("diagram_invalid_items")
    return rows


def _grid(d, x, y, s, kind):
    d.rect(x, y, s, s, color=d.red, width=1.3)
    if kind != "stroke_grid":
        for a, b in [
            ((x + s / 2, y), (x + s / 2, y + s)),
            ((x, y + s / 2), (x + s, y + s / 2)),
        ]:
            d.line(*a, *b, color=d.red, width=0.7, dashed=True)
    if kind == "mi_grid":
        for a, b in [((x, y), (x + s, y + s)), ((x + s, y), (x, y + s))]:
            d.line(*a, *b, color=d.red, width=0.7, dashed=True)
    if kind == "palace_grid":
        for t in [1 / 3, 2 / 3]:
            d.line(x + s * t, y, x + s * t, y + s, color=d.red, width=0.7, dashed=True)
            d.line(x, y + s * t, x + s, y + s * t, color=d.red, width=0.7, dashed=True)


def _column(d, x, y, w=38, h=90):
    d.rect(x, y, w, h, fill=d.surface)
    for xx in [x + 8, x + 19, x + 30]:
        d.line(xx, y + 5, xx, y + h - 5, color=d.muted, width=1)
    d.rect(x - 5, y - 8, w + 10, 8, fill=d.gold)
    d.rect(x - 5, y + h, w + 10, 10, fill=d.gold)


def _wheel(d, x, y, r, spokes=8):
    d.circle(x, y, r, fill=d.glass)
    for i in range(spokes):
        a = i * 2 * math.pi / spokes
        d.line(x, y, x + r * math.cos(a), y + r * math.sin(a))
    d.circle(x, y, 6, fill=d.muted)


def draw(v, p, mono=False):
    d = canvas(mono)
    if v in {"tian_grid", "mi_grid", "palace_grid", "stroke_grid"}:
        for row in range(2):
            for col in range(4):
                _grid(d, 40 + col * 105, 42 + row * 111, 89, v)
        if v == "stroke_grid":
            paths = [
                "M 57 88 L 112 88",
                "M 188 56 L 188 114",
                "M 284 56 Q 302 71 272 116",
                "M 377 64 Q 400 104 429 112",
                "M 65 169 Q 72 173 76 188",
                "M 179 218 L 206 179",
                "M 270 179 L 318 179 L 318 226",
                "M 399 169 L 399 218 Q 404 233 418 215",
            ]
            for path in paths:
                d.path(path, width=4)
        text(d, "writing practice scaffold", 240, 295, size=12)
    elif v == "handwriting_lines":
        for start in [55, 165]:
            for i in range(4):
                d.line(
                    40,
                    start + i * 23,
                    440,
                    start + i * 23,
                    color=d.blue if i in {0, 3} else d.red,
                    width=1,
                    dashed=i == 1,
                )
        text(d, "ascender · x-height · baseline · descender", 240, 302, size=12)
    elif v == "radical_assembly":
        # Own coordinate strokes for 日 + 月 = 明; no downloaded calligraphy paths.
        def sun(x, y, s=1):
            d.path(
                f"M {x} {y} L {x} {y+72*s} M {x} {y} L {x+46*s} {y} L {x+46*s} {y+72*s} M {x} {y+35*s} L {x+46*s} {y+35*s} M {x} {y+72*s} L {x+46*s} {y+72*s}",
                width=4,
            )

        def moon(x, y, s=1):
            d.path(
                f"M {x+4*s} {y} Q {x+15*s} {y+55*s} {x-4*s} {y+82*s} M {x+4*s} {y} L {x+48*s} {y} L {x+48*s} {y+78*s} L {x+34*s} {y+68*s} M {x+10*s} {y+27*s} L {x+47*s} {y+27*s} M {x+10*s} {y+52*s} L {x+47*s} {y+52*s}",
                width=4,
            )

        sun(47, 111)
        moon(173, 111)
        sun(311, 111, 0.8)
        moon(369, 111, 0.8)
        text(d, "+", 130, 163, size=26)
        text(d, "=", 270, 163, size=26)
        text(d, "component assembly", 240, 278)
    elif v == "character_structure":
        for x, label, mode in [
            (80, "left/right", 0),
            (240, "top/bottom", 1),
            (400, "enclosure", 2),
        ]:
            _grid(d, x - 57, 89, 114, "tian_grid")
            if mode == 0:
                d.rect(x - 47, 100, 41, 91, fill=d.glass, width=1)
                d.rect(x + 6, 100, 41, 91, fill=d.gold, width=1)
            elif mode == 1:
                d.rect(x - 46, 100, 92, 39, fill=d.glass, width=1)
                d.rect(x - 46, 151, 92, 39, fill=d.gold, width=1)
            else:
                d.rect(x - 47, 100, 94, 92, fill=d.glass, width=1)
                d.rect(x - 24, 121, 48, 50, fill=d.gold, width=1)
            text(d, label, x, 251, size=12)
    elif v == "pinyin_syllable":
        for x, label, token, col in [
            (85, "initial", "sh", d.blue),
            (240, "final", "uang", d.green),
            (395, "tone", "4", d.gold),
        ]:
            node(d, token, x, 137, w=115, h=66, fill=col)
            text(d, label, x, 207)
        text(d, "sh + uang + 4 → shuàng", 240, 281, size=19)
    elif v == "tone_contours":
        shapes = [
            [(0, 0), (1, 0)],
            [(0, 0.7), (1, 0.1)],
            [(0, 0.4), (0.45, 0.9), (1, 0.15)],
            [(0, 0), (1, 0.9)],
        ]
        for i, pts in enumerate(shapes):
            x = 40 + i * 110
            d.rect(x, 60, 90, 165, fill=d.surface, width=1)
            for j in range(5):
                d.line(x, 77 + j * 30, x + 90, 77 + j * 30, color=d.muted, width=0.5)
            d.poly(
                [(x + 12 + a * 66, 77 + b * 120) for a, b in pts], color=d.blue, width=3
            )
            text(d, str(i + 1), x + 45, 269)
        d.facts["tone_numbers"] = [55, 35, 214, 51]
    elif v in LANGUAGE_TEXT:
        items = _items(p)
        n = len(items)
        if v in {"english_syllables", "affixes"}:
            spacing = 400 / n
            for i, label in enumerate(items):
                node(
                    d,
                    label,
                    40 + (i + 0.5) * spacing,
                    150,
                    w=spacing - 9,
                    h=54,
                    fill=[d.glass, d.gold, d.green][i % 3],
                )
                text(d, str(i + 1), 40 + (i + 0.5) * spacing, 215, size=12)
            text(d, "·" if v == "english_syllables" else "+", 240, 75, size=24)
        elif v in {"dependency", "agreement", "subordinate_clauses"}:
            spacing = 400 / n
            centers = [40 + (i + 0.5) * spacing for i in range(n)]
            for x, label in zip(centers, items):
                node(d, label, x, 187, w=spacing - 7, h=40)
            edges = p["edges"] if v == "dependency" else [[0, i] for i in range(1, n)]
            if len(edges) > 8 or any(
                not isinstance(e, list)
                or len(e) != 2
                or any(type(i) is not int or not 0 <= i < n for i in e)
                or e[0] == e[1]
                for e in edges
            ):
                raise DiagramError("diagram_invalid_dependency")
            for i, (head, dependent) in enumerate(edges):
                a, b = centers[head], centers[dependent]
                d.path(f"M {a} 158 Q {(a+b)/2} {100-i*12} {b} 158", color=d.blue)
                d.arrow(b - 4, 148, b, 158, color=d.blue)
            text(
                d,
                {
                    "dependency": "dependency links",
                    "agreement": "agreement links",
                    "subordinate_clauses": "main / dependent clause",
                }[v],
                240,
                270,
                size=13,
            )
        elif v in {"paragraph_support", "general_specific", "narrative_view"}:
            node(d, items[0], 240, 65, w=170, h=42, fill=d.gold)
            for i, label in enumerate(items[1:]):
                x = 45 + (i + 0.5) * 390 / (n - 1)
                node(d, label, x, 206, w=min(112, 390 / (n - 1) - 10), h=55)
                d.arrow(240, 89, x, 174, color=d.blue)
            text(
                d,
                {
                    "paragraph_support": "claim → support",
                    "general_specific": "general → specific",
                    "narrative_view": "perspective → observations",
                }[v],
                240,
                302,
                size=12,
            )
        elif v == "dialogue_turns":
            for i, label in enumerate(items):
                x = 143 if i % 2 == 0 else 337
                y = 42 + i * 42
                panel(d, x - 85, y, 170, 32, d.glass if i % 2 == 0 else d.gold)
                text(d, label, x, y + 22, size=12)
                d.poly(
                    [
                        (x - 64 if i % 2 == 0 else x + 64, y + 32),
                        (x - 77 if i % 2 == 0 else x + 77, y + 39),
                        (x - 55 if i % 2 == 0 else x + 55, y + 32),
                    ],
                    closed=True,
                    fill=d.glass if i % 2 == 0 else d.gold,
                    width=1,
                )
        else:
            for i, label in enumerate(items):
                x = 55 + (i + 0.5) * 370 / n
                node(d, label, x, 149, w=min(104, 370 / n - 8), h=62, fill=d.glass)
                text(d, str(i + 1), x, 240, size=12)
            d.arrow(45, 95, 438, 95, color=d.blue)
            text(d, "logical / rhetorical progression", 240, 294, size=12)
        d.facts["content_source"] = "task_parameters"
    elif v == "tense_timeline":
        d.arrow(35, 160, 446, 160)
        d.circle(240, 160, 6, fill=d.red)
        d.line(240, 90, 240, 230, color=d.red)
        for x, label in [(99, "past"), (240, "now"), (377, "future")]:
            text(d, label, x, 260)
            d.rect(x - 41, 124, 82, 19, fill=d.glass, width=1)
        text(d, "time reference", 240, 53)
    elif v in {"chronology", "parallel_civilizations"}:
        events = p["events"]
        if not 2 <= len(events) <= 8 or any(
            not isinstance(a, list)
            or len(a) != 3
            or type(a[0]) not in {int, float}
            or not isinstance(a[1], str)
            or len(a[1]) > 12
            or type(a[2]) is not int
            or a[2] not in {0, 1}
            for a in events
        ):
            raise DiagramError("diagram_invalid_events")
        lo = min(a[0] for a in events)
        hi = max(a[0] for a in events)
        if lo == hi:
            raise DiagramError("diagram_invalid_events")
        for y in ([130, 240] if v == "parallel_civilizations" else [174]):
            d.arrow(40, y, 441, y)
        for i, (year, label, row) in enumerate(events):
            x = 56 + (year - lo) / (hi - lo) * 367
            y = 130 + row * 110 if v == "parallel_civilizations" else 174
            sign = -1 if i % 2 == 0 else 1
            d.circle(x, y, 4, fill=d.gold)
            d.line(x, y, x, y + sign * 38, color=d.muted)
            text(d, label, x, y + sign * 51, size=10)
            text(d, f"{year:g}", x, y + sign * 66, size=10, color=d.muted)
        d.facts["events_source"] = "task_parameters"
    elif v in {"causality", "evidence_compare"}:
        items = _items(p, minimum=3, maximum=5)
        if v == "causality":
            for i, label in enumerate(items):
                x = 52 + i * 375 / (len(items) - 1)
                node(
                    d,
                    label,
                    x,
                    158,
                    w=86,
                    h=60,
                    fill=d.glass if i != len(items) - 1 else d.gold,
                )
            for i in range(len(items) - 1):
                connect(
                    d,
                    (96 + i * 375 / (len(items) - 1), 158),
                    (8 + (i + 1) * 375 / (len(items) - 1), 158),
                )
        else:
            for i, label in enumerate(items):
                panel(d, 55, 42 + i * 47, 370, 38, d.surface)
                text(d, label, 240, 67 + i * 47, size=13)
                d.rect(62, 51 + i * 47, 14, 17, fill=d.gold, width=1)
            text(d, "compare provenance / claims / context", 240, 310, size=12)
    elif v == "settlement":
        d.path("M 45 58 Q 237 96 420 53", color=d.blue, width=7)
        for x, y in [(97, 151), (194, 205), (307, 153), (371, 239)]:
            d.rect(x - 25, y, 50, 40, fill=d.gold)
            d.poly([(x - 31, y), (x, y - 41), (x + 31, y)], closed=True, fill=d.green)
            d.rect(x - 7, y + 15, 14, 25, fill=d.muted)
        d.ellipse(228, 141, 30, 15, fill=d.surface)
        d.hatch(43, 236, 100, 46, spacing=15)
        text(d, "generic early settlement", 240, 307, size=12)
    elif v == "city_gate":
        d.rect(45, 144, 390, 128, fill=d.gold)
        for x in [53, 345]:
            d.rect(x, 90, 82, 182, fill=d.surface)
            for j in range(3):
                d.rect(x + j * 28, 73, 17, 20, fill=d.surface)
        d.path("M 189 272 L 189 189 A 51 51 0 0 1 291 189 L 291 272", fill=d.muted)
        for y in [172, 217, 260]:
            d.line(135, y, 184, y, color=d.muted, width=1)
            d.line(296, y, 345, y, color=d.muted, width=1)
        text(d, "generic fortification", 240, 306, size=12)
    elif v == "arch_bridge":
        d.rect(32, 236, 417, 48, fill=d.glass)
        d.rect(40, 155, 400, 42, fill=d.gold)
        for x in [120, 240, 360]:
            d.path(
                f"M {x-50} 258 L {x-50} 212 A 50 50 0 0 1 {x+50} 212 L {x+50} 258",
                fill=d.glass,
            )
        for x in range(47, 438, 27):
            d.line(x, 157, x + 6, 195, color=d.muted, width=1)
        d.line(39, 147, 439, 147, width=4)
        text(d, "masonry arch structure", 240, 306)
    elif v == "irrigation_channels":
        d.rect(30, 45, 420, 240, fill=d.gold)
        d.path("M 45 75 L 435 75 M 240 75 L 240 272", color=d.blue, width=7)
        for y in [125, 194, 263]:
            d.line(65, y, 415, y, color=d.blue, width=4)
            for x in [80, 140, 330, 390]:
                d.line(x, y - 12, x, y - 37, color=d.green, width=3)
        d.arrow(46, 75, 138, 75, color=d.ink)
        text(d, "gravity-fed channels", 240, 309)
    elif v == "plough":
        d.line(83, 175, 317, 210, width=6)
        d.poly([(293, 184), (350, 217), (299, 261)], closed=True, fill=d.muted)
        d.poly([(205, 194), (252, 126), (310, 125)], width=5)
        _wheel(d, 137, 203, 34, 6)
        d.line(35, 262, 443, 262, color=d.gold, width=3)
        d.path("M 305 264 Q 347 238 400 264", color=d.gold, width=3)
        text(d, "generic plough mechanism", 240, 306)
    elif v == "waterwheel":
        _wheel(d, 240, 161, 105, 12)
        for i in range(12):
            a = i * math.pi / 6
            d.line(
                240 + 92 * math.cos(a),
                161 + 92 * math.sin(a),
                240 + 118 * math.cos(a),
                161 + 118 * math.sin(a),
                width=5,
            )
        d.rect(30, 258, 420, 27, fill=d.glass)
        d.arrow(55, 273, 165, 273, color=d.blue)
        d.poly([(202, 272), (240, 162), (278, 272)], width=5)
        text(d, "water-driven wheel", 240, 311)
    elif v == "spinning_wheel":
        _wheel(d, 175, 154, 93, 10)
        d.poly([(77, 275), (135, 240), (305, 240), (367, 275)], width=5)
        d.line(175, 154, 282, 154, width=4)
        d.rect(287, 115, 15, 100, fill=d.gold)
        d.path("M 175 65 L 294 118 L 294 210 L 175 247", color=d.blue, width=1.5)
        d.ellipse(338, 197, 27, 42, fill=d.surface)
        d.line(305, 154, 337, 176, color=d.muted, width=1)
    elif v == "loom":
        d.poly([(70, 275), (70, 65), (382, 65), (382, 275)], width=6)
        for x in range(85, 372, 15):
            d.line(x, 68, x, 254, color=d.gold, width=1)
        for y in range(143, 258, 12):
            d.line(85, y, 369, y, color=d.blue, width=2)
        d.poly(
            [(117, 121), (273, 115), (308, 130), (136, 136)],
            closed=True,
            fill=d.surface,
        )
        d.line(60, 260, 393, 260, width=5)
        text(d, "warp and weft", 240, 307)
    elif v == "pottery_wheel":
        d.ellipse(240, 213, 135, 32, fill=d.surface)
        d.rect(221, 239, 38, 41, fill=d.muted)
        d.path(
            "M 184 199 Q 177 151 194 116 Q 187 81 216 70 L 264 70 Q 293 81 286 116 Q 303 151 296 199 Z",
            fill=d.gold,
        )
        d.ellipse(240, 71, 25, 9, fill=d.muted)
        for y in [122, 150, 178]:
            d.path(f"M 189 {y} Q 240 {y+15} 291 {y}", color=d.red, width=1)
        d.path("M 137 229 Q 240 276 343 229", color=d.blue)
        text(d, "rotating clay form", 240, 310)
    elif v == "pottery_forms":
        for x, shape in [(80, 0), (240, 1), (400, 2)]:
            if shape == 0:
                d.path(
                    f"M {x-49} 146 Q {x-38} 234 {x} 238 Q {x+38} 234 {x+49} 146 Z",
                    fill=d.gold,
                )
                d.ellipse(x, 146, 49, 12, fill=d.surface)
            elif shape == 1:
                d.path(
                    f"M {x-20} 65 L {x-20} 109 Q {x-75} 147 {x-38} 255 L {x+38} 255 Q {x+75} 147 {x+20} 109 L {x+20} 65 Z",
                    fill=d.gold,
                )
                d.ellipse(x, 65, 22, 7, fill=d.surface)
            else:
                d.path(
                    f"M {x-22} 88 Q {x-78} 118 {x-45} 248 L {x+45} 248 Q {x+78} 118 {x+22} 88 Z",
                    fill=d.gold,
                )
                d.ellipse(x, 88, 24, 7, fill=d.surface)
            d.line(x - 21, 164, x + 21, 164, color=d.red, width=2)
        text(d, "generic shapes · no artifact reproduction", 240, 306, size=12)
    elif v == "bronze_vessel":
        d.path("M 130 113 Q 130 246 240 244 Q 350 246 350 113 Z", fill=d.green)
        d.ellipse(240, 113, 110, 27, fill=d.glass)
        for x in [171, 240, 309]:
            d.poly(
                [(x - 9, 237), (x - 16, 282), (x + 17, 282), (x + 10, 237)],
                closed=True,
                fill=d.green,
            )
        for x in [145, 315]:
            d.rect(x, 56, 20, 63, fill=d.green, radius=7)
            d.rect(x + 5, 67, 10, 35, fill=d.surface, width=1)
        for x in range(169, 317, 35):
            d.path(
                f"M {x} 153 l 20 0 l 0 21 l -13 0 l 0 -12 l 7 0", color=d.gold, width=2
            )
        d.facts["historical_object"] = (
            "generic_original_reconstruction_not_specific_artifact"
        )
    elif v == "trade_routes":
        d.path(
            "M 30 135 Q 100 39 226 90 Q 327 26 444 117 L 429 267 Q 326 235 288 173 Q 140 250 51 267 Z",
            fill=d.surface,
        )
        points = [(70, 151), (171, 119), (261, 163), (356, 113), (407, 213)]
        for i, (x, y) in enumerate(points):
            d.circle(x, y, 7, fill=d.gold)
            text(d, chr(65 + i), x, y - 15)
        d.poly(points, color=d.blue, width=3)
        d.path("M 73 154 Q 160 299 405 218", color=d.blue, dashed=True)
        text(d, "schematic trade network · no historical map claim", 240, 308, size=11)
    elif v == "movable_type":
        box(d, 53, 88, 200, 125, 25)
        for row in range(3):
            for col in range(5):
                x = 65 + col * 36
                y = 105 + row * 31
                d.rect(x, y, 29, 25, fill=d.gold, width=1)
                text(d, chr(65 + row * 5 + col), x + 14, y + 17, size=11)
        d.rect(312, 90, 107, 145, fill=d.surface)
        text(d, "A B C", 366, 134)
        text(d, "D E F", 366, 169)
        text(d, "G H I", 366, 204)
        connect(d, (271, 161), (303, 161))
        text(d, "reusable type → impression", 240, 293)
    elif v == "steam_factory":
        d.path(
            "M 80 265 L 80 141 L 165 91 L 165 141 L 250 91 L 250 141 L 335 91 L 335 265 Z",
            fill=d.surface,
        )
        d.rect(350, 80, 32, 185, fill=d.gold)
        for x in [102, 196, 290]:
            d.rect(x, 168, 26, 38, fill=d.glass)
            _wheel(d, x + 11, 242, 18, 6)
        for x, y, r in [(367, 58, 17), (388, 44, 21), (416, 30, 24)]:
            d.circle(x, y, r, fill=d.surface, color=d.muted, width=1)
        text(d, "generic industrial scene", 240, 309)
    elif v == "archaeology":
        for y, col in [(60, d.surface), (113, d.gold), (181, d.muted), (243, d.glass)]:
            d.rect(55, y, 370, 53, fill=col, width=1)
        for x, y in [(118, 144), (318, 214), (244, 268)]:
            d.path(f"M {x-19} {y} Q {x-15} {y+23} {x+19} {y} Z", fill=d.red, width=1)
        for x, y in [(180, 102), (368, 110)]:
            d.poly(
                [(x - 9, y), (x, y - 14), (x + 16, y)],
                closed=True,
                fill=d.muted,
                width=1,
            )
        d.arrow(39, 70, 39, 289, color=d.blue)
        text(d, "stratigraphic context", 240, 312)
    elif v == "artifact_views":
        for x in [88, 240, 392]:
            if x == 240:
                d.ellipse(x, 153, 48, 26, fill=d.gold)
                d.ellipse(x, 153, 23, 12, fill=d.surface)
            else:
                d.path(
                    f"M {x-20} 92 L {x-20} 130 Q {x-50} 173 {x-32} 234 L {x+32} 234 Q {x+50} 173 {x+20} 130 L {x+20} 92 Z",
                    fill=d.gold,
                )
                d.line(x - 20, 92, x + 20, 92)
            text(d, ["front", "top", "side"][[88, 240, 392].index(x)], x, 281)
        text(d, "generic object · independent orthographic views", 240, 311, size=11)
    elif v in {"ppf", "opportunity_cost"}:
        xy = axes(d, x_label="good X", y_label="good Y")
        if v == "ppf":
            curve(
                d, lambda x: 0.9 * math.sqrt(max(0, 1 - (x / 0.92) ** 2)), xy, end=0.92
            )
            d.circle(*xy(0.4, 0.55), 4, fill=d.green)
            d.circle(*xy(0.75, 0.75), 4, fill=d.red)
            text(d, f"X max {p['capacity_x']:g}", 333, 297, size=12)
            text(d, f"Y max {p['capacity_y']:g}", 105, 25, size=12)
            d.facts["model"] = "elliptical_production_frontier"
        else:
            choices = p["choices"]
            if (
                len(choices) != 2
                or any(
                    not isinstance(a, list)
                    or len(a) != 2
                    or any(type(n) not in {int, float} or n < 0 for n in a)
                    for a in choices
                )
                or choices[0][0] >= choices[1][0]
                or choices[0][1] <= choices[1][1]
            ):
                raise DiagramError("diagram_invalid_choices")
            mx = max(a[0] for a in choices) * 1.2
            my = max(a[1] for a in choices) * 1.2
            coords = [xy(a / mx, b / my) for a, b in choices]
            d.poly(coords, color=d.blue, width=3)
            a, b = coords
            d.poly([a, (b[0], a[1]), b], color=d.gold)
            d.facts["opportunity_cost_y_per_x"] = (choices[0][1] - choices[1][1]) / (
                choices[1][0] - choices[0][0]
            )
    elif v in {"budget_line", "indifference", "consumer_optimum"}:
        income, px, py, alpha = p["income"], p["price_x"], p["price_y"], p["alpha"]
        xy = axes(d, x_label="X", y_label="Y")
        d.line(*xy(0, 0.92), *xy(0.92, 0), color=d.blue, width=3)
        x, y = 0.92 * alpha, 0.92 * (1 - alpha)
        utility = x**alpha * y ** (1 - alpha)
        if v != "budget_line":
            for scale in ([0.75, 1, 1.25] if v == "indifference" else [1]):
                curve(
                    d,
                    lambda q, s=scale: ((utility * s) / max(q, 0.0001) ** alpha)
                    ** (1 / (1 - alpha)),
                    xy,
                    start=0.015,
                    end=0.98,
                    color=d.green,
                )
        if v == "consumer_optimum":
            d.circle(*xy(x, y), 5, fill=d.red)
            d.poly([xy(x, 0), xy(x, y), xy(0, y)], color=d.muted)
        text(d, f"{income/px:g}", 407, 294, size=12)
        text(d, f"{income/py:g}", 34, 65, size=12)
        d.facts.update(
            income=income,
            optimal_x=alpha * income / px,
            optimal_y=(1 - alpha) * income / py,
            utility_model="Cobb_Douglas",
        )
    elif v in ECON_DATA:
        pts = p["points"]
        if (
            not 3 <= len(pts) <= 40
            or any(
                not isinstance(a, list)
                or not 2 <= len(a) <= 4
                or len(a) != len(pts[0])
                or any(type(n) not in {int, float} or not math.isfinite(n) for n in a)
                for a in pts
            )
            or any(pts[i][0] >= pts[i + 1][0] for i in range(len(pts) - 1))
        ):
            raise DiagramError("diagram_invalid_plot_data")
        xmin, xmax = pts[0][0], pts[-1][0]
        ys = [n for a in pts for n in a[1:]]
        ymin = min(0, min(ys))
        ymax = max(ys)
        if ymax == ymin:
            raise DiagramError("diagram_invalid_plot_data")
        xy = axes(
            d,
            x_label=(
                "t" if v in {"compound_growth", "annuity", "inventory_model"} else "Q"
            ),
            y_label="value",
        )
        for col in range(1, len(pts[0])):
            coord = lambda a, b: xy(
                (a - xmin) / (xmax - xmin) * 0.92,
                0.05 + 0.85 * (b - ymin) / (ymax - ymin),
            )
            if v == "annuity":
                for a in pts:
                    d.arrow(
                        *coord(a[0], 0), *coord(a[0], a[col]), color=d.blue, width=2
                    )
            else:
                points = []
                for i, a in enumerate(pts):
                    if v == "inventory_model" and i and a[col] > pts[i - 1][col]:
                        points.append(coord(a[0], pts[i - 1][col]))
                    points.append(coord(a[0], a[col]))
                d.poly(points, color=[d.blue, d.red, d.green][col - 1], width=2.5)
        d.facts.update(data_source="task_parameters", points=pts)
    elif v in {"tax_wedge", "price_controls", "surplus", "externality"}:
        intercept = p["demand_intercept"]
        supply = p["supply_intercept"]
        tax = p["tax"]
        q0 = (intercept - supply) / 2
        price0 = (intercept + supply) / 2
        maxq = intercept
        maxprice = intercept + tax
        xy = axes(d, x_label="Q", y_label="P")
        coord = lambda q, price: xy(q / maxq * 0.94, price / maxprice * 0.94)
        d.line(*coord(0, intercept), *coord(intercept, 0), color=d.blue)
        d.line(*coord(0, supply), *coord(intercept - supply, intercept), color=d.red)
        if v in {"tax_wedge", "externality"}:
            qt = (intercept - supply - tax) / 2
            d.line(
                *coord(0, supply + tax),
                *coord(intercept - supply - tax, intercept),
                color=d.green,
            )
            d.line(
                *coord(qt, supply + qt),
                *coord(qt, intercept - qt),
                color=d.gold,
                width=5,
            )
            text(
                d,
                "tax" if v == "tax_wedge" else "social cost",
                330,
                83,
                size=12,
                color=d.green,
            )
            d.facts.update(quantity_before=q0, quantity_after=qt, wedge=tax)
        elif v == "price_controls":
            ceiling = price0 * 0.7
            d.line(
                *coord(0, ceiling),
                *coord(intercept, ceiling),
                color=d.gold,
                dashed=True,
            )
            d.facts.update(
                ceiling=ceiling, shortage=(intercept - ceiling) - (ceiling - supply)
            )
        else:
            d.poly(
                [coord(0, intercept), coord(0, price0), coord(q0, price0)],
                closed=True,
                fill=d.glass,
                color=d.blue,
                width=1,
            )
            d.poly(
                [coord(0, supply), coord(0, price0), coord(q0, price0)],
                closed=True,
                fill=d.gold,
                color=d.red,
                width=1,
            )
        d.circle(*coord(q0, price0), 4, fill=d.ink)
    elif v == "circular_flow":
        node(d, "households", 105, 160, w=115, h=90)
        node(d, "firms", 375, 160, w=115, h=90)
        d.path("M 105 107 L 105 65 L 375 65 L 375 107", color=d.blue, width=3)
        d.arrow(375, 81, 375, 104, color=d.blue)
        d.path("M 375 213 L 375 265 L 105 265 L 105 213", color=d.green, width=3)
        d.arrow(105, 244, 105, 217, color=d.green)
        d.arrow(167, 139, 313, 139, color=d.gold)
        d.arrow(313, 182, 167, 182, color=d.red)
        text(d, "labor / resources", 240, 47)
        text(d, "goods / services", 240, 299)
        text(d, "spending", 240, 125, size=12)
        text(d, "income", 240, 205, size=12)
    elif v in {"input_output", "balance_sheet"}:
        data = p["values"]
        if not 2 <= len(data) <= 4 or any(
            not isinstance(a, list)
            or len(a) != len(data[0])
            or not 2 <= len(a) <= 4
            or any(type(n) not in {int, float} for n in a)
            for a in data
        ):
            raise DiagramError("diagram_invalid_financial_data")
        if v == "balance_sheet" and any(
            len(a) != 3 or abs(a[0] - a[1] - a[2]) > 1e-6 for a in data
        ):
            raise DiagramError("diagram_unbalanced_accounts")
        if v == "input_output" and len(data) != len(data[0]):
            raise DiagramError("diagram_non_square_input_output")
        h = 180 / len(data)
        w = 300 / len(data[0])
        for i, row in enumerate(data):
            for j, n in enumerate(row):
                y = 70 + i * h
                d.rect(
                    90 + j * w, y, w, h, fill=d.glass if i == j else d.surface, width=1
                )
                text(d, f"{n:g}", 90 + (j + 0.5) * w, y + h / 2 + 5, size=15)
        text(
            d,
            "sector flows" if v == "input_output" else "assets | liabilities | equity",
            240,
            36,
            size=14,
        )
        text(d, "provided accounting data", 240, 293, size=12)
        d.facts["data_source"] = "task_parameters"
    elif v == "break_even":
        fixed, cost, price = p["fixed_cost"], p["unit_cost"], p["unit_price"]
        q = fixed / (price - cost)
        maxq = q * 1.6
        maxy = price * maxq
        xy = axes(d, x_label="Q", y_label="value")
        coord = lambda a, b: xy(a / maxq * 0.9, b / maxy * 0.9)
        d.line(*coord(0, 0), *coord(maxq, price * maxq), color=d.blue)
        d.line(*coord(0, fixed), *coord(maxq, fixed + cost * maxq), color=d.red)
        d.circle(*coord(q, price * q), 5, fill=d.gold)
        d.facts.update(break_even_quantity=q, revenue_at_break_even=q * price)
    elif v == "dupont":
        node(d, "ROE", 240, 50, w=90, h=38, fill=d.gold)
        for x, label in [
            (83, "net margin"),
            (240, "asset turnover"),
            (397, "equity multiplier"),
        ]:
            node(d, label, x, 164, w=136, h=51)
            d.line(240, 71, x, 137, color=d.muted)
        text(d, "profit / sales", 83, 245, size=12)
        text(d, "sales / assets", 240, 245, size=12)
        text(d, "assets / equity", 397, 245, size=12)
        text(d, "ROE = margin × turnover × multiplier", 240, 302, size=12)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
