"""Project-created molecular projections and glassware experiment compositions."""

import math

from .extended_common import (
    canvas,
    text,
    ball,
    vessel,
    stand,
    burner,
    pipe,
    axes,
    curve,
    panel,
    numeric,
    node,
    connect,
    values,
)
from .instruments import vessel as instrument_vessel, apparatus
from .schema import DiagramError

DATA_VARIANTS = {
    "titration_curve",
    "solubility_curve",
    "equilibrium_time",
    "mass_spectrum",
    "ir_spectrum",
    "nmr_spectrum",
}


def parameters(v):
    if v == "gas_burette":
        return numeric("reading", 32.4, 5, 45)
    if v in DATA_VARIANTS:
        return {
            "points": {"type": "list", "required": True},
            "x_label": {"type": "string", "default": "x", "max_length": 16},
            "y_label": {"type": "string", "default": "y", "max_length": 16},
        }
    if v == "microplate":
        return numeric("rows", 8, 2, 8, True) | numeric("columns", 12, 2, 12, True)
    if v == "orbital_boxes":
        return {
            "electrons": {"type": "integer", "default": 4, "minimum": 0, "maximum": 6}
        }
    return {}


def _glass(d, kind, x, y, scale=1, **p):
    fn = (
        apparatus
        if kind
        in {
            "buchner_funnel",
            "condenser",
            "fractionating_column",
            "pipette",
            "burette",
            "separatory_funnel",
            "thermometer",
            "micropipette",
        }
        else instrument_vessel
    )
    drawing = fn(kind, p, d.monochrome)
    d.add(drawing, x, y, scale)


def _bond(d, a, b, style="line", color=None):
    if style == "wedge":
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy)
        nx, ny = -dy / n * 8, dx / n * 8
        d.poly(
            [a, (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny)],
            closed=True,
            fill=color or d.ink,
        )
    elif style == "dash":
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy)
        for i in range(1, 9):
            t = i / 9
            cx = a[0] + dx * t
            cy = a[1] + dy * t
            w = t * 7
            d.line(
                cx - dy / n * w,
                cy + dx / n * w,
                cx + dy / n * w,
                cy - dx / n * w,
                color=color,
                width=1.5,
            )
    else:
        d.line(*a, *b, color=color, width=3)


def _atom(d, q, label, color, r=20):
    ball(d, *q, r, color)
    text(d, label, q[0], q[1] + 5, size=15, color="#fff")


def _plot_data(d, v, p):
    points = p["points"]
    if not 2 <= len(points) <= 70 or any(
        not isinstance(row, list)
        or len(row) != 2
        or any(type(x) not in {int, float} or not math.isfinite(x) for x in row)
        for row in points
    ):
        raise DiagramError("diagram_invalid_plot_data")
    if any(points[i][0] >= points[i + 1][0] for i in range(len(points) - 1)):
        raise DiagramError("diagram_unsorted_plot_data")
    xmin, xmax = points[0][0], points[-1][0]
    ys = [q[1] for q in points]
    ymin = min(0, min(ys))
    ymax = max(ys)
    if ymax <= ymin:
        raise DiagramError("diagram_invalid_plot_data")
    if v in {"mass_spectrum", "ir_spectrum", "nmr_spectrum"} and ymin < 0:
        raise DiagramError("diagram_invalid_spectrum")
    xy = axes(d, x_label=p["x_label"], y_label=p["y_label"])
    coord = lambda a, b: xy(
        (a - xmin) / (xmax - xmin), 0.05 + 0.85 * (b - ymin) / (ymax - ymin)
    )
    if v == "mass_spectrum":
        for a, b in points:
            d.line(*coord(a, ymin), *coord(a, b), color=d.blue, width=3)
    else:
        d.poly([coord(a, b) for a, b in points], color=d.blue)
    for a, b in [points[0], points[-1]]:
        text(d, f"{a:g}", coord(a, ymin)[0], 294, size=11)
    text(d, f"{ymax:g}", 31, 77, size=11)
    d.facts.update(
        data_points=points,
        data_source="task_parameters",
        x_range=[xmin, xmax],
        y_range=[ymin, ymax],
    )


def draw(v, p, mono=False):
    d = canvas(mono)
    if v in DATA_VARIANTS:
        _plot_data(d, v, p)
    elif v == "gas_burette":
        reading = p["reading"]
        level = 70 + reading * 3.7
        d.path("M 161 55 Q 180 38 199 55 L 199 257 L 161 257 Z", fill=d.glass)
        d.rect(164, level, 32, 257 - level, fill=d.blue, color=d.blue, width=0.5)
        d.path(f"M 164 {level} Q 180 {level+5} 196 {level}", color=d.ink, width=1)
        for i in range(51):
            y = 70 + i * 3.7
            d.line(200, y, 213 if i % 5 == 0 else 207, y, width=0.8)
            if i % 10 == 0:
                text(d, str(i), 230, y + 4, size=11)
        vessel(d, 320, 137, 80, 123, 0.48)
        pipe(d, [(180, 257), (180, 285), (359, 285), (359, 259)])
        d.rect(166, 40, 28, 8, fill=d.muted)
        d.arrow(424, 159, 424, 247, double=True, color=d.gold)
        text(d, "mL", 230, 50, size=12)
        text(d, "leveling reservoir", 360, 122, size=12)
        d.facts.update(reading_ml=reading, scale_direction="increases_downward")
    elif v in {
        "tetrahedral",
        "trigonal_pyramidal",
        "linear_geometry",
        "trigonal_planar",
    }:
        center = (240, 155)
        if v == "tetrahedral":
            points = [(240, 55), (140, 210), (335, 215), (340, 110)]
            styles = ["line", "line", "wedge", "dash"]
            labels = ["X"] * 4
        elif v == "trigonal_pyramidal":
            points = [(130, 200), (340, 210), (325, 115)]
            styles = ["line", "wedge", "dash"]
            labels = ["H"] * 3
        elif v == "linear_geometry":
            points = [(95, 155), (385, 155)]
            styles = ["line"] * 2
            labels = ["X"] * 2
        else:
            points = [(240, 48), (135, 215), (345, 215)]
            styles = ["line"] * 3
            labels = ["X"] * 3
        for q, style, label in zip(points, styles, labels):
            _bond(d, center, q, style)
            _atom(d, q, label, d.blue)
        _atom(d, center, "A", d.red, 26)
        if v == "trigonal_pyramidal":
            d.ellipse(240, 76, 24, 40, fill=d.glass, color=d.blue)
            d.circle(233, 52, 3, fill=d.ink)
            d.circle(247, 52, 3, fill=d.ink)
        text(
            d,
            {
                "tetrahedral": "109.5°",
                "trigonal_pyramidal": "one lone pair",
                "linear_geometry": "180°",
                "trigonal_planar": "120°",
            }[v],
            240,
            287,
        )
        d.facts["geometry"] = v
    elif v == "cis_trans":
        for x, trans in [(128, False), (353, True)]:
            a, b = (x - 37, 155), (x + 37, 155)
            for dy in [-4, 4]:
                d.line(a[0], a[1] + dy, b[0], b[1] + dy)
            for point, corners in [
                (a, [(x - 76, 90), (x - 76, 220)]),
                (b, [(x + 76, 90), (x + 76, 220)]),
            ]:
                for j, q in enumerate(corners):
                    _bond(d, point, q)
                    label = "X" if j == (1 if trans and point == b else 0) else "H"
                    _atom(d, q, label, d.red if label == "X" else d.blue, 14)
            for q in [a, b]:
                _atom(d, q, "C", d.ink, 16)
            text(d, "trans" if trans else "cis", x, 282)
    elif v == "chirality":
        d.line(240, 40, 240, 280, dashed=True, color=d.muted)
        for cx, mirror in [(120, 1), (360, -1)]:
            center = (cx, 155)
            for q, style, label, col in [
                ((cx, 65), "line", "A", d.blue),
                ((cx - mirror * 70, 205), "line", "B", d.green),
                ((cx + mirror * 70, 210), "wedge", "D", d.gold),
                ((cx + mirror * 55, 105), "dash", "E", d.red),
            ]:
                _bond(d, center, q, style)
                _atom(d, q, label, col, 15)
            _atom(d, center, "C", d.ink, 20)
        text(d, "mirror", 240, 305)
    elif v == "hydrogen_network":
        oxygens = [(110, 80), (295, 95), (190, 230), (380, 230)]
        for i, (x, y) in enumerate(oxygens):
            for q in [(x - 35, y + 25), (x + 37, y + 21)]:
                _bond(d, (x, y), q)
                _atom(d, q, "H", d.blue, 11)
            _atom(d, (x, y), "O", d.red, 19)
        for a, b in [
            ((147, 101), (295, 95)),
            ((145, 105), (190, 230)),
            ((332, 116), (380, 230)),
            ((227, 251), (380, 230)),
        ]:
            d.line(*a, *b, color=d.gold, dashed=True, width=2)
        text(d, "O—H ··· O", 240, 303)
    elif v == "hybrid_orbitals":
        for cx, number, label in [(90, 2, "sp"), (240, 3, "sp²"), (390, 4, "sp³")]:
            for i in range(number):
                a = (
                    [-math.pi / 2, math.pi / 6, 5 * math.pi / 6, -math.pi / 7][i]
                    if number == 4
                    else 2 * math.pi * i / number
                )
                tip = (cx + 55 * math.cos(a), 150 + 55 * math.sin(a))
                d.path(
                    f"M {cx} 150 Q {cx+28*math.cos(a)-18*math.sin(a)} {150+28*math.sin(a)+18*math.cos(a)} {tip[0]} {tip[1]} Q {cx+28*math.cos(a)+18*math.sin(a)} {150+28*math.sin(a)-18*math.cos(a)} {cx} 150 Z",
                    fill=d.surface if number == 4 and i == 3 else d.glass,
                    color=d.blue,
                    dashed=number == 4 and i == 3,
                )
            d.circle(cx, 150, 5, fill=d.red)
            text(d, label, cx, 255)
            text(
                d, ["180°", "120°", "tetrahedral 109.5°"][number - 2], cx, 285, size=11
            )
        d.facts["projection"] = "schematic_orbital_lobes_not_probability_scale"
    elif v == "vacuum_filtration":
        stand(d, 75, 45, 235)
        # Original section view: funnel stem seals into a side-arm filter flask.
        d.path(
            "M 147 146 L 147 191 L 106 268 Q 165 282 224 268 L 183 191 L 183 146",
            fill=d.glass,
        )
        d.path(
            "M 123 237 L 110 266 Q 165 278 220 266 L 207 237 Z",
            fill=d.blue,
            color=d.blue,
        )
        d.rect(143, 145, 44, 12, fill=d.muted, radius=2)
        d.path(
            "M 113 55 L 113 102 L 155 127 L 155 157 L 175 157 L 175 127 L 217 102 L 217 55",
            fill=d.glass,
        )
        d.ellipse(165, 55, 52, 9, fill=d.surface)
        d.line(115, 96, 215, 96, color=d.muted, width=3)
        for x in range(123, 212, 12):
            d.circle(x, 96, 1.5, fill=d.ink, width=0.5)
        d.line(75, 82, 112, 82, width=3)
        pipe(d, [(184, 180), (237, 168), (283, 168), (283, 237), (339, 237)])
        panel(d, 339, 208, 103, 61)
        text(d, "vacuum", 390, 245)
        d.arrow(165, 70, 165, 89, color=d.gold)
        d.arrow(165, 167, 165, 218, color=d.gold)
        d.facts["connections"] = ["funnel_sealed_to_flask", "sidearm_to_vacuum"]
    elif v in {"reflux", "fractional_distillation"}:
        stand(d, 65, 45, 240)
        _glass(d, "round_flask", 95, 145, 0.88, fill=0.4)
        burner(d, 165, 245)
        d.line(80, 235, 240, 235)
        if v == "reflux":
            d.rect(147, 45, 35, 112, fill=d.glass)
            d.rect(157, 38, 15, 126, fill=d.surface)
            for y in range(57, 145, 12):
                d.line(149, y, 179, y, color=d.blue, width=1)
            pipe(d, [(147, 134), (100, 134), (100, 174)])
            pipe(d, [(182, 64), (238, 64), (238, 35)])
            d.arrow(115, 173, 115, 135, color=d.blue)
            d.arrow(220, 64, 220, 40, color=d.blue)
            d.arrow(165, 85, 165, 140, color=d.gold)
            text(d, "open top", 166, 26, size=12)
        else:
            d.rect(150, 66, 22, 105, fill=d.glass)
            for y in range(76, 161, 13):
                d.circle(157, y, 3, fill=d.surface)
                d.circle(166, y + 5, 3, fill=d.surface)
            pipe(d, [(162, 66), (205, 66), (337, 160)])
            d.poly(
                [(213, 58), (202, 77), (323, 166), (337, 150)],
                closed=True,
                fill=d.glass,
            )
            pipe(d, [(205, 66), (337, 160), (374, 189)])
            pipe(d, [(320, 157), (354, 136), (377, 136)])
            pipe(d, [(221, 75), (216, 113), (245, 113)])
            _glass(d, "conical_flask", 310, 160, 0.8, fill=0.3)
            d.arrow(365, 134, 339, 151, color=d.blue)
            d.arrow(220, 86, 220, 109, color=d.blue)
    elif v == "rotary_evaporator":
        panel(d, 275, 60, 120, 205)
        d.circle(335, 86, 18, fill=d.muted)
        d.line(335, 86, 234, 144, width=12)
        d.circle(200, 173, 43, fill=d.glass)
        d.path("M 157 173 Q 200 217 243 173", fill=d.blue, color=d.blue)
        d.path("M 112 205 Q 195 185 278 205 L 264 277 L 126 277 Z", fill=d.surface)
        d.rect(144, 32, 29, 100, fill=d.glass)
        pipe(d, [(159, 120), (245, 120), (245, 96), (309, 96)])
        _glass(d, "round_flask", 91, 97, 0.48, fill=0.2)
        text(d, "water bath", 196, 302)
        text(d, "vacuum", 398, 48)
    elif v == "leak_check":
        _glass(d, "conical_flask", 55, 100, 1, fill=0)
        d.rect(118, 130, 33, 8, fill=d.muted)
        pipe(d, [(135, 130), (135, 70), (325, 70), (325, 224)])
        vessel(d, 283, 168, 88, 105, 0.65)
        for y in [210, 191, 177]:
            d.circle(325, y, 4, fill=d.surface, color=d.blue, width=1)
        d.path("M 52 220 Q 75 180 95 205 M 54 236 Q 75 200 97 222", color=d.gold)
        text(d, "warm by hand", 130, 286)
        text(d, "bubbles", 327, 298)
    elif v == "reaction_calorimeter":
        d.rect(125, 100, 230, 170, fill=d.surface, radius=9)
        vessel(d, 145, 112, 190, 143, 0.62)
        d.rect(119, 88, 242, 18, fill=d.muted, radius=3)
        d.rect(213, 40, 10, 171, fill=d.glass)
        d.circle(218, 210, 8, fill=d.red)
        d.line(218, 202, 218, 60, color=d.red, width=3)
        for y in range(55, 133, 10):
            d.line(225, y, 237, y, width=1)
        d.path("M 283 65 L 283 229 L 306 229 L 306 201", width=3)
        text(d, "insulating jacket", 240, 300)
    elif v == "gas_syringe":
        d.rect(180, 128, 210, 55, fill=d.glass)
        d.rect(292, 130, 12, 51, fill=d.muted)
        d.line(304, 156, 429, 156, width=5)
        d.line(429, 132, 429, 180, width=5)
        for i in range(10):
            d.line(185 + i * 19, 128, 185 + i * 19, 139 if i % 5 else 150, width=1)
        pipe(d, [(180, 155), (130, 155), (130, 201)])
        _glass(d, "conical_flask", 40, 160, 0.9, fill=0.3)
        d.arrow(321, 98, 387, 98, color=d.gold)
        text(d, "V", 370, 78)
    elif v == "precipitate_wash":
        for x in [45, 205, 365]:
            vessel(d, x, 100, 70, 125, 0.58)
            for i in range(7):
                d.circle(x + 12 + i * 7, 214 - (i % 2) * 3, 3, fill=d.gold, width=1)
        connect(d, (128, 164), (187, 164))
        connect(d, (288, 164), (347, 164))
        d.arrow(240, 46, 240, 96, color=d.blue)
        text(d, "H₂O", 240, 35)
        d.arrow(402, 171, 443, 137, color=d.blue)
        for x, label in [(80, "settle"), (240, "wash"), (400, "decant")]:
            text(d, label, x, 276)
    elif v == "pipette_transfer":
        vessel(d, 35, 175, 105, 110, 0.55)
        _glass(d, "volumetric_flask", 283, 125, 1, fill=0.38)
        d.add(apparatus("pipette", {}, mono), 135, 42, 0.85, 12)
        d.ellipse(224, 48, 19, 25, fill=d.red)
        d.arrow(228, 138, 290, 158, color=d.gold)
        d.circle(327, 139, 3, fill=d.blue)
        text(d, "calibrated aliquot", 240, 309)
    elif v == "standard_solution":
        for x in [45, 200, 350]:
            _glass(
                d, "volumetric_flask", x - 20, 100, 0.9, fill=0.48 if x > 100 else 0.22
            )
        connect(d, (145, 172), (195, 172))
        connect(d, (299, 172), (345, 172))
        d.poly([(90, 45), (137, 60), (93, 90)], closed=True, fill=d.surface)
        d.arrow(104, 87, 108, 112, color=d.gold)
        d.line(418, 65, 402, 153, width=4)
        d.circle(400, 165, 3, fill=d.blue)
        text(d, "dissolve", 100, 275)
        text(d, "transfer", 254, 275)
        text(d, "to mark", 400, 275)
    elif v == "microplate":
        rows, cols = p["rows"], p["columns"]
        w = 360 / cols
        h = 210 / rows
        panel(d, 57, 47, 384, 238, d.surface)
        for i in range(rows):
            text(d, chr(65 + i), 43, 65 + i * h, size=11)
            for j in range(cols):
                d.circle(
                    73 + j * w,
                    62 + i * h,
                    min(w, h) * 0.31,
                    fill=d.glass if (i + j) % 3 else d.blue,
                    width=1,
                )
        for j in range(cols):
            text(d, str(j + 1), 73 + j * w, 33, size=11)
        d.facts.update(rows=rows, columns=cols, well_count=rows * cols)
    elif v == "tlc_chamber":
        vessel(d, 120, 72, 240, 217, 0.13)
        d.rect(166, 89, 144, 175, fill=d.surface)
        d.line(173, 235, 303, 235, color=d.muted, width=1)
        d.line(173, 118, 303, 118, color=d.blue, dashed=True)
        for x, spots in [(190, [180, 146]), (238, [162]), (286, [180, 146, 162])]:
            d.circle(x, 235, 3, fill=d.ink)
            for y in spots:
                d.ellipse(x, y, 8, 4, fill=d.gold, width=1)
        d.rect(110, 65, 260, 10, fill=d.surface)
        text(d, "solvent front", 239, 104, size=12)
        d.facts["baseline_above_solvent"] = True
    elif v == "activation_energy":
        xy = axes(d, x_label="progress", y_label="energy", ticks=False)
        curve(
            d,
            lambda x: 0.25 + 0.6 * math.exp(-(((x - 0.5) / 0.18) ** 2)) - 0.16 * x,
            xy,
        )
        curve(
            d,
            lambda x: 0.25 + 0.3 * math.exp(-(((x - 0.5) / 0.18) ** 2)) - 0.16 * x,
            xy,
            color=d.green,
        )
        d.arrow(*xy(0.5, 0.17), *xy(0.5, 0.77), color=d.gold, double=True)
        text(d, "Eₐ", 270, 147)
        text(d, "catalyzed", 345, 173, color=d.green)
    elif v == "equilibrium_shift":
        for cx, shift in [(120, False), (360, True)]:
            panel(d, cx - 85, 65, 170, 170, d.glass)
            for i in range(12):
                x = cx - 55 + (i % 4) * 37
                y = 98 + (i // 4) * 50
                is_product = i < (8 if shift else 4)
                if is_product:
                    _bond(d, (x - 7, y), (x + 7, y))
                    ball(d, x - 7, y, 7, d.blue)
                    ball(d, x + 7, y, 7, d.red)
                else:
                    ball(d, x, y, 9, d.green)
            text(d, "initial" if not shift else "after change", cx, 268)
        connect(d, (217, 150), (263, 150))
        text(d, "schematic populations", 240, 304, size=12)
    elif v == "orbital_boxes":
        count = p["electrons"]
        for i in range(3):
            d.rect(103 + i * 95, 110, 76, 72, fill=d.surface)
            if count > i:
                d.arrow(125 + i * 95, 164, 125 + i * 95, 125, color=d.blue)
            if count > 3 + i:
                d.arrow(154 + i * 95, 125, 154 + i * 95, 164, color=d.red)
            text(d, ["pₓ", "pᵧ", "p𝓏"][i], 141 + i * 95, 218)
        text(d, "Hund filling order", 240, 278)
        d.facts["electron_count"] = count
    elif v == "periodic_table":
        # Standard symbol placement; table artwork, cell geometry and spacing are ours.
        rows = [
            [(0, "H"), (17, "He")],
            [(0, "Li"), (1, "Be")] + list(zip(range(12, 18), "B C N O F Ne".split())),
            [(0, "Na"), (1, "Mg")]
            + list(zip(range(12, 18), "Al Si P S Cl Ar".split())),
            list(
                enumerate("K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr".split())
            ),
            list(
                enumerate("Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe".split())
            ),
            list(
                enumerate(
                    "Cs Ba La Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn".split()
                )
            ),
            list(
                enumerate(
                    "Fr Ra Ac Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split()
                )
            ),
        ]
        for i, row in enumerate(rows):
            for col, symbol in row:
                x = 24 + col * 24
                y = 28 + i * 27
                d.rect(
                    x, y, 22, 25, fill=d.glass if col >= 12 else d.surface, width=0.6
                )
                text(d, symbol, x + 11, y + 16, size=9)
        for i, symbols in enumerate(
            [
                "Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu",
                "Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr",
            ]
        ):
            for j, symbol in enumerate(symbols.split()):
                x = 96 + j * 24
                y = 243 + i * 27
                d.rect(x, y, 22, 25, fill=d.green, width=0.6)
                text(d, symbol, x + 11, y + 16, size=9)
        d.facts["element_count"] = 118
    elif v == "molecular_vibration":
        for i, (label, coords) in enumerate(
            [
                ("stretch", [(80, 85), (160, 85), (240, 85)]),
                ("bend", [(150, 188), (220, 232), (290, 188)]),
            ]
        ):
            for a, b in zip(coords, coords[1:]):
                _bond(d, a, b)
            for j, q in enumerate(coords):
                _atom(d, q, "O" if j != 1 else "C", d.red if j != 1 else d.ink, 18)
            text(d, label, 390, 95 + i * 130)
            if i == 0:
                d.arrow(76, 120, 43, 120, color=d.gold)
                d.arrow(243, 120, 277, 120, color=d.gold)
            else:
                d.arrow(143, 164, 172, 139, color=d.gold)
                d.arrow(297, 164, 268, 139, color=d.gold)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
