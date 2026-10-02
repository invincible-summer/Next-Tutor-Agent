"""Independent mechanism, section and astronomy geometry illustrations."""

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


def parameters(v):
    if v in {"crank_slider", "four_bar"}:
        return numeric("angle", 35, 10, 60)
    if v in {"planet_tilt", "orbit_inclination"}:
        return numeric("angle", 23.44 if v == "planet_tilt" else 30, 0, 60)
    if v == "hr_diagram":
        return {"stars": {"type": "list", "required": True, "max_abs": 100000}}
    if v == "dimension_tolerance":
        return numeric("diameter", 20, 5, 100) | numeric("tolerance", 0.2, 0.01, 1)
    return {}


def _gear(d, x, y, r=55, teeth=18):
    pts = []
    for i in range(teeth * 4):
        a = i * 2 * math.pi / (teeth * 4)
        rr = r + (6 if i % 4 in {1, 2} else -3)
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    d.poly(pts, closed=True, fill=d.glass)
    d.circle(x, y, r * 0.35, fill=d.surface)
    d.circle(x, y, 6, fill=d.muted)
    d.facts["tooth_profile"] = "educational_schematic_not_manufacturing_involute"


def _support(d, x, y, roller=False):
    d.poly([(x, y), (x - 20, y + 31), (x + 20, y + 31)], closed=True, fill=d.surface)
    if roller:
        for dx in [-10, 10]:
            d.circle(x + dx, y + 38, 5, fill=d.muted, width=1)
    else:
        d.line(x - 27, y + 34, x + 27, y + 34)
        d.hatch(x - 27, y + 34, 54, 14)


def _planet(d, x, y, r):
    ball(d, x, y, r)
    d.path(
        f"M {x-r*.7} {y-r*.3} Q {x-r*.1} {y-r*.7} {x+r*.5} {y-r*.15} Q {x+r*.2} {y+r*.6} {x-r*.35} {y+r*.3} Z",
        fill=d.green,
        color=d.green,
        width=1,
    )


def _beam(d, kind):
    d.rect(65, 103, 350, 20, fill=d.glass)
    if kind == "cantilever":
        d.rect(43, 71, 20, 104, fill=d.muted)
        d.hatch(27, 71, 16, 104)
        d.arrow(405, 52, 405, 101, color=d.red, width=3)
    else:
        _support(d, 80, 125)
        _support(d, 400, 125, True)
        d.arrow(240, 45, 240, 101, color=d.red, width=3)
    for y, label in [(219, "V"), (283, "M")]:
        d.line(70, y, 414, y, color=d.muted, width=1)
        text(d, label, 44, y + 5)
    if kind == "cantilever":
        d.poly([(70, 186), (405, 186), (405, 219)], color=d.blue)
        d.poly([(70, 244), (405, 283)], color=d.gold)
    else:
        d.poly([(80, 193), (240, 193), (240, 245), (400, 245)], color=d.blue)
        d.poly([(80, 283), (240, 253), (400, 283)], color=d.gold)


def draw(v, p, mono=False):
    d = canvas(mono)
    if v == "rack_pinion":
        _gear(d, 240, 143, 66, 20)
        pitch = 2 * math.pi * 66 / 20
        pts = [(50, 221)]
        for i in range(18):
            x = 50 + i * pitch
            pts.extend(
                [
                    (x, 221),
                    (x + pitch * 0.25, 211),
                    (x + pitch * 0.65, 211),
                    (x + pitch * 0.85, 221),
                ]
            )
        pts.extend([(429, 245), (50, 245)])
        d.poly(pts, closed=True, fill=d.gold)
        d.arrow(120, 277, 320, 277, color=d.blue)
        d.path("M 202 97 Q 247 64 282 108", color=d.red)
    elif v == "worm_gear":
        _gear(d, 270, 195, 67, 24)
        d.rect(80, 90, 280, 27, fill=d.surface, radius=10)
        for i in range(14):
            x = 92 + i * 18
            d.line(x, 91, x + 13, 116, color=d.blue, width=3)
        d.line(235, 195, 405, 195, width=8)
        d.arrow(395, 174, 395, 217, color=d.gold)
        text(d, "perpendicular shafts", 240, 304)
    elif v in {"belt_drive", "chain_drive"}:
        for x, r in [(115, 55), (350, 85)]:
            d.circle(x, 165, r, fill=d.glass)
            d.circle(x, 165, 10, fill=d.muted)
        angle = math.acos((55 - 85) / 235)
        for sign in [-1, 1]:
            a = (115 + 55 * math.cos(angle), 165 + sign * 55 * math.sin(angle))
            b = (350 + 85 * math.cos(angle), 165 + sign * 85 * math.sin(angle))
            d.line(*a, *b, color=d.gold, width=4)
            if v == "chain_drive":
                for i in range(18):
                    t = i / 17
                    d.circle(
                        a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t,
                        3,
                        fill=d.surface,
                        width=1,
                    )
        if v == "chain_drive":
            for x, r in [(115, 55), (350, 85)]:
                for i in range(18):
                    a = i * math.pi / 9
                    d.circle(
                        x + r * math.cos(a),
                        165 + r * math.sin(a),
                        3,
                        fill=d.surface,
                        width=1,
                    )
        d.arrow(190, 55, 290, 55, color=d.blue)
        text(
            d,
            (
                "linked transmission"
                if v == "chain_drive"
                else "tangent belt transmission"
            ),
            240,
            301,
        )
    elif v == "crank_slider":
        theta = math.radians(p["angle"])
        o = (105, 174)
        r, L = 55, 190
        a = (o[0] + r * math.cos(theta), o[1] - r * math.sin(theta))
        b = (a[0] + math.sqrt(L * L - (o[1] - a[1]) ** 2), o[1])
        d.circle(*o, r, color=d.muted)
        d.line(50, 215, 427, 215, width=3)
        d.rect(b[0] - 26, b[1] - 23, 52, 46, fill=d.glass)
        d.line(*o, *a, color=d.red, width=7)
        d.line(*a, *b, color=d.blue, width=6)
        for point in [o, a, b]:
            d.circle(*point, 6, fill=d.gold)
        d.arrow(b[0] - 45, 248, b[0] + 45, 248, color=d.blue, double=True)
        d.facts.update(
            crank_angle=p["angle"],
            crank_radius=r,
            rod_length=L,
            slider_position=b[0] - o[0],
        )
    elif v == "four_bar":
        a, b = (90, 250), (390, 250)
        theta = math.radians(p["angle"])
        q = (a[0] + 100 * math.cos(theta), a[1] - 100 * math.sin(theta))
        dx, dy = b[0] - q[0], b[1] - q[1]
        length = math.hypot(dx, dy)
        half = length / 2
        height = math.sqrt(190**2 - half**2)
        c = (
            (q[0] + b[0]) / 2 + dy / length * height,
            (q[1] + b[1]) / 2 - dx / length * height,
        )
        for aa, bb, col in [
            (a, q, d.red),
            (q, c, d.blue),
            (c, b, d.green),
            (a, b, d.muted),
        ]:
            d.line(*aa, *bb, color=col, width=6)
        for x, y in [a, q, c, b]:
            d.circle(x, y, 7, fill=d.gold)
        _support(d, *a)
        _support(d, *b)
        d.facts["link_lengths"] = [100, 190, 190, 300]
    elif v == "cam_follower":
        center = (220, 203)
        pts = []
        for i in range(100):
            a = i * 2 * math.pi / 100
            r = 60 + 28 * (1 + math.cos(a + math.pi / 2)) / 2
            pts.append((center[0] + r * math.cos(a), center[1] + r * math.sin(a)))
        d.poly(pts, closed=True, fill=d.glass)
        d.circle(*center, 8, fill=d.muted)
        d.circle(220, 108, 14, fill=d.surface)
        d.line(220, 94, 220, 40, width=6)
        d.rect(195, 43, 50, 12, fill=d.muted)
        d.poly(
            [(209 + 8 * math.sin(i * math.pi / 2), 61 + i * 3) for i in range(10)],
            color=d.gold,
        )
        d.arrow(330, 81, 330, 139, color=d.blue, double=True)
        text(d, "follower", 380, 117, size=12)
    elif v == "bearing_section":
        for r, col in [
            (112, d.muted),
            (92, d.surface),
            (68, d.glass),
            (49, d.muted),
            (31, d.surface),
        ]:
            d.circle(240, 160, r, fill=col, width=1.5)
        for i in range(10):
            a = i * math.pi / 5
            ball(d, 240 + 80 * math.cos(a), 160 + 80 * math.sin(a), 11)
        d.line(110, 160, 370, 160, color=d.red, dashed=True, width=1)
        text(d, "rolling elements / races", 240, 305)
    elif v == "thread_joint":
        d.rect(50, 130, 380, 42, fill=d.glass)
        d.rect(50, 177, 380, 42, fill=d.gold)
        d.rect(211, 91, 58, 172, fill=d.surface)
        d.poly([(202, 71), (278, 71), (285, 91), (195, 91)], closed=True, fill=d.muted)
        d.poly(
            [(200, 235), (280, 235), (289, 250), (280, 272), (200, 272), (191, 250)],
            closed=True,
            fill=d.muted,
        )
        for y in range(111, 265, 12):
            d.line(212, y, 268, y - 8, color=d.muted, width=1.5)
        d.line(240, 44, 240, 290, color=d.red, dashed=True, width=1)
    elif v == "coupling":
        d.rect(40, 146, 400, 33, fill=d.surface)
        d.rect(164, 113, 69, 99, fill=d.glass)
        d.rect(247, 113, 69, 99, fill=d.glass)
        for y in [135, 192]:
            d.rect(219, y, 42, 8, fill=d.gold)
        d.line(40, 163, 440, 163, dashed=True, color=d.red, width=1)
        text(d, "shaft alignment / torque transfer", 240, 283, size=13)
    elif v == "truss_joint":
        o = (240, 175)
        for q, col in [((76, 259), d.blue), ((412, 260), d.green), ((238, 42), d.red)]:
            d.line(*o, *q, width=7, color=col)
        d.poly(
            [(204, 143), (274, 142), (288, 209), (193, 209)],
            closed=True,
            fill=d.surface,
        )
        for x, y in [(226, 151), (251, 151), (212, 195), (269, 195)]:
            d.circle(x, y, 5, fill=d.muted)
        d.arrow(240, 178, 240, 266, color=d.gold, width=3)
        text(d, "joint equilibrium", 240, 311)
    elif v in {"cantilever", "supported_beam"}:
        _beam(d, v)
    elif v == "axial_load":
        box(d, 125, 127, 230, 66, 25)
        d.arrow(119, 160, 40, 160, color=d.red, width=4)
        d.arrow(383, 160, 450, 160, color=d.red, width=4)
        d.line(249, 110, 249, 210, color=d.blue, dashed=True)
        text(d, "P", 60, 131)
        text(d, "P", 416, 131)
        text(d, "σ = P/A", 240, 275, size=20)
    elif v == "torsion":
        d.rect(105, 115, 267, 90, fill=d.glass)
        d.ellipse(105, 160, 24, 45, fill=d.surface)
        d.ellipse(372, 160, 24, 45, fill=d.glass)
        for y in [125, 160, 195]:
            d.line(110, y, 370, 160 + (y - 160) * 0.7, color=d.blue, width=1)
        d.path("M 377 100 C 449 114 449 207 377 223", color=d.red, width=3)
        d.arrow(390, 221, 377, 223, color=d.red)
        text(d, "T", 433, 86)
        text(d, "τ = Tr/J", 240, 282, size=20)
    elif v == "hydraulic_valve":
        for x in [144, 240, 336]:
            d.rect(x - 48, 98, 96, 113, fill=d.glass)
        d.arrow(120, 187, 120, 122, color=d.blue)
        d.arrow(168, 122, 168, 187, color=d.blue)
        d.arrow(312, 187, 360, 122, color=d.blue)
        d.arrow(312, 122, 360, 187, color=d.blue)
        for x, upper, lower in [(216, "A", "P"), (264, "B", "T")]:
            d.line(x, 65, x, 124)
            d.line(x - 10, 124, x + 10, 124)
            d.line(x, 185, x, 254)
            d.line(x - 10, 185, x + 10, 185)
            text(d, upper, x, 51)
            text(d, lower, x, 277)
        d.line(96, 155, 55, 155, width=3)
        d.line(384, 155, 425, 155, width=3)
        text(d, "4/3 valve · closed center", 240, 310, size=12)
    elif v == "pneumatic_control":
        d.rect(245, 55, 152, 53, fill=d.glass)
        d.rect(296, 58, 10, 47, fill=d.muted)
        d.line(305, 81, 444, 81, width=5)
        node(d, "3/2 valve", 176, 211, w=95, h=60)
        node(d, "air", 64, 211, w=63, h=46)
        d.arrow(98, 211, 124, 211, color=d.blue)
        d.poly([(176, 178), (176, 83), (245, 83)], color=d.blue, width=3)
        for i in range(10):
            d.line(325 + i * 7, 64, 330 + i * 7, 97, color=d.gold, width=1)
        text(d, "single-acting cylinder", 331, 139, size=12)
        d.arrow(198, 241, 198, 275, color=d.blue)
    elif v == "feedback_control":
        for x, label, w in [
            (53, "r", 36),
            (135, "Σ", 43),
            (254, "controller", 100),
            (398, "plant", 83),
        ]:
            node(d, label, x, 111, w=w, h=43)
        for a, b in [(73, 110), (159, 196), (308, 351)]:
            d.arrow(a, 111, b, 111, color=d.blue)
        d.poly([(425, 135), (425, 233), (135, 233), (135, 136)], color=d.green)
        d.arrow(135, 162, 135, 136, color=d.green)
        node(d, "sensor", 287, 233, w=92, h=38, fill=d.green)
        text(d, "−", 115, 160, size=19)
        text(d, "y", 446, 115)
    elif v == "robot_chain":
        points = [(100, 265), (129, 179), (247, 121), (329, 168), (382, 103)]
        for a, b in zip(points, points[1:]):
            d.line(*a, *b, color=d.blue, width=8)
        for x, y in points[:-1]:
            d.circle(x, y, 13, fill=d.gold)
            d.circle(x, y, 4, fill=d.muted)
        d.rect(66, 278, 74, 10, fill=d.muted)
        d.poly([(368, 90), (394, 114), (416, 96)], width=4)
        for i, (x, y) in enumerate(points[1:-1]):
            text(d, f"θ{i+1}", x - 24, y - 20, size=12)
    elif v == "dimension_tolerance":
        d.rect(122, 82, 230, 178, fill=d.surface)
        d.circle(237, 171, 60, fill=d.glass)
        d.line(237, 75, 237, 270, color=d.muted, dashed=True, width=1)
        d.line(112, 171, 362, 171, color=d.muted, dashed=True, width=1)
        d.arrow(195, 212, 279, 128, double=True, color=d.blue)
        d.line(277, 132, 366, 54, color=d.blue)
        text(d, f"Ø{p['diameter']:g} ± {p['tolerance']:g}", 365, 35, size=13)
        d.facts.update(
            nominal_diameter=p["diameter"],
            tolerance=p["tolerance"],
            drawing_to_scale=False,
        )
    elif v == "solar_system":
        ball(d, 55, 160, 32, d.gold)
        names = [
            "Mercury",
            "Venus",
            "Earth",
            "Mars",
            "Jupiter",
            "Saturn",
            "Uranus",
            "Neptune",
        ]
        for i, name in enumerate(names):
            x = 115 + i * 43
            r = [5, 9, 10, 7, 19, 17, 13, 12][i]
            ball(
                d,
                x,
                160,
                r,
                [d.muted, d.gold, d.blue, d.red, d.gold, d.gold, d.blue, d.blue][i],
            )
            if name == "Saturn":
                d.ellipse(x, 160, 25, 7, color=d.muted)
            text(d, name, x, 210 + (i % 2) * 29, size=9)
        text(d, "schematic · sizes and distances compressed", 240, 288, size=12)
        d.facts["to_scale"] = False
    elif v == "planet_tilt":
        _planet(d, 240, 159, 103)
        a = math.radians(p["angle"])
        d.line(
            240 - 130 * math.sin(a),
            159 + 130 * math.cos(a),
            240 + 130 * math.sin(a),
            159 - 130 * math.cos(a),
            color=d.red,
            width=3,
        )
        d.line(240, 26, 240, 293, dashed=True, color=d.muted)
        equator = canvas(mono)
        equator.ellipse(0, 0, 103, 25, color=d.ink, width=1)
        d.add(equator, 240, 159, 1, p["angle"])
        text(d, f"tilt {p['angle']:g}°", 371, 43)
        d.facts["axial_tilt"] = p["angle"]
    elif v == "earth_moon_scale":
        _planet(d, 128, 156, 76)
        ball(d, 373, 156, 76 / 3.67, d.muted)
        d.poly([(239, 139), (224, 155), (239, 171), (224, 187)], color=d.muted)
        text(d, "diameter ratio ≈ 3.67 : 1", 240, 258)
        text(d, "distance compressed with break", 240, 290, size=12)
        d.facts.update(diameter_ratio=3.67, distance_scale_compressed=True)
    elif v in {"equatorial_coordinates", "horizontal_coordinates"}:
        d.circle(240, 160, 114, fill=d.glass)
        plane = canvas(mono)
        plane.ellipse(0, 0, 114, 31, color=d.muted)
        tilt = 23 if v == "equatorial_coordinates" else 0
        d.add(plane, 240, 160, 1, tilt)
        d.ellipse(240, 160, 45, 114, color=d.muted, width=1)
        if v == "horizontal_coordinates":
            d.circle(240, 150, 5, fill=d.ink)
            d.line(240, 155, 240, 173, width=3)
            text(d, "observer", 207, 199, size=10)
            text(d, "zenith", 240, 23, size=11)
        else:
            d.line(
                240 - 125 * math.sin(math.radians(23)),
                160 + 125 * math.cos(math.radians(23)),
                240 + 125 * math.sin(math.radians(23)),
                160 - 125 * math.cos(math.radians(23)),
                color=d.blue,
                dashed=True,
                width=1,
            )
            text(d, "celestial pole", 334, 32, size=11)
        d.line(240, 29, 240, 292, color=d.muted, dashed=True)
        q = (301, 75)
        d.circle(*q, 6, fill=d.gold)
        d.line(240, 160, *q, color=d.blue)
        d.path("M 300 75 Q 331 130 321 181", color=d.red, width=2)
        text(
            d,
            "δ / α" if v == "equatorial_coordinates" else "altitude / azimuth",
            240,
            310,
            size=13,
        )
        text(
            d,
            "celestial equator" if v == "equatorial_coordinates" else "horizon",
            386,
            214,
            size=11,
        )
    elif v == "stellar_parallax":
        d.ellipse(240, 245, 156, 42, color=d.muted)
        ball(d, 240, 245, 15, d.gold)
        for x in [84, 396]:
            _planet(d, x, 245, 10)
            d.line(x, 245, 240, 49, color=d.blue)
        d.circle(240, 49, 7, fill=d.gold)
        d.line(240, 49, 240, 244, dashed=True, color=d.muted)
        text(d, "p", 260, 99)
        text(d, "baseline = 2 AU", 240, 311)
        d.facts["angles_exaggerated"] = True
    elif v == "annual_motion":
        d.ellipse(240, 164, 164, 70, color=d.muted)
        ball(d, 240, 164, 18, d.gold)
        for i in range(4):
            a = i * math.pi / 2
            x = 240 + 164 * math.cos(a)
            y = 164 + 70 * math.sin(a)
            _planet(d, x, y, 12)
            d.arrow(
                x,
                y,
                x + 24 * math.cos(a + math.pi / 2),
                y + 24 * math.sin(a + math.pi / 2),
                color=d.blue,
            )
        text(d, "annual apparent solar motion", 240, 307)
    elif v == "spectral_classes":
        classes = ["O", "B", "A", "F", "G", "K", "M"]
        colors = (
            [
                "#7296c9",
                "#91add0",
                "#b7c7d2",
                "#ded5b3",
                "#d4b66f",
                "#ca9768",
                "#b9796b",
            ]
            if not mono
            else [d.muted] * 7
        )
        for i, (label, c) in enumerate(zip(classes, colors)):
            ball(d, 60 + i * 60, 137, 22, c)
            text(d, label, 60 + i * 60, 204, size=21)
        d.arrow(422, 262, 56, 262, color=d.red)
        text(d, "effective temperature increases", 240, 293, size=12)
    elif v == "hr_diagram":
        stars = p["stars"]
        if not 3 <= len(stars) <= 40 or any(
            not isinstance(a, list)
            or len(a) != 2
            or any(type(n) not in {int, float} for n in a)
            or not 2500 <= a[0] <= 50000
            or not 0.0001 <= a[1] <= 100000
            for a in stars
        ):
            raise DiagramError("diagram_invalid_stars")
        xy = axes(d, x_label="Tₑ𝒇𝒇 (hot → cool)", y_label="L / L☉")
        for temp, lum in stars:
            x = (math.log(50000) - math.log(temp)) / (math.log(50000) - math.log(2500))
            y = (math.log10(lum) + 4) / 9
            d.circle(*xy(x, y), 4, fill=d.gold, width=1)
        d.facts.update(
            temperature_axis_reversed=True,
            luminosity_logarithmic=True,
            data_source="task_parameters",
        )
    elif v == "stellar_evolution":
        node(d, "nebula", 70, 155, w=80, h=48)
        node(d, "main sequence", 233, 155, w=123, h=48)
        connect(d, (116, 155), (165, 155))
        for x, y, label, col in [
            (400, 73, "giant → remnant", d.red),
            (400, 236, "supergiant → SN", d.gold),
        ]:
            node(d, label, x, y, w=130, h=47, fill=col)
            d.arrow(295, 145 if y < 155 else 169, 333, y, color=d.blue)
        text(d, "mass-dependent schematic branches", 240, 300, size=12)
    elif v in {"star_cluster", "spiral_galaxy", "elliptical_galaxy"}:
        if v == "star_cluster":
            for i in range(70):
                a = i * 2.39996
                r = 12 * math.sqrt(i)
                d.circle(
                    240 + r * math.cos(a),
                    160 + r * 0.85 * math.sin(a),
                    1.5 + (i % 3) * 0.7,
                    fill=d.gold,
                    width=0.5,
                )
        elif v == "spiral_galaxy":
            d.ellipse(240, 160, 42, 28, fill=d.gold, color=d.gold)
            for arm in range(3):
                points = []
                for i in range(75):
                    t = i / 74 * 2 * math.pi
                    a = t + arm * 2 * math.pi / 3
                    r = 30 + 20 * t
                    points.append((240 + r * math.cos(a), 160 + r * 0.65 * math.sin(a)))
                d.poly(points, color=d.blue, width=6)
                for x, y in points[::5]:
                    d.circle(x, y, 2, fill=d.gold, width=0.5)
        else:
            for r in [140, 110, 80, 50]:
                d.ellipse(240, 160, r, r * 0.65, color=d.gold, width=2)
            for i in range(55):
                a = i * 2.39996
                r = 15 * math.sqrt(i)
                d.circle(
                    240 + r * math.cos(a),
                    160 + r * 0.6 * math.sin(a),
                    2,
                    fill=d.gold,
                    width=0.5,
                )
        d.facts["schematic_not_real_object_image"] = True
    elif v in {"refractor", "reflector"}:
        d.rect(55, 99, 365, 120, fill=d.surface)
        if v == "refractor":
            d.ellipse(79, 159, 12, 60, fill=d.glass)
            d.ellipse(385, 159, 7, 19, fill=d.glass)
            for y in [122, 159, 196]:
                d.poly(
                    [(30, y), (79, y), (347, 159), (411, 159 + (y - 159) * 0.3)],
                    color=d.gold,
                    width=1.5,
                )
        else:
            d.path("M 387 103 Q 435 159 387 214", color=d.blue, width=5)
            d.line(253, 140, 277, 167, width=5)
            for y in [119, 197]:
                d.poly(
                    [(29, y), (398, y), (265, 151), (265, 65)], color=d.gold, width=1.5
                )
            d.rect(252, 58, 26, 45, fill=d.glass)
        text(
            d,
            "objective lens" if v == "refractor" else "primary / secondary mirrors",
            240,
            289,
        )
    elif v == "space_telescope":
        d.poly(
            [(172, 115), (235, 79), (346, 145), (283, 181)], closed=True, fill=d.surface
        )
        d.poly(
            [(172, 115), (172, 188), (283, 255), (283, 181)], closed=True, fill=d.muted
        )
        d.ellipse(310, 165, 31, 43, fill=d.glass)
        for x, y in [(63, 150), (318, 55)]:
            d.poly(
                [(x, y), (x + 95, y - 15), (x + 126, y + 30), (x + 31, y + 45)],
                closed=True,
                fill=d.blue,
            )
            d.line(x + 32, y - 5, x + 60, y + 40, color=d.glass, width=1)
            d.line(x + 65, y - 10, x + 95, y + 35, color=d.glass, width=1)
        text(d, "generic orbital observatory", 240, 309)
    elif v == "orbit_inclination":
        _planet(d, 240, 160, 44)
        d.ellipse(240, 160, 170, 44, color=d.muted)
        a = math.radians(p["angle"])
        part = canvas(mono)
        part.ellipse(0, 0, 145, 35, color=d.blue)
        d.add(part, 240, 160, 1, -p["angle"])
        d.line(68, 160, 412, 160, dashed=True, color=d.gold)
        text(d, f"i = {p['angle']:g}°", 375, 272)
        d.facts["inclination"] = p["angle"]
    elif v == "transfer_orbit":
        _planet(d, 185, 160, 23)
        d.circle(185, 160, 57, color=d.blue)
        d.circle(185, 160, 129, color=d.green)
        d.ellipse(221, 160, 93, math.sqrt(93**2 - 36**2), color=d.gold)
        d.circle(128, 160, 5, fill=d.red)
        d.circle(314, 160, 5, fill=d.red)
        text(d, "Hohmann transfer · coplanar circular orbits", 240, 310, size=12)
        d.facts.update(
            inner_radius=57,
            outer_radius=129,
            transfer_semimajor_axis=93,
            transfer_focus_offset=36,
        )
    elif v == "tidal_bulge":
        d.ellipse(210, 160, 141, 88, fill=d.glass)
        _planet(d, 210, 160, 82)
        ball(d, 416, 160, 18, d.muted)
        d.line(52, 160, 436, 160, dashed=True, color=d.muted, width=1)
        d.arrow(209, 160, 334, 160, color=d.blue)
        d.arrow(194, 160, 84, 160, color=d.blue)
        text(d, "tidal deformation exaggerated", 240, 292, size=12)
    elif v == "eclipse_detail":
        for y in [85, 228]:
            ball(d, 60, y, 35, d.gold)
        d.poly([(340, 78), (410, 85), (340, 92)], closed=True, fill=d.muted, width=1)
        d.poly(
            [(340, 78), (433, 64), (433, 106), (340, 92)],
            closed=True,
            color=d.gold,
            width=1,
        )
        _planet(d, 410, 85, 24)
        ball(d, 340, 85, 7, d.muted)
        d.poly(
            [(240, 204), (435, 216), (435, 240), (240, 252)],
            closed=True,
            fill=d.muted,
            width=1,
        )
        d.poly(
            [(240, 204), (435, 168), (435, 288), (240, 252)],
            closed=True,
            color=d.gold,
            width=1,
        )
        _planet(d, 240, 228, 24)
        ball(d, 408, 228, 7, d.red)
        text(d, "solar eclipse", 240, 30, size=13)
        text(d, "lunar eclipse", 240, 174, size=13)
        text(d, "shadow relationships · distances compressed", 240, 311, size=11)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
