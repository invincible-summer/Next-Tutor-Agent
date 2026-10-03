"""Dedicated implementations for first-edition deferred component structures."""

import math

from .extended_common import (
    canvas,
    text,
    ball,
    box,
    vessel,
    plant,
    panel,
    node,
    connect,
    numeric,
    axes,
)
from .drawing import Drawing
from .schema import DiagramError

VARIANTS = {
    "biology": {"root_tip"},
    "circuit": {"mosfet"},
    "earth": {
        "moon_phase",
        "plate_boundary",
        "subduction",
        "sea_breeze",
        "valley_breeze",
        "wind",
        "watershed",
    },
    "chemistry": {
        "isotope",
        "electron_pair",
        "ion",
        "ester",
        "solution",
        "precipitate",
        "suspension",
        "emulsion",
        "diamond",
        "graphite",
        "diffusion",
    },
    "graph": {
        "cashflow",
        "gantt",
        "pedigree",
        "ecosystem",
        "hasse",
        "bipartite",
        "er",
        "class",
        "double_list",
    },
    "logic": {"decoder", "register"},
    "objects": {"heat_exchanger", "truck", "train"},
}


def parameters(v):
    if v == "ion":
        return numeric("atomic_number", 11, 3, 18, True) | numeric(
            "charge", 1, -2, 3, True
        )
    if v in {"cashflow", "gantt"}:
        return {
            "values": {"type": "list", "required": True},
            "items": {"type": "list", "required": True},
        }
    if v in {"ecosystem", "hasse", "bipartite", "er", "class", "double_list"}:
        return {
            "items": {"type": "list", "required": True},
            "edges": {"type": "list", "default": []},
        }
    if v == "pedigree":
        return {
            "people": {"type": "list", "required": True},
            "couples": {"type": "list", "required": True},
            "children": {"type": "list", "required": True},
        }
    return {}


def _items(p):
    items = p["items"]
    if not 2 <= len(items) <= 6 or any(
        not isinstance(s, str) or not 1 <= len(s) <= 12 for s in items
    ):
        raise DiagramError("diagram_invalid_items")
    edges = p.get("edges", [])
    if len(edges) > 12 or any(
        not isinstance(e, list)
        or len(e) != 2
        or any(type(i) is not int or not 0 <= i < len(items) for i in e)
        or e[0] == e[1]
        for e in edges
    ):
        raise DiagramError("diagram_invalid_edges")
    return items, edges


def pedigree(d, p):
    people = p["people"]
    couples = p["couples"]
    children = p["children"]
    # Person: [label, sex (0=female,1=male), affected (0/1), generation (0..2)].
    if not 2 <= len(people) <= 9 or any(
        not isinstance(a, list)
        or len(a) != 4
        or not isinstance(a[0], str)
        or len(a[0]) > 6
        or type(a[1]) is not int
        or a[1] not in {0, 1}
        or type(a[2]) is not int
        or a[2] not in {0, 1}
        or type(a[3]) is not int
        or not 0 <= a[3] <= 2
        for a in people
    ):
        raise DiagramError("diagram_invalid_pedigree")
    if len(couples) > 4 or any(
        not isinstance(a, list)
        or len(a) != 2
        or any(type(i) is not int or not 0 <= i < len(people) for i in a)
        or a[0] == a[1]
        or people[a[0]][3] != people[a[1]][3]
        for a in couples
    ):
        raise DiagramError("diagram_invalid_pedigree")
    if len(children) > 8 or any(
        not isinstance(a, list)
        or len(a) != 2
        or any(type(i) is not int for i in a)
        or not 0 <= a[0] < len(couples)
        or not 0 <= a[1] < len(people)
        or people[a[1]][3] != people[couples[a[0]][0]][3] + 1
        for a in children
    ):
        raise DiagramError("diagram_invalid_pedigree")
    positions = {}
    for generation in range(3):
        indices = [i for i, a in enumerate(people) if a[3] == generation]
        for j, i in enumerate(indices):
            positions[i] = (
                70 + (j + 0.5) * 340 / max(1, len(indices)),
                55 + generation * 95,
            )
    for a, b in couples:
        d.line(*positions[a], *positions[b])
    for pair, child in children:
        a, b = couples[pair]
        origin = ((positions[a][0] + positions[b][0]) / 2, positions[a][1])
        target = positions[child]
        middle = origin[1] + 45
        d.poly(
            [origin, (origin[0], middle), (target[0], middle), target],
            color=d.ink,
            width=1.5,
        )
    for i, (label, sex, affected, generation) in enumerate(people):
        x, y = positions[i]
        fill = d.ink if affected else "#fff"
        if sex:
            d.rect(x - 14, y - 14, 28, 28, fill=fill)
        else:
            d.circle(x, y, 14, fill=fill)
        text(d, label, x, y + 33, size=11)
    d.facts.update(people=people, couples=couples, children=children)


def draw(v, p, mono=False):
    d = canvas(mono)
    if v == "root_tip":
        d.path("M 179 31 L 179 239 Q 240 306 301 239 L 301 31", fill=d.glass)
        for y, h, rows in [(32, 68, 2), (100, 74, 2), (174, 48, 4)]:
            for row in range(rows):
                for col in range(5):
                    d.rect(
                        183 + col * 23,
                        y + row * h / rows,
                        22,
                        h / rows - 1,
                        fill=d.surface,
                        width=0.7,
                    )
        d.path(
            "M 182 224 Q 240 278 298 224 M 190 242 Q 240 290 290 242",
            color=d.gold,
            width=3,
        )
        for y in [43, 62, 84]:
            d.path(
                f"M 179 {y} Q 132 {y-10} 107 {y+5} M 301 {y} Q 352 {y-13} 373 {y+4}",
                color=d.green,
                width=1.5,
            )
        for y, label in [
            (66, "differentiation"),
            (135, "elongation"),
            (199, "division"),
            (261, "root cap"),
        ]:
            text(d, label, 390, y + 5, size=11)
            d.line(306, y, 329, y, color=d.muted, width=1)
    elif v == "mosfet":
        d.circle(240, 160, 94, color=d.muted, width=1)
        d.line(205, 90, 205, 231, width=3)
        d.line(95, 160, 205, 160)
        for y in [99, 153, 207]:
            d.line(229, y, 229, y + 24, width=3)
        d.poly([(229, 112), (292, 112), (292, 40)])
        d.poly([(229, 219), (292, 219), (292, 280)])
        d.line(229, 165, 292, 165)
        d.arrow(260, 165, 232, 165)
        d.line(292, 165, 292, 219)
        text(d, "G", 76, 166)
        text(d, "D", 292, 28)
        text(d, "S", 292, 307)
        d.facts["symbol"] = "n_channel_enhancement_MOSFET"
    elif v == "moon_phase":
        positions = [
            (
                240 + 118 * math.cos(i * math.pi / 4),
                157 + 111 * math.sin(i * math.pi / 4),
            )
            for i in range(8)
        ]
        for i, (x, y) in enumerate(positions):
            r = 22
            phase = i * math.pi / 4
            d.circle(x, y, r, fill=d.muted, width=1)
            if i == 4:
                d.circle(x, y, r, fill=d.surface, width=1)
            elif i:
                rx = max(0.01, abs(math.cos(phase)) * r)
                outer = 1 if i < 4 else 0
                back = (
                    (0 if math.cos(phase) > 0 else 1)
                    if i < 4
                    else (1 if math.cos(phase) > 0 else 0)
                )
                d.path(
                    f"M {x} {y-r} A {r} {r} 0 0 {outer} {x} {y+r} A {rx} {r} 0 0 {back} {x} {y-r} Z",
                    fill=d.surface,
                    color=d.surface,
                    width=0.5,
                )
            text(d, str(i + 1), x, y + 37, size=10)
        ball(d, 240, 157, 30, d.blue)
        text(d, "phase sequence · illumination schematic", 240, 14, size=11)
    elif v in {"plate_boundary", "subduction"}:
        if v == "plate_boundary":
            for y, label in [
                (70, "divergent"),
                (160, "convergent"),
                (250, "transform"),
            ]:
                d.rect(70, y - 21, 157, 42, fill=d.gold)
                d.rect(253, y - 21, 157, 42, fill=d.glass)
                text(d, label, 240, y + 45, size=11)
                if y == 250:
                    d.arrow(231, y - 18, 231, y + 17, color=d.blue)
                    d.arrow(248, y + 17, 248, y - 18, color=d.red)
                else:
                    sign = 1 if y == 160 else -1
                    d.arrow(160, y, 160 + sign * 50, y, color=d.blue)
                    d.arrow(320, y, 320 - sign * 50, y, color=d.red)
        else:
            d.rect(30, 170, 420, 115, fill=d.gold)
            d.poly(
                [(35, 155), (205, 155), (355, 255), (339, 274), (193, 181), (35, 181)],
                closed=True,
                fill=d.glass,
            )
            d.poly(
                [
                    (219, 146),
                    (285, 146),
                    (325, 104),
                    (363, 146),
                    (446, 146),
                    (446, 177),
                    (240, 177),
                ],
                closed=True,
                fill=d.surface,
            )
            d.arrow(85, 140, 176, 140, color=d.blue)
            d.arrow(394, 128, 346, 128, color=d.red)
            d.path("M 304 242 Q 344 216 326 123", color=d.red, width=6)
            text(d, "subducting slab", 123, 262, size=12)
    elif v in {"sea_breeze", "valley_breeze", "wind"}:
        if v == "sea_breeze":
            d.rect(25, 236, 215, 50, fill=d.glass)
            d.rect(240, 236, 215, 50, fill=d.gold)
            d.arrow(81, 210, 379, 210, color=d.blue, width=3)
            d.arrow(379, 190, 379, 80, color=d.red, width=3)
            d.arrow(379, 60, 81, 60, color=d.blue, width=3)
            d.arrow(81, 80, 81, 190, color=d.blue, width=3)
            text(d, "sea", 125, 273)
            text(d, "land (day)", 344, 273)
        elif v == "valley_breeze":
            d.poly(
                [
                    (30, 83),
                    (90, 64),
                    (237, 243),
                    (385, 64),
                    (450, 83),
                    (450, 282),
                    (30, 282),
                ],
                closed=True,
                fill=d.gold,
            )
            d.arrow(224, 209, 112, 75, color=d.red, width=3)
            d.arrow(257, 209, 367, 75, color=d.red, width=3)
            text(d, "daytime upslope flow", 240, 35)
        else:
            for x in [95, 180, 265, 350]:
                d.line(x, 45, x, 269, color=d.muted, width=1)
            text(d, "H", 60, 75, size=24)
            text(d, "L", 425, 75, size=24)
            d.arrow(100, 165, 391, 165, color=d.blue, width=5)
            text(d, "pressure-gradient direction", 240, 298)
    elif v == "watershed":
        d.poly(
            [(35, 257), (95, 70), (238, 35), (389, 83), (449, 250), (270, 285)],
            closed=True,
            fill=d.surface,
        )
        d.poly(
            [(55, 238), (102, 85), (237, 51), (376, 98), (432, 242)],
            color=d.gold,
            width=3,
        )
        for points in [
            [(118, 103), (174, 172), (248, 207), (306, 271)],
            [(236, 75), (237, 164), (248, 207)],
            [(369, 123), (300, 176), (248, 207)],
        ]:
            d.poly(points, color=d.blue, width=3)
        text(d, "drainage divide", 239, 24)
        text(d, "outlet", 349, 295, size=12)
    elif v == "isotope":
        for x, neutrons in [(133, 6), (347, 8)]:
            d.circle(x, 155, 90, color=d.muted)
            d.circle(x, 155, 60, color=d.muted)
            for i in range(6 + neutrons):
                a = i * 2.39996
                r = 8 * math.sqrt(i)
                ball(
                    d,
                    x + r * math.cos(a),
                    155 + r * math.sin(a),
                    7,
                    d.red if i < 6 else d.blue,
                )
            for r, count in [(60, 2), (90, 4)]:
                for i in range(count):
                    a = i * 2 * math.pi / count
                    d.circle(x + r * math.cos(a), 155 + r * math.sin(a), 4, fill=d.ink)
            text(d, f"C-{6+neutrons}", x, 282)
            text(d, f"6 p / {neutrons} n", x, 307, size=11)
        d.facts["isotopes"] = [
            {"protons": 6, "neutrons": 6},
            {"protons": 6, "neutrons": 8},
        ]
    elif v == "electron_pair":
        d.ellipse(240, 157, 113, 67, fill=d.glass, color=d.blue)
        ball(d, 199, 157, 15, d.blue)
        ball(d, 281, 157, 15, d.blue)
        d.arrow(199, 112, 199, 80, color=d.gold)
        d.arrow(281, 80, 281, 112, color=d.gold)
        text(d, "paired opposite spins", 240, 276)
    elif v == "ion":
        electrons = p["atomic_number"] - p["charge"]
        remaining = electrons
        d.circle(240, 160, 22, fill=d.gold)
        text(d, str(p["atomic_number"]) + "p", 240, 166, size=13)
        for r, cap in [(42, 2), (66, 8), (90, 8), (113, 2)]:
            count = min(cap, remaining)
            if not count:
                break
            d.circle(240, 160, r, color=d.muted, width=1)
            for i in range(count):
                a = i * 2 * math.pi / count
                d.circle(
                    240 + r * math.cos(a),
                    160 + r * math.sin(a),
                    4,
                    fill=d.blue,
                    width=1,
                )
            remaining -= count
        text(d, f"charge {p['charge']:+d}", 389, 60)
        d.facts.update(
            protons=p["atomic_number"], electrons=electrons, charge=p["charge"]
        )
    elif v == "ester":
        for label, x, y in [
            ("R", 70, 179),
            ("C", 200, 179),
            ("O", 200, 60),
            ("O", 319, 179),
            ("R′", 418, 179),
        ]:
            text(d, label, x, y, size=25)
        d.line(91, 170, 180, 170)
        d.line(219, 170, 295, 170)
        d.line(335, 170, 389, 170)
        d.line(194, 145, 194, 76)
        d.line(207, 145, 207, 76)
        text(d, "R—C(=O)—O—R′", 240, 282, size=21)
    elif v in {"solution", "precipitate", "suspension", "emulsion", "diffusion"}:
        count = 2 if v == "diffusion" else 1
        for stage in range(count):
            x = 50 + stage * 240 if count == 2 else 135
            w = 145 if count == 2 else 210
            vessel(d, x, 68, w, 204, 0.82)
            if v == "emulsion":
                for i in range(10):
                    cx = x + 26 + (i * 43) % int(w - 48)
                    cy = 120 + (i * 31) % 123
                    d.circle(cx, cy, 9 + (i % 3) * 3, fill=d.gold, width=1)
            else:
                for i in range(24):
                    cx = x + 17 + (i * 31) % int(w - 36)
                    cy = (
                        253 - (i % 3) * 5
                        if v == "precipitate"
                        else (
                            (225 + (i * 7) % 25)
                            if i % 3 == 0
                            else (
                                (115 + (i * 23) % 100)
                                if v == "diffusion" and stage == 0
                                else 115 + (i * 23) % 132
                            )
                        )
                    )
                    col = (
                        d.gold
                        if v in {"precipitate", "suspension"}
                        else d.red if i % 3 == 0 else d.surface
                    )
                    r = 4 if v == "suspension" else 2.5
                    d.circle(cx, cy, r, fill=col, width=0.5)
            if count == 2:
                text(d, "before" if not stage else "after", x + w / 2, 302)
        if count == 2:
            connect(d, (207, 174), (276, 174))
        d.facts["particle_view"] = "schematic_not_to_scale"
    elif v == "diamond":
        centers = [(140, 139), (254, 203), (343, 124)]
        for i, (x, y) in enumerate(centers):
            endpoints = [
                (x, y - 60),
                (x - 50, y + 35),
                (x + 51, y + 35),
                (x + 36, y - 24),
            ]
            for j, q in enumerate(endpoints):
                d.line(x, y, *q, color=d.muted, width=4)
                ball(d, *q, 10, d.blue)
            ball(d, x, y, 14, d.ink)
        d.line(191, 174, 254, 203, color=d.muted, width=4)
        text(d, "tetrahedral covalent network", 240, 304)
    elif v == "graphite":
        for layer, y0 in enumerate([95, 198]):
            for col in range(4):
                x = 91 + col * 90
                pts = [
                    (
                        x + 45 * math.cos(i * math.pi / 3),
                        y0 + 27 * math.sin(i * math.pi / 3),
                    )
                    for i in range(6)
                ]
                d.poly(pts, closed=True, color=d.ink, width=2)
                for q in pts:
                    d.circle(*q, 4, fill=d.blue, width=1)
            if not layer:
                for x in [91, 181, 271, 361]:
                    d.line(x, 122, x, 172, color=d.muted, dashed=True, width=1)
        text(d, "layered hexagonal networks", 240, 296)
    elif v in {"cashflow", "gantt"}:
        items, _ = _items(p)
        numbers = p["values"]
        if len(numbers) != len(items):
            raise DiagramError("diagram_invalid_data")
        if v == "cashflow":
            if (
                any(type(n) not in {int, float} for n in numbers)
                or max(map(abs, numbers)) == 0
            ):
                raise DiagramError("diagram_invalid_data")
            peak = max(map(abs, numbers))
            d.arrow(35, 160, 444, 160)
            for i, (label, n) in enumerate(zip(items, numbers)):
                x = 60 + i * 360 / (len(items) - 1)
                d.arrow(
                    x,
                    160,
                    x,
                    160 - 95 * n / peak,
                    color=d.blue if n >= 0 else d.red,
                    width=3,
                )
                text(d, label, x, 294, size=11)
        else:
            if any(
                not isinstance(a, list)
                or len(a) != 2
                or any(type(n) not in {int, float} or n < 0 for n in a)
                or a[1] <= a[0]
                for a in numbers
            ):
                raise DiagramError("diagram_invalid_data")
            peak = max(a[1] for a in numbers)
            spacing = 230 / len(items)
            for i, (label, (a, b)) in enumerate(zip(items, numbers)):
                y = 45 + i * spacing
                text(d, label, 60, y + 17, size=11)
                d.rect(
                    118 + a / peak * 320,
                    y,
                    (b - a) / peak * 320,
                    25,
                    fill=[d.blue, d.gold, d.green][i % 3],
                    width=1,
                )
            d.arrow(118, 285, 440, 285)
            text(d, "time", 412, 310, size=12)
        d.facts["data_source"] = "task_parameters"
    elif v == "pedigree":
        pedigree(d, p)
    elif v in {"ecosystem", "hasse", "bipartite", "er", "class", "double_list"}:
        items, edges = _items(p)
        n = len(items)
        if v == "double_list":
            for i, label in enumerate(items):
                x = 40 + (i + 0.5) * 400 / n
                node(d, label, x, 156, w=400 / n - 14, h=55)
            for i in range(n - 1):
                a = 40 + (i + 1) * 400 / n
                d.arrow(a - 5, 143, a + 5, 143, color=d.blue)
                d.arrow(a + 5, 170, a - 5, 170, color=d.gold)
        else:
            if v == "bipartite":
                positions = [
                    (
                        100 if i < n // 2 else 380,
                        65 + (i if i < n // 2 else i - n // 2) * 80,
                    )
                    for i in range(n)
                ]
            elif v == "hasse":
                successors = {i: [b for a, b in edges if a == i] for i in range(n)}
                levels, active = {}, set()

                def level(i):
                    if i in active:
                        raise DiagramError("diagram_hasse_cycle")
                    if i not in levels:
                        active.add(i)
                        levels[i] = 1 + max(
                            (level(j) for j in successors[i]), default=-1
                        )
                        active.remove(i)
                    return levels[i]

                for i in range(n):
                    level(i)
                height = max(levels.values()) or 1
                positions = []
                for i in range(n):
                    peers = [j for j in range(n) if levels[j] == levels[i]]
                    positions.append(
                        (
                            480 * (peers.index(i) + 1) / (len(peers) + 1),
                            65 + 190 * levels[i] / height,
                        )
                    )

                def reaches(a, b, skip):
                    todo, seen = [a], set()
                    while todo:
                        q = todo.pop()
                        if q == b:
                            return True
                        if q in seen:
                            continue
                        seen.add(q)
                        todo.extend(j for j in successors[q] if (q, j) != skip)
                    return False

                edges = [(a, b) for a, b in edges if not reaches(a, b, (a, b))]
            elif v in {"er", "class"}:
                positions = [
                    (110 + (i % 2) * 260, 70 + (i // 2) * 95) for i in range(n)
                ]
            else:
                positions = [
                    (
                        240 + 155 * math.cos(i * 2 * math.pi / n),
                        160 + 100 * math.sin(i * 2 * math.pi / n),
                    )
                    for i in range(n)
                ]
            for a, b in edges:
                if v == "bipartite" and (a < n // 2) == (b < n // 2):
                    raise DiagramError("diagram_not_bipartite")
                aa, bb = positions[a], positions[b]
                if v in {"hasse", "bipartite", "er"}:
                    d.line(*aa, *bb, color=d.muted, width=1.5)
                    if v == "er":
                        mx, my = (aa[0] + bb[0]) / 2, (aa[1] + bb[1]) / 2
                        d.poly(
                            [
                                (mx - 23, my),
                                (mx, my - 14),
                                (mx + 23, my),
                                (mx, my + 14),
                            ],
                            closed=True,
                            fill=d.surface,
                            width=1,
                        )
                        text(d, "R", mx, my + 4, size=10)
                else:
                    d.arrow(*aa, *bb, color=d.blue, width=1.5)
            for (x, y), label in zip(positions, items):
                if v == "class":
                    d.rect(x - 47, y - 30, 94, 60, fill=d.surface, width=1)
                    text(d, label, x, y - 13, size=12)
                    d.line(x - 47, y - 5, x + 47, y - 5, width=1)
                    d.line(x - 47, y + 12, x + 47, y + 12, width=1)
                else:
                    node(d, label, x, y, w=95, h=40 if v == "er" else 34)
        d.facts.update(items=items, edges=edges)
    elif v == "decoder":
        panel(d, 170, 55, 135, 210, d.glass)
        for i in range(2):
            y = 120 + i * 60
            d.line(60, y, 170, y)
            text(d, f"A{i}", 45, y + 5)
        for i in range(4):
            y = 80 + i * 52
            d.line(305, y, 419, y)
            text(d, f"Y{i}", 438, y + 5)
        text(d, "2 → 4", 238, 162, size=23)
    elif v == "register":
        for i in range(4):
            x = 75 + i * 109
            node(d, "D", x, 148, w=70, h=70, fill=d.glass)
            d.line(x, 70, x, 110)
            d.line(x, 185, x, 239)
            text(d, f"D{i}", x, 50)
            text(d, f"Q{i}", x, 264)
            d.poly([(x - 35, 163), (x - 25, 170), (x - 35, 177)], width=1.5)
            d.line(x - 35, 170, x - 45, 170)
            d.line(x - 45, 170, x - 45, 286)
        d.line(30, 286, 397, 286)
        text(d, "CLK", 433, 291, size=12)
    elif v == "heat_exchanger":
        d.rect(85, 89, 300, 151, fill=d.glass, radius=30)
        for y in [123, 165, 207]:
            d.line(60, y, 419, y, color=d.red, width=6)
        for x in [155, 230, 305]:
            d.line(x, 101, x, 229, color=d.muted, width=4)
        d.arrow(46, 123, 110, 123, color=d.red)
        d.arrow(375, 165, 436, 165, color=d.red)
        d.arrow(337, 50, 337, 105, color=d.blue, width=4)
        d.arrow(127, 225, 127, 286, color=d.blue, width=4)
        text(d, "two separated fluid paths", 240, 309)
    elif v in {"truck", "train"}:
        if v == "truck":
            d.rect(40, 90, 267, 143, fill=d.glass, radius=5)
            d.poly(
                [(312, 127), (377, 127), (424, 180), (437, 237), (312, 237)],
                closed=True,
                fill=d.gold,
            )
            d.poly(
                [(326, 141), (369, 141), (398, 178), (326, 178)],
                closed=True,
                fill=d.glass,
            )
            wheels = [98, 258, 375]
        else:
            d.rect(40, 108, 390, 128, fill=d.glass, radius=20)
            for x in [70, 138, 206, 274, 342]:
                d.rect(x, 126, 45, 45, fill=d.blue, radius=4)
            for x in [168, 299]:
                d.line(x, 111, x, 233, color=d.muted, width=3)
            wheels = [95, 162, 310, 377]
            d.line(25, 270, 455, 270, width=3)
        for x in wheels:
            ball(d, x, 239, 24, d.muted)
            d.circle(x, 239, 10, fill=d.surface)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
