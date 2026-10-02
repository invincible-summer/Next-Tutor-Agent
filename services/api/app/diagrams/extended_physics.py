"""Original physics scenes. Geometry, scales and fields are calculated locally."""

import math

from .extended_common import (
    canvas,
    text,
    ball,
    box,
    axes,
    curve,
    panel,
    numeric,
    node,
    connect,
)
from .schema import DiagramError


def parameters(v):
    if v in {"oblique_projectile", "polarizers", "critical_angle"}:
        return numeric("angle", 35 if v == "oblique_projectile" else 45, 10, 75)
    if v == "vernier_reading":
        return numeric("reading", 23.4, 10, 35)
    if v == "micrometer_reading":
        return numeric("reading", 6.28, 0, 12)
    if v == "scope_reading":
        return {**numeric("cycles", 2, 1, 4), **numeric("amplitude", 1.5, 0.2, 2)}
    if v in {"capacitor_rc", "beats", "standing_nodes"}:
        return (
            numeric("tau", 1, 0.3, 3)
            if v == "capacitor_rc"
            else numeric("count", 4, 2, 6, True)
        )
    return {}


def _coil(d, x, y, w=75, h=22, turns=8):
    pts = [
        (x + i * w / 120, y + h * math.sin(i * turns * 2 * math.pi / 120))
        for i in range(121)
    ]
    d.poly(pts, color=d.blue)


def _charge(d, x, y, positive=True, r=15):
    ball(d, x, y, r, d.red if positive else d.blue)
    d.line(x - r * 0.4, y, x + r * 0.4, y, color="#fff")
    if positive:
        d.line(x, y - r * 0.4, x, y + r * 0.4, color="#fff")


def _field(d, charges):
    # Integrate the Coulomb field; stop before charge singularities or paper edges.
    positives = [c for c in charges if c[2] > 0]
    for cx, cy, q in positives:
        for i in range(12):
            a = i * 2 * math.pi / 12
            x = cx + 20 * math.cos(a)
            y = cy + 20 * math.sin(a)
            pts = [(x, y)]
            for j in range(150):
                ex = ey = 0
                for sx, sy, sq in charges:
                    dx, dy = x - sx, y - sy
                    r = max(12, math.hypot(dx, dy))
                    ex += sq * dx / r**3
                    ey += sq * dy / r**3
                norm = math.hypot(ex, ey)
                if norm < 1e-12:
                    break
                x += 3 * ex / norm
                y += 3 * ey / norm
                if (
                    not 24 < x < 456
                    or not 25 < y < 295
                    or any(math.hypot(x - sx, y - sy) < 18 for sx, sy, sq in charges)
                ):
                    break
                pts.append((x, y))
            if len(pts) > 2:
                d.poly(pts, color=d.gold, width=1.5)
                k = len(pts) // 2
                d.arrow(*pts[max(0, k - 3)], *pts[k], color=d.gold, width=1.5)
    for x, y, q in charges:
        _charge(d, x, y, q > 0)


def draw(v, p, mono=False):
    d = canvas(mono)
    if v == "free_body":
        d.poly([(65, 265), (405, 265), (405, 80)], closed=True, fill=d.surface)
        angle = math.atan2(185, 340)
        contact = (250, 265 - (250 - 65) * 185 / 340)
        c = (contact[0] - 23 * math.sin(angle), contact[1] - 23 * math.cos(angle))
        tangent = (math.cos(angle), -math.sin(angle))
        normal = (-math.sin(angle), -math.cos(angle))
        d.poly(
            [
                (
                    c[0] + u * 35 * tangent[0] + w * 23 * normal[0],
                    c[1] + u * 35 * tangent[1] + w * 23 * normal[1],
                )
                for u, w in [(-1, -1), (1, -1), (1, 1), (-1, 1)]
            ],
            closed=True,
            fill=d.glass,
        )
        d.arrow(*c, c[0], c[1] + 95, color=d.red, width=3)
        text(d, "mg", 275, 238)
        d.arrow(
            *c,
            c[0] - 85 * math.sin(angle),
            c[1] - 85 * math.cos(angle),
            color=d.blue,
            width=3,
        )
        text(d, "N", 195, 69)
        d.arrow(
            *c,
            c[0] + 95 * math.cos(angle),
            c[1] - 95 * math.sin(angle),
            color=d.green,
            width=3,
        )
        text(d, "f", 349, 99)
    elif v == "oblique_projectile":
        a = math.radians(p["angle"])
        xy = axes(d, x=45, y=270, w=390, h=225, x_label="x", y_label="y")
        # Normalize time by flight time; range / peak retains the chosen launch angle.
        range_px = min(340, 760 / math.tan(a))
        peak = range_px * math.tan(a) / 4
        pts = [
            (55 + range_px * t, 270 - 4 * peak * t * (1 - t))
            for t in [i / 60 for i in range(61)]
        ]
        d.poly(pts, color=d.blue)
        for i in [0, 15, 30, 45, 60]:
            ball(d, *pts[i], 5)
        d.arrow(55, 270, 55 + 70 * math.cos(a), 270 - 70 * math.sin(a), color=d.red)
        d.facts.update(
            launch_angle=p["angle"],
            trajectory_model="constant_gravity_no_drag",
            range_peak_ratio=4 / math.tan(a),
        )
    elif v in {"rope_constraint", "rod_constraint"}:
        d.line(45, 60, 435, 60, width=5)
        d.hatch(45, 42, 390, 18)
        if v == "rope_constraint":
            d.circle(240, 110, 30, fill=d.surface)
            d.line(240, 60, 240, 80)
            d.path("M 210 205 L 210 110 A 30 30 0 0 1 270 110 L 270 250")
            box(d, 190, 205, 40, 45, 9)
            box(d, 250, 250, 40, 40, 9)
            d.arrow(160, 212, 160, 264, color=d.blue)
            d.arrow(320, 275, 320, 223, color=d.red)
        else:
            d.circle(115, 85, 5, fill=d.ink)
            d.line(115, 85, 335, 225, width=6)
            ball(d, 335, 225, 27)
            d.arrow(330, 225, 243, 170, color=d.blue)
            text(d, "T", 247, 153)
        d.facts["constraint"] = v
    elif v in {"binary_orbit", "centripetal_components"}:
        if v == "binary_orbit":
            d.ellipse(240, 160, 155, 95, color=d.muted)
            d.ellipse(240, 160, 77.5, 47.5, color=d.muted)
            ball(d, 85, 160, 19)
            ball(d, 317.5, 160, 30, d.gold)
            d.line(85, 160, 317.5, 160, dashed=True)
            d.circle(240, 160, 4, fill=d.red)
            d.arrow(85, 160, 160, 160, color=d.red)
            d.arrow(317.5, 160, 265, 160, color=d.red)
            d.facts.update(
                mass_ratio=2, orbital_radius_ratio=2, center_of_mass=[240, 160]
            )
        else:
            d.circle(240, 160, 105)
            a = -0.6
            q = (240 + 105 * math.cos(a), 160 + 105 * math.sin(a))
            ball(d, *q, 12)
            d.arrow(*q, 240, 160, color=d.red)
            d.arrow(*q, q[0] + 65 * math.sin(a), q[1] - 65 * math.cos(a), color=d.blue)
            text(d, "aᵣ", 268, 123)
            text(d, "v", q[0] - 52, q[1] - 52)
    elif v == "newton_cradle":
        d.poly([(80, 270), (80, 45), (405, 45), (405, 270)], width=5)
        for i in range(5):
            top = (140 + i * 40, 70)
            q = (top[0], 210)
            if i == 0:
                q = (top[0] - 75, 188)
            d.line(*top, *q)
            ball(d, *q, 19)
        d.arrow(64, 175, 119, 200, color=d.gold)
    elif v == "center_support":
        d.rect(65, 125, 350, 30, fill=d.glass)
        d.poly([(240, 155), (205, 225), (275, 225)], closed=True, fill=d.surface)
        d.circle(240, 140, 4, fill=d.red)
        d.arrow(240, 140, 240, 245, color=d.red)
        text(d, "G", 260, 143)
        d.arrow(240, 157, 240, 75, color=d.blue)
        text(d, "N", 260, 84)
    elif v in {"inertia_comparison", "angular_momentum"}:
        if v == "inertia_comparison":
            for x in [135, 345]:
                d.circle(x, 155, 72, fill=d.glass)
                d.circle(x, 155, 4, fill=d.red)
            d.circle(345, 155, 50, fill="#fff")
            d.circle(345, 155, 4, fill=d.red)
            text(d, "I = ½mr²", 135, 260)
            text(d, "I = mr²", 345, 260)
        else:
            for x, spread in [(135, 72), (345, 27)]:
                d.circle(x, 75, 15, fill=d.glass)
                d.line(x, 90, x, 195, width=5)
                d.poly([(x - 35, 260), (x, 195), (x + 35, 260)], width=5)
                d.line(x - spread, 128, x + spread, 128, width=5)
                d.path(f"M {x-70} 220 Q {x} 280 {x+70} 220", color=d.gold)
            connect(d, (220, 155), (270, 155))
            text(d, "L = Iω", 240, 35)
    elif v in {
        "pv_cycle",
        "heat_engine",
        "refrigeration",
        "phase_diagram",
        "speed_distribution",
    }:
        if v == "pv_cycle":
            xy = axes(d, x_label="V", y_label="p")
            points = [xy(0.15, 0.75), xy(0.75, 0.75), xy(0.75, 0.2), xy(0.15, 0.2)]
            d.poly(points + [points[0]], color=d.blue)
            for i in range(4):
                a, b = points[i], points[(i + 1) % 4]
                d.arrow(*a, *((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), color=d.blue)
        elif v in {"heat_engine", "refrigeration"}:
            panel(d, 120, 30, 240, 45, d.red)
            text(d, "Tₕ", 240, 59)
            panel(d, 120, 245, 240, 45, d.blue)
            text(d, "T𝚌", 240, 275)
            node(d, "W", 240, 160, w=100, h=60, fill=d.glass)
            if v == "heat_engine":
                d.arrow(240, 75, 240, 125, color=d.red, width=5)
                d.arrow(240, 190, 240, 245, color=d.blue, width=3)
                d.arrow(290, 160, 410, 160, color=d.gold, width=4)
            else:
                d.arrow(240, 245, 240, 190, color=d.blue, width=3)
                d.arrow(240, 125, 240, 75, color=d.red, width=5)
                d.arrow(410, 160, 290, 160, color=d.gold, width=4)
        elif v == "phase_diagram":
            xy = axes(d, x_label="T", y_label="p")
            triple = xy(0.36, 0.27)
            d.poly([xy(0.05, 0.06), xy(0.2, 0.12), triple], color=d.blue)
            d.poly([triple, xy(0.32, 0.95)], color=d.green)
            d.path(f"M {triple[0]} {triple[1]} Q 300 170 390 80", color=d.red)
            d.circle(*triple, 4, fill=d.ink)
            d.circle(390, 80, 4, fill=d.red)
            text(d, "solid", 115, 150)
            text(d, "liquid", 235, 85)
            text(d, "gas", 330, 240)
        else:
            xy = axes(d, x_label="v", y_label="f(v)")
            for a, c in [(8, d.blue), (4, d.red)]:
                curve(
                    d,
                    lambda x, a=a: 3.4 * a**1.5 * x * x * math.exp(-a * 5 * x * x),
                    xy,
                    color=c,
                )
            text(d, "T₁ < T₂", 350, 55)
    elif v in {"point_charge", "dipole_field"}:
        _field(
            d,
            [(240, 160, 1)] if v == "point_charge" else [(160, 160, 1), (320, 160, -1)],
        )
    elif v in {"plate_field", "equipotential", "charged_trajectory"}:
        if v == "equipotential":
            _charge(d, 240, 160)
            for r in [40, 70, 100, 130]:
                d.circle(240, 160, r, color=d.blue, width=1.5)
            for i in range(8):
                a = i * math.pi / 4
                d.arrow(
                    240 + 35 * math.cos(a),
                    160 + 35 * math.sin(a),
                    240 + 125 * math.cos(a),
                    160 + 125 * math.sin(a),
                    color=d.gold,
                )
        else:
            d.rect(75, 65, 330, 10, fill=d.red)
            d.rect(75, 245, 330, 10, fill=d.blue)
            for x in range(90, 401, 45):
                d.arrow(x, 90, x, 230, color=d.gold, width=1.5)
            for x in range(100, 400, 60):
                text(d, "+", x, 54)
                text(d, "−", x, 278)
            if v == "charged_trajectory":
                d.poly(
                    [
                        (60 + 360 * t, 160 + 70 * t * t)
                        for t in [i / 60 for i in range(61)]
                    ],
                    color=d.blue,
                    width=3,
                )
                _charge(d, 60, 160)
                d.arrow(35, 160, 70, 160, color=d.blue)
    elif v == "cyclotron":
        for sign in [-1, 1]:
            d.path(
                f"M {240+sign*8} 45 A 115 115 0 0 {1 if sign>0 else 0} {240+sign*8} 275 Z",
                fill=d.glass,
            )
        pts = []
        for i in range(201):
            t = i * math.pi * 6 / 200
            r = 8 + 5 * t
            pts.append((240 + r * math.cos(t), 160 + r * math.sin(t)))
        d.poly(pts, color=d.gold)
        d.arrow(*pts[-4], *pts[-1], color=d.gold)
        text(d, "B ⊗", 410, 55)
        node(d, "~", 240, 302, w=45, h=22)
    elif v == "hall_element":
        box(d, 100, 150, 260, 48, 35)
        d.arrow(60, 174, 390, 174, color=d.blue)
        text(d, "I", 57, 159)
        for x in [160, 230, 300]:
            _charge(d, x, 148, False, r=7)
            _charge(d, x, 195, True, r=7)
        d.line(230, 145, 230, 78)
        d.line(230, 198, 230, 275)
        text(d, "−", 230, 65)
        text(d, "+", 230, 295)
        text(d, "B ⊗", 400, 110)
    elif v in {"capacitor_rc", "ac_phasor", "lc_oscillation"}:
        if v == "capacitor_rc":
            xy = axes(d, x_label="t / τ", y_label="U / U₀")
            curve(d, lambda x: 1 - math.exp(-5 * x / p["tau"]), xy, color=d.blue)
            curve(d, lambda x: math.exp(-5 * x / p["tau"]), xy, color=d.red)
            text(d, "charge", 360, 66, color=d.blue)
            text(d, "discharge", 340, 225, color=d.red)
            d.facts["time_constant"] = p["tau"]
        elif v == "ac_phasor":
            o = (155, 215)
            d.line(55, 215, 430, 215, color=d.muted)
            d.line(155, 280, 155, 40, color=d.muted)
            for tip, label, c in [
                ((350, 215), "Uᵣ", d.blue),
                ((155, 80), "Uₗ", d.red),
                ((155, 270), "U𝚌", d.green),
                ((350, 135), "U", d.gold),
            ]:
                d.arrow(*o, *tip, color=c, width=3)
                text(d, label, tip[0] + 18, tip[1] + 5)
            d.poly([(350, 215), (350, 135), (155, 135)], color=d.muted)
        else:
            for x, phase in [(115, 0), (355, math.pi / 2)]:
                d.poly([(x - 65, 90), (x - 65, 230), (x + 65, 230), (x + 65, 90)])
                d.line(x - 65, 90, x - 12, 90)
                d.line(x + 12, 90, x + 65, 90)
                for xx in [x - 12, x + 12]:
                    d.line(xx, 65, xx, 115, width=3)
                _coil(d, x - 35, 230, 70, 12, 5)
                if not phase:
                    text(d, "+ +", x - 35, 57, color=d.red)
                    text(d, "− −", x + 35, 57, color=d.blue)
                else:
                    d.arrow(x + 65, 110, x + 65, 170, color=d.gold, width=3)
            connect(d, (205, 160), (275, 160))
            text(d, "E𝒆 ↔ Eₘ", 240, 290)
    elif v in {"standing_nodes", "superposition", "beats", "doppler"}:
        if v == "standing_nodes":
            count = p["count"]
            d.line(45, 160, 435, 160, color=d.muted)
            for sign in [-1, 1]:
                d.poly(
                    [
                        (45 + 390 * t, 160 + sign * 58 * math.sin(count * math.pi * t))
                        for t in [i / 120 for i in range(121)]
                    ],
                    color=d.blue if sign > 0 else d.gold,
                )
            for i in range(count + 1):
                d.circle(45 + 390 * i / count, 160, 4, fill=d.red)
            d.facts.update(segments=count, nodes=count + 1)
        elif v == "doppler":
            # Wavefront centers are retarded source positions, not concentric.
            for i in range(1, 6):
                d.circle(300 - i * 18, 160, i * 22, color=d.blue, width=1.5)
            ball(d, 300, 160, 12)
            d.arrow(313, 160, 380, 160, color=d.red)
            text(d, "v", 376, 147)
        elif v == "superposition":
            for row, (y, fn, c) in enumerate(
                [
                    (70, lambda t: math.sin(2 * math.pi * t), d.blue),
                    (155, lambda t: math.sin(2 * math.pi * t + 0.9), d.red),
                    (
                        250,
                        lambda t: (
                            math.sin(2 * math.pi * t) + math.sin(2 * math.pi * t + 0.9)
                        )
                        / 2,
                        d.green,
                    ),
                ]
            ):
                d.line(45, y, 435, y, color=d.muted, width=1)
                d.poly(
                    [
                        (45 + 390 * t, y - 30 * fn(t))
                        for t in [i / 80 for i in range(81)]
                    ],
                    color=c,
                )
                text(d, ["y₁", "y₂", "y₁ + y₂"][row], 70, y - 36)
        else:
            count = p["count"]
            d.line(40, 160, 440, 160, color=d.muted)
            pts = [
                (
                    45 + 390 * t,
                    160
                    + 95 * math.cos(count * math.pi * t) * math.sin(28 * math.pi * t),
                )
                for t in [i / 300 for i in range(301)]
            ]
            d.poly(pts, color=d.blue)
            for sign in [-1, 1]:
                d.poly(
                    [
                        (
                            45 + 390 * t,
                            160 + sign * 95 * abs(math.cos(count * math.pi * t)),
                        )
                        for t in [i / 100 for i in range(101)]
                    ],
                    color=d.gold,
                    width=1,
                )
    elif v in {"interference_fringes", "single_diffraction"}:
        d.line(75, 40, 75, 125, width=5)
        d.line(75, 135, 75, 185, width=5)
        d.line(75, 195, 75, 280, width=5)
        if v == "single_diffraction":
            d.line(75, 135, 75, 185, color="#fff", width=8)
        d.line(295, 30, 295, 290, width=4)
        xy = axes(d, x=320, y=275, w=125, h=230, x_label="I", y_label="y", ticks=False)
        for yy in range(35, 286, 5):
            z = (yy - 160) / 26
            intensity = (
                math.cos(z) ** 2
                if v == "interference_fringes"
                else (math.sin(z) / z) ** 2 if z else 1
            )
            d.line(
                284, yy, 305, yy, color=d.ink if intensity > 0.5 else d.surface, width=5
            )
        points = []
        for i in range(101):
            yy = 40 + i * 2.4
            z = (yy - 160) / 26
            intensity = (
                math.cos(z) ** 2
                if v == "interference_fringes"
                else (math.sin(z) / z) ** 2 if z else 1
            )
            points.append((325 + 105 * intensity, yy))
        d.poly(points, color=d.blue)
        for yy in ([130, 190] if v == "interference_fringes" else [160]):
            for ty in [80, 160, 240]:
                d.line(75, yy, 295, ty, color=d.gold, width=1)
    elif v == "polarizers":
        angle = math.radians(p["angle"])
        d.arrow(25, 160, 455, 160, color=d.gold)
        for x, a in [(160, 0), (320, angle)]:
            d.ellipse(x, 160, 48, 105, fill=d.glass)
            for offset in range(-3, 4):
                cx = x + offset * 9
                dy = math.sqrt(max(0, 1 - (offset * 9 / 48) ** 2)) * 85
                d.line(
                    cx - math.sin(a) * dy / 2,
                    160 - math.cos(a) * dy,
                    cx + math.sin(a) * dy / 2,
                    160 + math.cos(a) * dy,
                    color=d.blue,
                    width=1.5,
                )
        text(d, "I = I₀ cos²θ", 240, 294)
        d.facts.update(angle=p["angle"], transmission=math.cos(angle) ** 2)
    elif v in {"critical_angle", "fiber_reflection"}:
        if v == "critical_angle":
            d.rect(30, 160, 420, 125, fill=d.glass)
            d.line(240, 35, 240, 280, color=d.muted, dashed=True)
            a = math.radians(p["angle"])
            n = 1.5
            d.arrow(
                240 - 100 * math.sin(a), 160 + 100 * math.cos(a), 240, 160, color=d.blue
            )
            d.arrow(
                240, 160, 240 + 100 * math.sin(a), 160 + 100 * math.cos(a), color=d.blue
            )
            if n * math.sin(a) <= 1:
                b = math.asin(n * math.sin(a))
                d.arrow(
                    240,
                    160,
                    240 + 110 * math.sin(b),
                    160 - 110 * math.cos(b),
                    color=d.gold,
                )
            d.facts.update(
                incident_angle=p["angle"],
                critical_angle=math.degrees(math.asin(1 / n)),
                total_internal_reflection=n * math.sin(a) > 1,
            )
        else:
            d.rect(35, 95, 410, 130, fill=d.glass)
            d.rect(35, 120, 410, 80, fill=d.surface)
            pts = [(35, 160), (100, 120), (230, 200), (360, 120), (445, 172)]
            d.poly(pts, color=d.gold, width=3)
            for i in [1, 2, 3]:
                d.line(
                    pts[i][0],
                    pts[i][1] - 22,
                    pts[i][0],
                    pts[i][1] + 22,
                    color=d.muted,
                    dashed=True,
                )
    elif v == "michelson":
        box(d, 35, 170, 55, 30, 10)
        text(d, "laser", 62, 236)
        d.line(225, 145, 265, 185, width=5)
        d.line(360, 145, 360, 200, width=5)
        d.line(220, 45, 270, 45, width=5)
        d.arrow(100, 175, 240, 175, color=d.red)
        d.arrow(240, 175, 240, 55, color=d.blue)
        d.arrow(248, 55, 248, 175, color=d.blue)
        d.arrow(250, 175, 355, 175, color=d.gold)
        d.arrow(355, 183, 250, 183, color=d.gold)
        d.arrow(248, 185, 248, 265, color=d.red)
        d.ellipse(248, 282, 47, 17, fill=d.surface)
        for r in [12, 25, 39]:
            d.ellipse(248, 282, r, r * 0.32, color=d.muted, width=1)
    elif v == "vernier_reading":
        reading = p["reading"]
        x0 = 45
        unit = 9
        zero = x0 + reading * unit
        if abs(reading * 10 - round(reading * 10)) > 1e-6:
            raise DiagramError("diagram_reading_resolution")
        d.rect(30, 80, 415, 45, fill=d.surface)
        d.rect(zero - 12, 132, 105, 50, fill=d.glass)
        for mm in range(43):
            x = x0 + mm * unit
            d.line(x, 125, x, 125 - (22 if mm % 5 == 0 else 11), width=1)
            if mm % 5 == 0:
                text(d, str(mm), x, 98, size=11)
        for i in range(11):
            x = zero + i * 8.1
            d.line(x, 132, x, 132 + (22 if i % 5 == 0 else 13), width=1)
            if i % 5 == 0:
                text(d, str(i), x, 175, size=11)
        d.path(
            f"M 30 82 L 30 45 L 52 45 L 52 80 M {zero-12} 133 L {zero-12} 225 L {zero+8} 225 L {zero+8} 133",
            fill=d.surface,
        )
        # Enlarged coincidence: one 0.1 mm vernier step.
        panel(d, 135, 235, 245, 58)
        text(d, "main: 1 mm   vernier: 0.1 mm", 257, 270, size=12)
        d.facts.update(reading_mm=reading, least_count_mm=0.1)
    elif v == "micrometer_reading":
        reading = p["reading"]
        whole = math.floor(reading * 2) / 2
        fraction = round((reading - whole) * 100)
        if abs(reading * 100 - round(reading * 100)) > 1e-6:
            raise DiagramError("diagram_reading_resolution")
        d.path(
            "M 140 88 C 20 35 20 280 140 235 L 140 212 C 60 245 60 85 140 112 Z",
            fill=d.muted,
        )
        d.rect(135, 143, 122, 34, fill=d.surface)
        d.rect(257, 118, 130, 84, fill=d.glass)
        d.rect(387, 135, 50, 50, fill=d.muted, radius=4)
        d.line(150, 160, 387, 160, color=d.red, width=1)
        for i in range(max(0, int(whole * 2) - 14), int(whole * 2) + 1):
            value = i / 2
            x = 250 - (whole - value) * 14
            d.line(x, 160, x, 148 if i % 2 == 0 else 174, width=1)
            if i % 2 == 0:
                text(d, f"{value:g}", x, 139, size=10)
        for j in range(-5, 6):
            y = 160 + j * 7
            d.line(260, y, 277 if j % 5 else 289, y, width=1)
            if j % 5 == 0:
                text(d, str((fraction - j) % 50), 304, y + 4, size=11)
        text(d, "0.01 mm", 323, 239)
        d.facts.update(
            reading_mm=reading,
            sleeve_mm=whole,
            thimble_division=fraction,
            least_count_mm=0.01,
        )
    elif v == "scope_reading":
        panel(d, 35, 35, 410, 245)
        d.rect(55, 55, 285, 200, fill=d.surface)
        for i in range(11):
            d.line(55 + i * 28.5, 55, 55 + i * 28.5, 255, color=d.muted, width=0.5)
        for i in range(9):
            d.line(55, 55 + i * 25, 340, 55 + i * 25, color=d.muted, width=0.5)
        d.poly(
            [
                (
                    55 + 285 * t,
                    155 - 40 * p["amplitude"] * math.sin(2 * math.pi * p["cycles"] * t),
                )
                for t in [i / 120 for i in range(121)]
            ],
            color=d.green,
        )
        for y, label in [(90, "V/div"), (170, "s/div")]:
            d.circle(390, y, 19, fill=d.glass)
            d.line(390, y, 401, y - 10)
            text(d, label, 390, y + 40, size=12)
        d.facts.update(cycles=p["cycles"], amplitude_divisions=p["amplitude"] * 1.6)
    elif v == "ultrasonic_distance":
        panel(d, 40, 115, 75, 90)
        d.circle(77, 140, 12, fill=d.muted)
        d.circle(77, 180, 12, fill=d.muted)
        d.rect(395, 45, 15, 235, fill=d.surface)
        d.hatch(410, 45, 20, 235)
        d.arrow(120, 130, 390, 130, color=d.blue)
        d.arrow(390, 190, 120, 190, color=d.gold)
        for x in [145, 205, 265, 325]:
            d.path(f"M {x} 118 Q {x+15} 130 {x} 142", color=d.blue)
        text(d, "d = vΔt / 2", 245, 265)
    elif v == "fall_timing":
        d.line(100, 40, 100, 275, width=4)
        d.rect(65, 275, 130, 10, fill=d.muted)
        ball(d, 135, 65, 12)
        d.rect(120, 48, 30, 10, fill=d.surface)
        for y in [140, 245]:
            d.path(
                f"M 100 {y-14} L 170 {y-14} L 170 {y+14} L 100 {y+14}",
                color=d.blue,
                width=4,
            )
            d.line(108, y, 164, y, color=d.red, dashed=True)
        d.arrow(135, 86, 135, 232, color=d.gold)
        panel(d, 290, 110, 125, 100)
        d.rect(305, 125, 95, 35, fill=d.glass)
        text(d, "Δt", 352, 150)
        d.poly([(170, 140), (230, 140), (230, 180), (290, 180)], color=d.muted)
        d.poly([(170, 245), (250, 245), (250, 190), (290, 190)], color=d.muted)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
