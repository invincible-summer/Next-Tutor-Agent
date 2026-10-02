"""Independent mathematical constructions; coordinates derive from relations."""

import math

from .drawing import num
from .extended_common import canvas, axes, curve, text, node, connect, numeric, box
from .schema import DiagramError


def parameters(v):
    if v in {"ellipse_foci", "hyperbola_asymptotes"}:
        return numeric(
            "eccentricity",
            0.6 if v == "ellipse_foci" else 1.5,
            0.1 if v == "ellipse_foci" else 1.1,
            0.9 if v == "ellipse_foci" else 2,
        )
    if v == "grouped_array":
        return {**numeric("rows", 3, 1, 5, True), **numeric("columns", 4, 1, 8, True)}
    if v == "equivalent_fractions":
        return {
            **numeric("numerator", 1, 1, 3, True),
            **numeric("denominator", 3, 2, 6, True),
        }
    if v == "inequality_line":
        return {**numeric("left", -1, -3, 2), **numeric("right", 2, -2, 3)}
    return {}


def _circumcenter(a, b, c):
    determinant = 2 * (
        a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1])
    )
    if abs(determinant) < 1e-6:
        raise DiagramError("diagram_degenerate_triangle")
    sq = lambda p: p[0] ** 2 + p[1] ** 2
    return (
        (sq(a) * (b[1] - c[1]) + sq(b) * (c[1] - a[1]) + sq(c) * (a[1] - b[1]))
        / determinant,
        (sq(a) * (c[0] - b[0]) + sq(b) * (a[0] - c[0]) + sq(c) * (b[0] - a[0]))
        / determinant,
    )


def draw(v, p, mono=False):
    d = canvas(mono)
    if v in {"bisector_construction", "perpendicular_bisector"}:
        for offset in [0, 240]:
            if v == "bisector_construction":
                a = (offset + 35, 245)
                ends = [(offset + 205, 245), (offset + 165, 60)]
                for end in ends:
                    d.line(*a, *end)
                angle = math.atan2(-185, 130)
                q = (a[0] + 60 * math.cos(angle), a[1] + 60 * math.sin(angle))
                d.path(f"M {offset+95} 245 A 60 60 0 0 0 {q[0]} {q[1]}", color=d.blue)
                if offset:
                    first = (a[0] + 60, a[1])
                    mid = ((first[0] + q[0]) / 2, (first[1] + q[1]) / 2)
                    half = math.dist(first, q) / 2
                    height = math.sqrt(70**2 - half**2)
                    cross = (
                        mid[0] + height * math.cos(angle / 2),
                        mid[1] + height * math.sin(angle / 2),
                    )
                    for center in [first, q]:
                        theta = math.atan2(cross[1] - center[1], cross[0] - center[0])
                        d.poly(
                            [
                                (
                                    center[0] + 70 * math.cos(theta + t),
                                    center[1] + 70 * math.sin(theta + t),
                                )
                                for t in [-0.24 + i * 0.024 for i in range(21)]
                            ],
                            color=d.gold,
                        )
                    d.line(
                        *a,
                        a[0] + 173 * math.cos(angle / 2),
                        a[1] + 173 * math.sin(angle / 2),
                        color=d.red,
                    )
            else:
                d.line(offset + 38, 165, offset + 202, 165)
                for x in [offset + 38, offset + 202]:
                    d.circle(x, 165, 3, fill=d.ink)
                    side = 1 if x == offset + 38 else -1
                    angle = math.acos(82 / 120)
                    for sign in [-1, 1]:
                        theta = sign * angle if side == 1 else math.pi + sign * angle
                        d.poly(
                            [
                                (
                                    x + 120 * math.cos(theta + t),
                                    165 + 120 * math.sin(theta + t),
                                )
                                for t in [-0.18 + i * 0.018 for i in range(21)]
                            ],
                            color=d.blue if side == 1 else d.gold,
                        )
                if offset:
                    d.line(offset + 120, 50, offset + 120, 280, color=d.red)
            text(d, "1" if not offset else "2", offset + 120, 302)
    elif v in {"alternate_angles", "corresponding_angles"}:
        for y in [90, 210]:
            d.line(40, y, 440, y, width=3)
        d.line(160, 35, 320, 285)
        # A transversal with the same orientation at both intersections.
        for y, turn in [(90, 0), (210, math.pi if v == "alternate_angles" else 0)]:
            x = 160 + (y - 35) * 160 / 250
            a = math.atan2(250, 160) + turn
            b = turn
            points = [
                (
                    x + 29 * math.cos(b + (a - b) * i / 20),
                    y + 29 * math.sin(b + (a - b) * i / 20),
                )
                for i in range(21)
            ]
            d.poly(points, color=d.gold, width=3)
        d.facts["parallel_lines"] = True
    elif v in {"power_circle", "intersecting_chords"}:
        cx, cy, r = 275, 155, 100
        d.circle(cx, cy, r)
        if v == "power_circle":
            point = (55, 155)
            for angle in [-0.28, 0.28]:
                dx, dy = math.cos(angle), math.sin(angle)
                proj = (cx - point[0]) * dx + (cy - point[1]) * dy
                disc = proj**2 - ((cx - point[0]) ** 2 + (cy - point[1]) ** 2 - r * r)
                roots = [proj - math.sqrt(disc), proj + math.sqrt(disc)]
                d.line(
                    *point,
                    point[0] + (roots[1] + 15) * dx,
                    point[1] + (roots[1] + 15) * dy,
                )
                for root in roots:
                    d.circle(point[0] + root * dx, point[1] + root * dy, 3, fill=d.blue)
            d.circle(*point, 4, fill=d.red)
        else:
            for aa, bb in [(-2.6, 0.5), (-0.9, 2.3)]:
                d.line(
                    cx + r * math.cos(aa),
                    cy + r * math.sin(aa),
                    cx + r * math.cos(bb),
                    cy + r * math.sin(bb),
                    color=d.blue if aa < -2 else d.red,
                )
    elif v == "common_tangents":
        for x in [130, 345]:
            d.circle(x, 160, 65)
        for y in [95, 225]:
            d.line(45, y, 430, y, color=d.blue)
        angle = math.acos(130 / 215)
        for sign in [-1, 1]:
            a = (130 + 65 * math.cos(angle), 160 + sign * 65 * math.sin(angle))
            b = (345 - 65 * math.cos(angle), 160 - sign * 65 * math.sin(angle))
            d.line(*a, *b, color=d.gold)
        d.line(130, 160, 345, 160, color=d.muted, dashed=True)
    elif v == "pythagorean_puzzle":
        a, b = 66, 88
        cx, cy = 205, 195
        d.poly([(cx, cy), (cx + a, cy), (cx, cy - b)], closed=True, fill=d.surface)
        d.rect(cx, cy, a, a, fill=d.blue)
        d.rect(cx - b, cy - b, b, b, fill=d.green)
        hyp = [(cx, cy - b), (cx + a, cy), (cx + a + b, cy - a), (cx + b, cy - b - a)]
        d.poly(hyp, closed=True, fill=d.gold)
        d.facts.update(
            a=a, b=b, c=math.hypot(a, b), squares=[a * a, b * b, a * a + b * b]
        )
    elif v in {"similarity_scale", "area_dissection"}:
        if v == "similarity_scale":
            for x, scale in [(45, 1), (260, 0.6)]:
                d.poly(
                    [
                        (x, 240),
                        (x + 155 * scale, 240),
                        (x + 55 * scale, 240 - 170 * scale),
                    ],
                    closed=True,
                    fill=d.glass,
                )
                d.line(x, 270, x + 155 * scale, 270, color=d.gold)
        else:
            d.poly(
                [(55, 210), (230, 210), (280, 75), (105, 75)], closed=True, fill=d.blue
            )
            d.line(105, 75, 105, 210, dashed=True)
            d.rect(315, 75, 120, 135, fill=d.glass)
            d.poly([(315, 210), (315, 75), (365, 75)], closed=True, fill=d.gold)
            connect(d, (250, 155), (305, 155))
    elif v in {"centroid", "orthocenter", "nine_point_circle"}:
        a, b, c = (175, 45), (65, 235), (410, 235)
        d.poly([a, b, c], closed=True, fill=d.surface)
        centroid = ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3)
        o = _circumcenter(a, b, c)
        h = (a[0] + b[0] + c[0] - 2 * o[0], a[1] + b[1] + c[1] - 2 * o[1])
        for vertex, start, end in [(a, b, c), (b, a, c), (c, a, b)]:
            if v == "centroid":
                target = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
            else:
                dx, dy = end[0] - start[0], end[1] - start[1]
                t = ((vertex[0] - start[0]) * dx + (vertex[1] - start[1]) * dy) / (
                    dx * dx + dy * dy
                )
                target = (start[0] + t * dx, start[1] + t * dy)
            d.line(*vertex, *target, color=d.blue, width=1.5)
            d.circle(*target, 3, fill=d.gold)
        d.circle(*(centroid if v == "centroid" else h), 4, fill=d.red)
        if v == "nine_point_circle":
            center = ((o[0] + h[0]) / 2, (o[1] + h[1]) / 2)
            d.circle(*center, math.dist(o, a) / 2, color=d.green)
            d.facts["nine_point_radius"] = math.dist(o, a) / 2
        d.facts.update(centroid=centroid, orthocenter=h, circumcenter=o)
    elif v == "ellipse_foci":
        e = p["eccentricity"]
        a = 140
        b = a * math.sqrt(1 - e * e)
        d.ellipse(240, 160, a, b)
        focus = a * e
        point = (240 + a * 0.4, 160 - b * math.sqrt(0.84))
        for x in [240 - focus, 240 + focus]:
            d.circle(x, 160, 4, fill=d.red)
            d.line(x, 160, *point, color=d.gold)
        d.line(55, 160, 425, 160, dashed=True, color=d.muted)
        d.facts.update(eccentricity=e, focal_distance=focus, distance_sum=2 * a)
    elif v == "hyperbola_asymptotes":
        a = 70
        e = p["eccentricity"]
        b = a * math.sqrt(e * e - 1)
        for sign in [-1, 1]:
            pts = [
                (240 + sign * a * math.cosh(t), 160 + b * math.sinh(t))
                for t in [(-1.1 + i * 2.2 / 80) for i in range(81)]
            ]
            pts = [pt for pt in pts if 30 <= pt[0] <= 450 and 30 <= pt[1] <= 290]
            d.poly(pts, color=d.blue)
            extent = min(180, 130 * a / b)
            d.line(
                240 - extent,
                160 + sign * (extent * b / a),
                240 + extent,
                160 - sign * (extent * b / a),
                dashed=True,
                color=d.muted,
            )
        d.facts.update(eccentricity=e, asymptote_slope=b / a)
    elif v == "parabola_directrix":
        d.line(105, 45, 105, 280, color=d.gold)
        pts = [(130 + t * t / 100, 160 + t) for t in range(-115, 116, 3)]
        d.poly(pts, color=d.blue)
        focus = (155, 160)
        q = (194, 240)
        d.circle(*focus, 4, fill=d.red)
        d.line(*focus, *q)
        d.line(105, q[1], *q)
        d.facts.update(focal_parameter=25, focus=focus, directrix_x=105)
    elif v in {"polar_curve", "parametric_curve"}:
        d.line(50, 160, 435, 160, color=d.muted)
        d.line(240, 30, 240, 290, color=d.muted)
        if v == "polar_curve":
            for r in [50, 100]:
                d.circle(240, 160, r, color=d.muted, width=1)
            pts = []
            for i in range(181):
                t = i * 2 * math.pi / 180
                r = 115 * math.cos(3 * t)
                pts.append((240 + r * math.cos(t), 160 - r * math.sin(t)))
        else:
            pts = [
                (240 + 170 * math.sin(t), 160 + 105 * math.sin(2 * t))
                for t in [i * 2 * math.pi / 180 for i in range(181)]
            ]
        d.poly(pts, color=d.blue)
    elif v in {"piecewise_function", "inverse_symmetry", "feasible_region"}:
        xy = axes(d)
        if v == "piecewise_function":
            d.poly([xy(0.05, 0.75), xy(0.4, 0.15)], color=d.blue)
            d.poly([xy(0.4, 0.45), xy(0.9, 0.8)], color=d.green)
            d.circle(*xy(0.4, 0.15), 4, fill="#fff", color=d.blue)
            d.circle(*xy(0.4, 0.45), 4, fill=d.green)
        elif v == "inverse_symmetry":
            d.line(*xy(0, 0), *xy(0.95, 0.95), color=d.muted, dashed=True)
            curve(d, lambda x: x * x, xy)
            curve(d, math.sqrt, xy, color=d.red)
        else:
            d.poly(
                [xy(0.1, 0.1), xy(0.65, 0.1), xy(0.45, 0.6), xy(0.1, 0.7)],
                closed=True,
                fill=d.glass,
            )
            d.line(*xy(0, 0.74), *xy(0.9, 0.4), color=d.gold)
            d.line(*xy(0.22, 0.95), *xy(0.72, 0), color=d.blue)
    elif v == "inequality_line":
        lo, hi = p["left"], p["right"]
        if lo >= hi:
            raise DiagramError("diagram_invalid_interval")
        d.arrow(35, 160, 445, 160)
        for i in range(-3, 4):
            x = 240 + i * 55
            d.line(x, 155, x, 165)
            text(d, str(i), x, 192)
        d.line(240 + lo * 55, 160, 240 + hi * 55, 160, color=d.blue, width=7)
        d.circle(240 + lo * 55, 160, 6, fill="#fff", color=d.blue)
        d.circle(240 + hi * 55, 160, 6, fill=d.blue)
        d.facts.update(interval=[lo, hi], left_closed=False, right_closed=True)
    elif v in {"space_axes", "space_vector", "plane_normal"}:
        o = (215, 220)
        a = (425, 220)
        b = (95, 285)
        c = (215, 40)
        for endpoint, label in [(a, "x"), (b, "y"), (c, "z")]:
            d.arrow(*o, *endpoint)
            text(d, label, endpoint[0] + 12, endpoint[1] + 5)
        if v == "space_vector":
            tip = (325, 95)
            d.arrow(*o, *tip, color=d.blue, width=3)
            d.poly([tip, (325, 220), o], color=d.muted)
            d.line(*tip, 215, 95, dashed=True, color=d.muted)
        if v == "plane_normal":
            d.poly(
                [(125, 210), (285, 260), (385, 175), (225, 125)],
                closed=True,
                fill=d.glass,
            )
            d.arrow(255, 190, 280, 70, color=d.red, width=3)
    elif v == "solid_revolution":
        d.ellipse(250, 90, 95, 26, color=d.blue)
        d.ellipse(250, 245, 95, 26, color=d.blue)
        d.line(155, 90, 155, 245, color=d.blue)
        d.line(345, 90, 345, 245, color=d.blue)
        d.line(250, 40, 250, 285, dashed=True, color=d.muted)
        d.rect(250, 90, 95, 155, fill=d.glass, color=d.gold)
    elif v in {"cone_net", "cylinder_net", "pyramid_net", "frustum_net"}:
        if v == "cylinder_net":
            d.rect(220 - math.pi * 39, 105, 2 * math.pi * 39, 105, fill=d.glass)
            for y in [60, 255]:
                d.circle(220, y, 39, fill=d.surface)
            d.line(220, 99, 220, 105, dashed=True)
            d.line(220, 210, 220, 216, dashed=True)
        elif v == "pyramid_net":
            d.rect(190, 110, 100, 100, fill=d.surface)
            for pts in [
                [(190, 110), (290, 110), (240, 28)],
                [(290, 110), (290, 210), (380, 160)],
                [(190, 210), (290, 210), (240, 292)],
                [(190, 110), (190, 210), (100, 160)],
            ]:
                d.poly(pts, closed=True, fill=d.glass)
        elif v == "cone_net":
            d.path(
                "M 210 125 L 328.794 243.794 A 168 168 0 0 1 91.206 243.794 Z",
                fill=d.glass,
            )
            d.circle(385, 95, 42, fill=d.surface)
            d.line(210, 125, 210, 293, dashed=True, color=d.muted)
        else:
            d.path(
                "M 395.563 95.563 A 220 220 0 0 1 84.437 95.563 L 136.055 43.945 A 147 147 0 0 0 343.945 43.945 Z",
                fill=d.glass,
            )
            d.circle(155, 235, 55, fill=d.surface)
            d.circle(315, 235, 36.75, fill=d.surface)
    elif v == "euler_polyhedron":
        box(d, 155, 95, 145, 145, 50)
        for x, y in [
            (155, 95),
            (300, 95),
            (350, 60),
            (205, 60),
            (155, 240),
            (300, 240),
            (350, 205),
        ]:
            d.circle(x, y, 4, fill=d.red)
        d.facts.update(vertices=8, edges=12, faces=6, euler_characteristic=2)
    elif v == "tangram":
        origin = (120, 35)
        pieces = [
            [(0, 0), (4, 0), (2, 2)],
            [(0, 0), (0, 4), (2, 2)],
            [(4, 4), (4, 2), (2, 4)],
            [(4, 0), (4, 2), (3, 1)],
            [(2, 2), (3, 3), (1, 3)],
            [(2, 2), (3, 1), (4, 2), (3, 3)],
            [(0, 4), (2, 4), (3, 3), (1, 3)],
        ]
        for i, pts in enumerate(pieces):
            d.poly(
                [(origin[0] + x * 60, origin[1] + y * 60) for x, y in pts],
                closed=True,
                fill=[d.blue, d.green, d.gold, d.red, d.glass, d.surface, d.muted][i],
            )
        d.facts["piece_area_ratios"] = [4, 4, 2, 1, 1, 2, 2]
    elif v == "unit_cubes":
        for row in range(2):
            for col in range(3):
                box(d, 100 + col * 60 + row * 30, 125 - row * 35, 60, 60, 30)
        d.facts["unit_count"] = 6
    elif v == "equation_balance":
        d.line(65, 135, 415, 135, width=3)
        d.poly([(240, 150), (210, 235), (270, 235)], closed=True, fill=d.surface)
        for x in [115, 365]:
            d.poly([(x - 45, 195), (x, 135), (x + 45, 195)])
            d.path(f"M {x-48} 195 Q {x} 225 {x+48} 195 Z", fill=d.glass)
        node(d, "x", 110, 174, w=32, h=30)
        d.circle(148, 180, 12, fill=d.gold)
        for x in [340, 365, 390]:
            d.circle(x, 180, 12, fill=d.gold)
    elif v == "grouped_array":
        rows, cols = p["rows"], p["columns"]
        spacing = min(48, 340 / max(1, cols))
        x0 = 240 - (cols - 1) * spacing / 2
        y0 = 155 - (rows - 1) * 43 / 2
        for row in range(rows):
            d.rect(
                x0 - 18,
                y0 + row * 43 - 18,
                (cols - 1) * spacing + 36,
                36,
                fill=d.glass,
                radius=8,
            )
            for col in range(cols):
                d.circle(x0 + col * spacing, y0 + row * 43, 8, fill=d.blue)
        d.facts.update(rows=rows, columns=cols, total=rows * cols)
    elif v == "equivalent_fractions":
        n, k = p["numerator"], p["denominator"]
        if n >= k:
            raise DiagramError("diagram_invalid_fraction")
        for y, count, filled in [(95, k, n), (185, 2 * k, 2 * n)]:
            for i in range(count):
                d.rect(
                    60 + i * 360 / count,
                    y,
                    360 / count,
                    44,
                    fill=d.blue if i < filled else d.surface,
                    width=1,
                )
        for i in range(k + 1):
            d.line(
                60 + i * 360 / k,
                140,
                60 + i * 360 / k,
                185,
                dashed=True,
                color=d.muted,
                width=1,
            )
        d.facts["fraction"] = [n, k]
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
