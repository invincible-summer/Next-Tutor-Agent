"""Original music notation, visual art constructions and sports illustrations."""

import colorsys
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
    ball,
    values,
)
from .schema import DiagramError


def parameters(v):
    if v in {"melody_contour", "rhythm_grid", "heart_rate", "landing_distribution"}:
        return {"values": {"type": "list", "required": True}}
    if v == "key_signature":
        return {"sharps": {"type": "integer", "default": 3, "minimum": 0, "maximum": 7}}
    if v == "joint_angles":
        return numeric("angle", 90, 30, 150)
    if v in {"hue_wheel", "complementary", "warm_cool"}:
        return numeric("hue", 25, 0, 359)
    return {}


def _color(d, h, s=0.45, value=0.8):
    if d.monochrome:
        return "#" + f"{round(value*255):02x}" * 3
    rgb = colorsys.hsv_to_rgb((h % 360) / 360, s, value)
    return "#" + "".join(f"{round(c*255):02x}" for c in rgb)


def _staff(d, x=40, y=90, w=400, space=18):
    for i in range(5):
        d.line(x, y + i * space, x + w, y + i * space, width=1)


def _note(d, x, y, duration=4):
    d.ellipse(x, y, 9, 6, fill=d.ink if duration >= 4 else "#fff", width=1.5)
    if duration >= 2:
        d.line(x + 8, y, x + 8, y - 43, width=2)
    if duration >= 8:
        for i in range(1 if duration == 8 else 2):
            d.path(
                f"M {x+8} {y-43+i*10} Q {x+28} {y-25+i*10} {x+15} {y-13+i*10}",
                color=d.ink,
                width=3,
            )


def _clef(d, x, y):
    # Own continuous curve, independent of glyph outlines.
    d.path(
        f"M {x+8} {y+93} C {x-18} {y+105} {x-20} {y+55} {x+7} {y+61} C {x+27} {y+64} {x+23} {y+90} {x+1} {y+84} C {x-39} {y+78} {x-19} {y+42} {x+9} {y+20} C {x+25} {y+5} {x+16} {y-19} {x+8} {y-6} C {x-1} {y+10} {x+10} {y+75} {x+14} {y+106} C {x+20} {y+129} {x-9} {y+126} {x-8} {y+111}",
        width=3,
    )


def _keyboard(d, x, y, w=385, h=120, octaves=2):
    white = 7 * octaves
    unit = w / white
    for i in range(white):
        d.rect(x + i * unit, y, unit, h, fill="#fff", width=1)
    for i in range(white - 1):
        if i % 7 not in {2, 6}:
            d.rect(x + (i + 0.68) * unit, y, unit * 0.64, h * 0.62, fill=d.ink, width=1)
    return unit


def _person(d, points, col=None):
    # Filled head/torso, rounded limbs and visible joints instead of a stick icon.
    head, neck, hip, left_hand, right_hand, left_foot, right_foot = points
    col = col or d.blue
    ball(d, *head, 10, d.gold)
    dx, dy = hip[0] - neck[0], hip[1] - neck[1]
    length = max(1, math.hypot(dx, dy))
    nx, ny = -dy / length * 10, dx / length * 10
    d.poly(
        [
            (neck[0] + nx, neck[1] + ny),
            (neck[0] - nx, neck[1] - ny),
            (hip[0] - nx * 0.8, hip[1] - ny * 0.8),
            (hip[0] + nx * 0.8, hip[1] + ny * 0.8),
        ],
        closed=True,
        fill=col,
        width=1,
    )
    for root, end, is_arm in [
        (neck, left_hand, True),
        (neck, right_hand, True),
        (hip, left_foot, False),
        (hip, right_foot, False),
    ]:
        midpoint = (
            (root[0] + end[0]) / 2 + (8 if is_arm else -7),
            (root[1] + end[1]) / 2,
        )
        d.poly(
            [root, midpoint, end],
            color=col if is_arm else d.muted,
            width=7 if is_arm else 8,
        )
        d.circle(*midpoint, 3, fill=d.gold, width=0.6)
        d.circle(*end, 3, fill=d.gold, width=0.6)


def _court(d, w, h):
    x = (480 - w) / 2
    y = (320 - h) / 2
    d.rect(x, y, w, h, fill=d.surface, width=2)
    d.line(240, y, 240, y + h, width=1.5)
    return x, y


def draw(v, p, mono=False):
    d = canvas(mono)
    if v in {
        "staff",
        "clef_position",
        "note_duration",
        "rest_duration",
        "time_signature",
        "beat_groups",
        "key_signature",
    }:
        _staff(d, y=130)
        if v == "staff":
            for i, y in enumerate(range(130, 203, 18)):
                text(d, str(5 - i), 22, y + 4, size=11)
            text(d, "five lines · four spaces", 240, 275)
        elif v == "clef_position":
            _clef(d, 85, 120)
            d.path(
                "M 269 175 C 252 171 253 143 271 139 C 302 137 291 181 263 195", width=3
            )
            for y in [151, 169]:
                d.circle(301, y, 3, fill=d.ink, width=1)
            text(d, "treble (G)", 112, 276)
            text(d, "bass (F)", 302, 276)
        elif v == "note_duration":
            for i, (duration, label) in enumerate(
                [
                    (1, "whole"),
                    (2, "half"),
                    (4, "quarter"),
                    (8, "eighth"),
                    (16, "sixteenth"),
                ]
            ):
                _note(d, 75 + i * 83, 184, duration)
                text(d, label, 75 + i * 83, 274, size=12)
        elif v == "rest_duration":
            d.rect(59, 148, 24, 9, fill=d.ink)
            d.rect(143, 157, 24, 9, fill=d.ink)
            d.path("M 238 132 L 226 149 L 241 166 L 229 182 Q 239 183 235 196", width=4)
            for x, flags in [(325, 1), (410, 2)]:
                d.line(x + 7, 142, x - 9, 192, width=2)
                for i in range(flags):
                    d.circle(x - 2 - i * 5, 150 + i * 17, 4, fill=d.ink)
                    d.path(
                        f"M {x-2-i*5} {150+i*17} Q {x+4} {160+i*17} {x+6-i*5} {148+i*17}",
                        width=2,
                    )
            for x, label in zip(
                [71, 155, 237, 325, 410],
                ["whole", "half", "quarter", "eighth", "sixteenth"],
            ):
                text(d, label, x, 274, size=12)
        elif v == "time_signature":
            _clef(d, 72, 120)
            for x, a, b in [(159, "2", "4"), (269, "3", "4"), (379, "4", "4")]:
                text(d, a, x, 159, size=26)
                text(d, b, x, 193, size=26)
            text(d, "beats / note unit", 240, 278)
        elif v == "beat_groups":
            for i in range(6):
                _note(d, 92 + i * 55, 184, 4)
            d.line(100, 141, 210, 141, width=5)
            d.line(265, 141, 375, 141, width=5)
            text(d, "compound grouping: 3 + 3", 240, 278)
        else:
            _clef(d, 75, 120)
            ys = [130, 157, 121, 148, 175, 139, 166]
            for i in range(p["sharps"]):
                x = 145 + i * 31
                y = ys[i]
                d.line(x - 4, y - 13, x - 7, y + 13)
                d.line(x + 5, y - 13, x + 2, y + 13)
                d.line(x - 12, y - 5, x + 12, y - 8, width=3)
                d.line(x - 12, y + 6, x + 12, y + 3, width=3)
            text(d, "sharp order: F C G D A E B", 240, 278)
            d.facts["sharps"] = p["sharps"]
    elif v in {"scale_keyboard", "piano_keyboard"}:
        unit = _keyboard(d, 40, 95, 400, 139, 2)
        if v == "scale_keyboard":
            for i, label in enumerate(["C", "D", "E", "F", "G", "A", "B", "C"]):
                text(
                    d,
                    label,
                    40 + (i + 0.5) * unit,
                    220,
                    size=12,
                    color=d.red if i in {0, 7} else d.ink,
                )
            text(d, "major scale: W W H W W W H", 240, 293)
        else:
            text(d, "seven white / five black keys per octave", 240, 293, size=13)
    elif v in {"intervals", "triads"}:
        _staff(d, y=108)
        if v == "intervals":
            for x, steps, label in [
                (104, 2, "third"),
                (240, 3, "fourth"),
                (376, 4, "fifth"),
            ]:
                _note(d, x, 180, 4)
                _note(d, x, 180 - steps * 9, 4)
                text(d, label, x, 274)
        else:
            for x, notes, label in [
                (128, [0, 2, 4], "root position"),
                (353, [2, 4, 7], "first inversion"),
            ]:
                for step in notes:
                    _note(d, x, 180 - step * 9, 1)
                text(d, label, x, 274, size=12)
    elif v in {"melody_contour", "rhythm_grid"}:
        nums = values(p, minimum=3, maximum=16)
        if v == "melody_contour":
            if any(n != int(n) or not -4 <= n <= 12 for n in nums):
                raise DiagramError("diagram_invalid_pitches")
            _staff(d, y=105)
            pts = []
            for i, n in enumerate(nums):
                x = 55 + i * 370 / (len(nums) - 1)
                y = 180 - n * 9
                _note(d, x, y, 4)
                pts.append((x, y))
            d.poly(pts, color=d.blue, width=1)
            d.facts["diatonic_steps"] = nums
        else:
            if any(n not in {0, 1} for n in nums):
                raise DiagramError("diagram_invalid_rhythm")
            for i, n in enumerate(nums):
                w = 400 / len(nums)
                d.rect(
                    40 + i * w, 113, w, 74, fill=d.glass if n else d.surface, width=1
                )
                text(d, "●" if n else "–", 40 + (i + 0.5) * w, 157, size=19)
                text(d, str(i + 1), 40 + (i + 0.5) * w, 217, size=10)
            d.facts["onsets"] = nums
        text(d, "provided sequence", 240, 291, size=12)
    elif v == "guitar_fretboard":
        d.rect(40, 78, 400, 163, fill=d.gold)
        for i, label in enumerate(["E", "A", "D", "G", "B", "e"]):
            y = 94 + i * 26
            d.line(40, y, 440, y, color=d.ink, width=2 - i * 0.2)
            text(d, label, 22, y + 4, size=12)
        for i in range(9):
            x = 40 + 400 * (1 - 2 ** (-i / 12)) / (1 - 2 ** (-8 / 12))
            d.line(x, 78, x, 241, color=d.muted, width=3 if not i else 1.5)
        for i in [3, 5, 7]:
            x = 40 + 400 * (1 - 2 ** (-(i - 0.5) / 12)) / (1 - 2 ** (-8 / 12))
            d.circle(x, 159, 4, fill=d.surface, width=1)
        text(d, "equal temperament fret spacing", 240, 289)
    elif v == "string_instrument":
        d.path(
            "M 217 103 Q 158 88 170 142 Q 191 169 154 185 Q 116 257 230 276 Q 349 257 309 185 Q 271 169 291 142 Q 303 88 247 103 Z",
            fill=d.gold,
        )
        d.rect(221, 37, 22, 174, fill=d.muted)
        d.rect(211, 17, 42, 29, fill=d.gold, radius=4)
        d.circle(232, 203, 24, fill=d.surface)
        for x in [226, 232, 238]:
            d.line(x, 30, x, 251, color=d.ink, width=0.7)
        d.rect(212, 246, 40, 8, fill=d.muted)
        text(d, "string + resonant body", 240, 311)
    elif v == "wind_column":
        d.rect(105, 78, 270, 160, fill=d.glass)
        d.line(105, 78, 105, 238, width=5)
        d.poly(
            [
                (105 + 270 * t, 158 + 60 * math.sin(math.pi * t / 2))
                for t in [i / 70 for i in range(71)]
            ],
            color=d.blue,
        )
        d.poly(
            [
                (105 + 270 * t, 158 - 60 * math.sin(math.pi * t / 2))
                for t in [i / 70 for i in range(71)]
            ],
            color=d.gold,
        )
        text(d, "closed", 105, 272)
        text(d, "open", 375, 272)
        text(d, "fundamental: λ = 4L", 240, 42)
    elif v == "drum_membrane":
        d.path("M 133 146 L 139 240 Q 240 279 341 240 L 347 146", fill=d.gold)
        d.ellipse(240, 146, 107, 40, fill=d.surface)
        for r in [30, 60, 90]:
            d.ellipse(240, 146, r, r * 0.35, color=d.blue, width=1)
        for x in [145, 180, 230, 280, 330]:
            d.line(x, 173, x, 249, color=d.muted, width=2)
        d.line(115, 65, 218, 135, width=6)
        d.circle(218, 135, 5, fill=d.gold)
        text(d, "membrane modes schematic", 240, 307, size=12)
    elif v == "instrument_families":
        for x, label in [(83, "strings"), (240, "winds"), (397, "percussion")]:
            node(d, label, x, 75, w=126, h=40)
        d.path(
            "M 58 188 Q 70 150 100 188 Q 118 236 79 241 Q 40 236 58 188", fill=d.gold
        )
        d.rect(70, 116, 15, 102, fill=d.muted)
        d.line(219, 140, 255, 230, color=d.gold, width=8)
        for y in [169, 186, 204]:
            d.circle(231 + (y - 169) / 4, y, 3, fill=d.ink, width=0.5)
        d.rect(366, 178, 62, 53, fill=d.gold)
        d.ellipse(397, 178, 31, 10, fill=d.surface)
    elif v == "musical_form":
        for i, label in enumerate(["A", "B", "A", "C", "A"]):
            node(
                d,
                label,
                70 + i * 85,
                158,
                w=70,
                h=87,
                fill=d.blue if label == "A" else d.gold if label == "B" else d.green,
            )
        text(d, "rondo: A B A C A", 240, 281)
    elif v == "textures":
        for row, (label, lines) in enumerate(
            [("monophony", 1), ("homophony", 3), ("polyphony", 3)]
        ):
            y = 60 + row * 93
            text(d, label, 84, y + 18, size=12)
            for j in range(lines):
                pts = [
                    (
                        170 + i * 35,
                        (
                            y + j * 14 + (i % 2) * 7 + (i * j % 3) * 4
                            if row == 2
                            else y + j * 14 + (i % 2) * 7
                        ),
                    )
                    for i in range(8)
                ]
                d.poly(pts, color=[d.blue, d.red, d.green][j], width=2)
    elif v == "hue_wheel":
        for i in range(12):
            a = i * math.pi / 6
            b = (i + 1) * math.pi / 6
            r = 110
            d.path(
                f"M 240 160 L {240+r*math.cos(a)} {160+r*math.sin(a)} A {r} {r} 0 0 1 {240+r*math.cos(b)} {160+r*math.sin(b)} Z",
                fill=_color(d, p["hue"] + i * 30),
                color="#fff",
                width=1,
            )
        d.circle(240, 160, 48, fill="#fff", color="#fff")
        text(d, "12 hues", 240, 166)
        d.facts["hue_offset"] = p["hue"]
    elif v in {"warm_cool", "complementary", "value_steps", "chroma_steps"}:
        if v in {"warm_cool", "complementary"}:
            for x, h, label in [
                (70, p["hue"], "warm / hue"),
                (270, p["hue"] + 180, "cool / complement"),
            ]:
                d.rect(x, 83, 140, 145, fill=_color(d, h), width=1)
                text(d, label, x + 70, 270, size=12)
        else:
            for i in range(7):
                c = _color(
                    d,
                    210,
                    i / 8 if v == "chroma_steps" else 0,
                    0.8 if v == "chroma_steps" else 0.15 + i * 0.12,
                )
                d.rect(38 + i * 58, 100, 58, 115, fill=c, width=0.7)
                text(d, str(i + 1), 67 + i * 58, 251, size=12)
    elif v in {"additive", "subtractive"}:
        centers = [(185, 126), (295, 126), (240, 214)]
        colors = (
            ["#ef6868", "#66c778", "#6589ef"]
            if v == "additive"
            else ["#60cede", "#df73bc", "#edd467"]
        )
        if mono:
            colors = [d.red, d.green, d.blue]
        circles = [
            [
                (
                    x + 76 * math.cos(i * math.tau / 60),
                    y + 76 * math.sin(i * math.tau / 60),
                )
                for i in range(60)
            ]
            for x, y in centers
        ]

        def intersect(subject, clip):
            for c, e in zip(clip, clip[1:] + clip[:1]):
                output = []

                def side(q):
                    return (e[0] - c[0]) * (q[1] - c[1]) - (e[1] - c[1]) * (q[0] - c[0])

                for start, end in zip(subject, subject[1:] + subject[:1]):
                    sa, sb = side(start), side(end)
                    if (sa >= 0) != (sb >= 0):
                        t = sa / (sa - sb)
                        output.append(
                            (
                                start[0] + t * (end[0] - start[0]),
                                start[1] + t * (end[1] - start[1]),
                            )
                        )
                    if sb >= 0:
                        output.append(end)
                subject = output
                if not subject:
                    break
            return subject

        for (x, y), color in zip(centers, colors):
            d.circle(x, y, 76, fill=color, color=color, width=0.5)
        mixtures = (
            ["#ead975", "#de81d8", "#77d5d7"]
            if v == "additive"
            else ["#697acf", "#72af75", "#d8806e"]
        )
        for (i, j), color in zip([(0, 1), (0, 2), (1, 2)], mixtures):
            color = d.muted if mono else color
            d.poly(
                intersect(circles[i], circles[j]),
                closed=True,
                fill=color,
                color=color,
                width=0.5,
            )
        center_color = "#fff" if v == "additive" else d.ink
        d.poly(
            intersect(intersect(circles[0], circles[1]), circles[2]),
            closed=True,
            fill=center_color,
            color=center_color,
            width=0.5,
        )
        for label, (x, y) in zip(
            "RGB" if v == "additive" else "CMY", [(159, 112), (321, 112), (240, 255)]
        ):
            text(d, label, x, y, size=17)
        text(
            d, "R + G + B → white" if v == "additive" else "C + M + Y → dark", 240, 310
        )
        d.facts["schematic_mixing"] = True
    elif v in {"one_point", "two_point", "three_point"}:

        def along(a, b, t):
            return tuple(a[i] + t * (b[i] - a[i]) for i in range(2))

        def crossing(a, b, c, e):
            u = (b[0] - a[0], b[1] - a[1])
            w = (e[0] - c[0], e[1] - c[1])
            t = ((c[0] - a[0]) * w[1] - (c[1] - a[1]) * w[0]) / (
                u[0] * w[1] - u[1] * w[0]
            )
            return along(a, b, t)

        if v == "one_point":
            vp = (340, 65)
            front = [(110, 140), (260, 140), (260, 265), (110, 265)]
            rear = [along(q, vp, 0.4) for q in front]
            for q in front:
                d.line(*q, *vp, color=d.muted, width=0.6)
            d.poly([front[0], front[1], rear[1], rear[0]], closed=True, fill=d.surface)
            d.poly([front[1], front[2], rear[2], rear[1]], closed=True, fill=d.glass)
            d.poly(front, closed=True, fill="#fff")
            vanish = [vp]
            horizon = 65
        else:
            third = v == "three_point"
            horizon = 240 if third else 85
            left, right = (30, horizon), (450, horizon)
            top, bottom = (240, 105 if third else 145), (240, 225 if third else 275)
            vertical = (240, 25)
            lt, rt = along(top, left, 0.5), along(top, right, 0.5)
            lb = (
                crossing(lt, vertical, bottom, left)
                if third
                else along(bottom, left, 0.5)
            )
            rb = (
                crossing(rt, vertical, bottom, right)
                if third
                else along(bottom, right, 0.5)
            )
            for vp, side in [(left, [top, bottom]), (right, [top, bottom])]:
                for q in side:
                    d.line(*q, *vp, color=d.muted, width=0.6)
            if third:
                for q in [lb, rb, bottom]:
                    d.line(*q, *vertical, color=d.muted, width=0.6)
            d.poly([top, lt, lb, bottom], closed=True, fill=d.surface)
            d.poly([top, rt, rb, bottom], closed=True, fill=d.glass)
            vanish = [left, right] + ([vertical] if third else [])
        d.line(25, horizon, 455, horizon, color=d.muted, dashed=True, width=0.7)
        for point in vanish:
            d.circle(*point, 4, fill=d.red)
        text(d, f"{len(vanish)} vanishing point(s)", 240, 308, size=12)
    elif v == "still_life":
        d.line(40, 257, 442, 257, color=d.muted)
        d.path(
            "M 112 236 L 112 117 Q 107 91 131 91 L 152 91 Q 177 92 171 117 L 171 236 Z",
            fill=d.glass,
        )
        d.ellipse(141, 237, 31, 10, fill=d.glass)
        ball(d, 239, 213, 43, d.gold)
        d.poly(
            [(309, 153), (375, 137), (416, 209), (346, 229)],
            closed=True,
            fill=d.surface,
        )
        d.poly(
            [(346, 229), (416, 209), (410, 246), (342, 267)], closed=True, fill=d.muted
        )
        text(d, "form · overlap · proportion", 240, 305)
    elif v == "cast_shadow":
        d.ellipse(295, 255, 125, 20, fill=d.muted, color=d.muted)
        ball(d, 216, 186, 66, d.glass)
        ball(d, 66, 52, 19, d.gold)
        d.line(88, 70, 335, 259, color=d.gold, dashed=True)
        d.line(83, 78, 241, 258, color=d.gold, dashed=True)
        text(d, "light → form → cast shadow", 240, 307)
    elif v == "thirds":
        d.rect(45, 55, 390, 225, fill=d.surface)
        for x in [175, 305]:
            d.line(x, 55, x, 280, color=d.blue, width=1)
        for y in [130, 205]:
            d.line(45, y, 435, y, color=d.blue, width=1)
        ball(d, 305, 130, 27, d.gold)
        d.poly(
            [(45, 247), (181, 164), (266, 251), (367, 185), (435, 246)],
            color=d.green,
            width=3,
        )
    elif v in {"visual_balance", "symmetry_balance"}:
        d.line(240, 45, 240, 275, dashed=True, color=d.muted)
        if v == "visual_balance":
            d.circle(133, 170, 65, fill=d.blue)
            d.circle(368, 155, 36, fill=d.gold)
            d.circle(352, 230, 20, fill=d.red)
        else:
            for x in [145, 335]:
                d.circle(x, 133, 44, fill=d.blue)
                d.rect(x - 35, 205, 70, 50, fill=d.gold)
        text(
            d,
            "asymmetrical balance" if v == "visual_balance" else "symmetrical balance",
            240,
            310,
        )
    elif v == "negative_space":
        d.rect(55, 40, 370, 245, fill=d.blue)
        d.path(
            "M 210 58 L 270 58 Q 259 104 280 137 Q 332 150 289 178 L 267 209 L 267 254 L 291 269 L 189 269 L 213 254 L 213 209 L 191 178 Q 148 150 200 137 Q 221 104 210 58 Z",
            fill="#fff",
            color="#fff",
            width=1,
        )
        text(d, "figure / ground", 240, 309)
    elif v in {"repeating_pattern", "tessellation"}:
        if v == "repeating_pattern":
            for row in range(4):
                for col in range(7):
                    x = 48 + col * 62
                    y = 52 + row * 62
                    d.path(
                        f"M {x} {y+22} Q {x+22} {y-5} {x+44} {y+22} Q {x+22} {y+49} {x} {y+22} Z",
                        fill=d.glass if (row + col) % 2 else d.gold,
                        width=1,
                    )
        else:
            for row in range(3):
                for col in range(6):
                    x = 65 + col * 59.25
                    y = (
                        70
                        + row * math.sqrt(3) * 39.5
                        + (col % 2) * math.sqrt(3) * 39.5 / 2
                    )
                    pts = [
                        (
                            x + 39.5 * math.cos(i * math.pi / 3),
                            y + 39.5 * math.sin(i * math.pi / 3),
                        )
                        for i in range(6)
                    ]
                    d.poly(
                        pts,
                        closed=True,
                        fill=[d.glass, d.gold, d.green][(row + col) % 3],
                        width=1,
                    )
    elif v == "paper_fold":
        for x in [110, 360]:
            d.rect(x - 65, 80, 130, 130, fill=d.surface)
            d.line(x - 65, 80, x + 65, 210, dashed=True, color=d.blue)
        d.poly([(295, 80), (425, 210), (295, 210)], closed=True, fill=d.glass)
        connect(d, (205, 145), (270, 145))
        text(d, "diagonal fold", 240, 283)
    elif v == "layout_hierarchy":
        d.rect(107, 30, 266, 267, fill=d.surface)
        d.rect(126, 50, 228, 42, fill=d.blue)
        d.rect(126, 108, 97, 102, fill=d.gold)
        for y in range(111, 208, 16):
            d.line(238, y, 352, y, color=d.muted, width=4)
        for y in [231, 248, 265]:
            d.line(126, y, 352, y, color=d.muted, width=3)
    elif v == "track":
        for i in range(8):
            r = 124 - i * 8
            y = 160 - r
            d.path(
                f"M 156 {y} L 324 {y} A {r} {r} 0 0 1 324 {160+r} L 156 {160+r} A {r} {r} 0 0 1 156 {y} Z",
                fill=d.red if i == 0 else "none",
                color="#fff" if i else d.ink,
                width=1,
            )
        d.path(
            "M 156 101 L 324 101 A 59 59 0 0 1 324 219 L 156 219 A 59 59 0 0 1 156 101 Z",
            fill=d.green,
        )
        d.line(237, 222, 237, 282, color="#fff", width=2)
    elif v == "relay_zone":
        for y in [95, 145, 195, 245]:
            d.line(35, y, 445, y, width=1)
        d.rect(173, 95, 135, 150, fill=d.glass, width=1)
        d.line(173, 80, 173, 260, color=d.blue, width=2)
        d.line(308, 80, 308, 260, color=d.blue, width=2)
        _person(
            d,
            [
                (126, 120),
                (135, 137),
                (152, 178),
                (101, 150),
                (220, 155),
                (139, 224),
                (192, 225),
            ],
        )
        _person(
            d,
            [
                (310, 113),
                (321, 128),
                (344, 171),
                (228, 152),
                (364, 149),
                (310, 222),
                (389, 218),
            ],
            d.green,
        )
        d.rect(215, 148, 19, 5, fill=d.gold, width=1)
        text(d, "exchange zone schematic", 240, 305)
    elif v in {
        "sprint_start",
        "running_gait",
        "long_jump",
        "vertical_jump",
        "throwing",
        "gymnastics_balance",
        "stretch_posture",
        "swimming",
    }:
        if v == "sprint_start":
            pose = [
                (284, 137),
                (258, 146),
                (190, 145),
                (310, 235),
                (333, 224),
                (117, 224),
                (203, 250),
            ]
            d.rect(119, 237, 36, 12, fill=d.muted)
            d.rect(183, 262, 36, 10, fill=d.muted)
        elif v == "running_gait":
            pose = [
                (226, 60),
                (233, 79),
                (252, 151),
                (179, 98),
                (297, 125),
                (185, 257),
                (345, 214),
            ]
        elif v == "long_jump":
            pose = [
                (248, 67),
                (245, 88),
                (266, 157),
                (164, 112),
                (312, 58),
                (359, 210),
                (385, 182),
            ]
            d.path("M 38 228 Q 249 -3 432 242", color=d.gold, dashed=True)
        elif v == "vertical_jump":
            pose = [
                (242, 88),
                (241, 106),
                (243, 171),
                (200, 44),
                (277, 43),
                (227, 248),
                (267, 246),
            ]
            d.arrow(350, 237, 350, 95, color=d.blue, width=3)
        elif v == "throwing":
            pose = [
                (230, 82),
                (235, 101),
                (224, 172),
                (300, 59),
                (158, 133),
                (160, 271),
                (293, 261),
            ]
            ball(d, 310, 50, 10, d.gold)
            d.path("M 321 40 Q 387 29 434 88", color=d.gold, dashed=True)
        elif v == "gymnastics_balance":
            pose = [
                (242, 63),
                (242, 82),
                (242, 157),
                (146, 98),
                (339, 99),
                (231, 267),
                (337, 192),
            ]
            d.rect(70, 274, 350, 12, fill=d.gold)
        elif v == "stretch_posture":
            pose = [
                (226, 102),
                (230, 121),
                (261, 185),
                (158, 174),
                (171, 194),
                (119, 257),
                (372, 263),
            ]
        else:
            pose = [
                (300, 146),
                (276, 150),
                (193, 158),
                (384, 122),
                (298, 220),
                (93, 132),
                (103, 192),
            ]
            d.rect(30, 201, 420, 85, fill=d.glass)
            d.line(30, 201, 450, 201, color=d.blue, width=2)
        _person(d, pose)
        if v != "swimming":
            d.line(40, 286, 445, 286, color=d.muted)
        d.facts["posture"] = "educational_schematic_not_personal_exercise_advice"
    elif v in {
        "basketball_court",
        "football_pitch",
        "volleyball_court",
        "badminton_court",
        "tennis_court",
    }:
        w, h = (
            (390, 209)
            if v == "basketball_court"
            else (
                (390, 252)
                if v == "football_pitch"
                else (
                    (400, 200)
                    if v == "volleyball_court"
                    else (400, 182) if v == "badminton_court" else (380, 175)
                )
            )
        )
        x, y = _court(d, w, h)
        if v in {"basketball_court", "football_pitch"}:
            d.circle(240, 160, 25 if v == "basketball_court" else 34, width=1.5)
            for side in [-1, 1]:
                edge = x if side < 0 else x + w
                depth = 81 if v == "basketball_court" else 61
                hh = 50 if v == "basketball_court" else 150
                d.rect(
                    edge if side < 0 else edge - depth,
                    160 - hh / 2,
                    depth,
                    hh,
                    width=1.5,
                )
                if v == "football_pitch":
                    d.rect(edge if side < 0 else edge - 20, 126, 20, 68, width=1)
                    d.rect(edge - 7 if side < 0 else edge, 146, 7, 28, width=1)
                else:
                    d.circle(edge + side * -81, 160, 25, width=1)
                    d.circle(edge + side * -12, 160, 4, width=1)
                    d.line(edge - side * 7, 148, edge - side * 7, 172, width=2)
                    hoop = edge - side * 12
                    d.poly(
                        [
                            (hoop - side * 83 * math.cos(t), 160 + 83 * math.sin(t))
                            for t in [
                                -math.pi / 2 + i * math.pi / 50 for i in range(51)
                            ]
                        ],
                        width=1,
                    )
                    for yy in [77, 243]:
                        d.line(edge, yy, hoop, yy, width=1)
        elif v == "volleyball_court":
            for xx in [240 - w / 6, 240 + w / 6]:
                d.line(xx, y, xx, y + h, width=1.5)
        elif v == "badminton_court":
            for xx in [240 - 59, 240 + 59, x + 23, x + w - 23]:
                d.line(xx, y, xx, y + h, width=1)
            for yy in [y + 14, y + h - 14]:
                d.line(x, yy, x + w, yy, width=1)
            d.line(x + 23, 160, 181, 160, width=1)
            d.line(299, 160, x + w - 23, 160, width=1)
        else:
            for yy in [y + 22, y + h - 22]:
                d.line(x, yy, x + w, yy, width=1)
            for xx in [138, 342]:
                d.line(xx, y + 22, xx, y + h - 22, width=1)
            d.line(138, 160, 342, 160, width=1)
        d.facts["court"] = "schematic_standard_line_relationships"
    elif v == "table_tennis":
        d.poly([(70, 144), (306, 88), (420, 180), (184, 246)], closed=True, fill=d.blue)
        d.line(184, 246, 184, 278, width=5)
        d.line(306, 88, 306, 251, width=5)
        d.line(75, 145, 75, 237, width=5)
        d.line(420, 180, 420, 272, width=5)
        d.poly(
            [(168, 114), (283, 207), (283, 182), (168, 89)], closed=True, fill=d.glass
        )
        d.line(184, 151, 312, 159, color="#fff", width=1)
        d.facts["perspective"] = True
    elif v == "joint_angles":
        theta = math.radians(p["angle"])
        center = (225, 180)
        a = (365, 180)
        b = (225 + 140 * math.cos(theta), 180 - 140 * math.sin(theta))
        d.line(*a, *center, color=d.muted, width=8)
        d.line(*center, *b, color=d.blue, width=8)
        d.circle(*center, 14, fill=d.gold)
        d.poly(
            [
                (225 + 50 * math.cos(t), 180 - 50 * math.sin(t))
                for t in [i * theta / 30 for i in range(31)]
            ],
            color=d.red,
        )
        text(d, f"{p['angle']:g}°", 320, 255)
        d.facts["joint_angle"] = p["angle"]
    elif v == "heart_rate":
        nums = values(p, minimum=3, maximum=30)
        if any(not 30 <= n <= 220 for n in nums):
            raise DiagramError("diagram_invalid_heart_rate")
        xy = axes(d, x_label="time", y_label="beats/min")
        d.poly(
            [
                xy(i / (len(nums) - 1), 0.05 + (n - 30) / 190 * 0.9)
                for i, n in enumerate(nums)
            ],
            color=d.red,
            width=3,
        )
        text(d, "30", 30, 265, size=11)
        text(d, "220", 30, 68, size=11)
        d.facts["readings"] = nums
    elif v == "training_stations":
        points = [(98, 70), (380, 70), (380, 250), (98, 250)]
        for i, (x, y) in enumerate(points):
            d.circle(x, y, 38, fill=[d.glass, d.gold, d.green, d.surface][i])
            text(d, str(i + 1), x, y + 7, size=22)
        for a, b in zip(points, points[1:] + points[:1]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            d.arrow(
                a[0] + dx / length * 42,
                a[1] + dy / length * 42,
                b[0] - dx / length * 42,
                b[1] - dy / length * 42,
                color=d.blue,
            )
        text(d, "rotate between stations", 240, 163, size=12)
    elif v == "landing_distribution":
        nums = values(p, minimum=8, maximum=8)
        if any(n < 0 for n in nums) or max(nums) <= 0:
            raise DiagramError("diagram_invalid_pressure")
        for foot, cx in enumerate([165, 320]):
            d.path(
                f"M {cx-26} 265 Q {cx-55} 219 {cx-34} 168 Q {cx-39} 107 {cx-14} 69 Q {cx+25} 54 {cx+38} 92 Q {cx+50} 136 {cx+25} 180 Q {cx+27} 220 {cx+17} 270 Z",
                fill=d.surface,
            )
            for i, (dx, y) in enumerate([(-8, 104), (0, 151), (-16, 197), (0, 243)]):
                n = nums[foot * 4 + i]
                d.circle(
                    cx + dx,
                    y,
                    9 + 13 * n / max(nums),
                    fill=d.red if n / max(nums) > 0.6 else d.blue,
                    width=1,
                )
        text(d, "provided relative pressure · schematic", 240, 309, size=12)
        d.facts["relative_pressures"] = nums
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
