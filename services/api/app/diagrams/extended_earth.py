"""Original landforms, agricultural systems and environmental process scenes."""

import math

from .extended_common import (
    canvas,
    text,
    ball,
    vessel,
    leaf,
    plant,
    axes,
    curve,
    panel,
    node,
    connect,
    numeric,
    box,
)
from .schema import DiagramError


def parameters(v):
    if v == "climate_plot":
        return {
            "temperature": {"type": "list", "required": True},
            "rainfall": {"type": "list", "required": True},
        }
    if v == "travel_time":
        return {"points": {"type": "list", "required": True}}
    if v == "time_zones":
        return numeric("utc_hour", 12, 0, 23, True)
    if v == "solar_altitude":
        return numeric("latitude", 30, -60, 60) | numeric(
            "declination", 0, -23.44, 23.44
        )
    if v == "soil_triangle":
        return numeric("sand", 40, 0, 100) | numeric("clay", 30, 0, 100)
    if v == "plant_spacing":
        return numeric("rows", 3, 2, 5, True) | numeric("columns", 5, 2, 7, True)
    return {}


def _cloud(d, x, y, s=1):
    d.path(
        f"M {x-38*s} {y+10*s} C {x-58*s} {y-12*s} {x-20*s} {y-28*s} {x-11*s} {y-13*s} C {x+2*s} {y-46*s} {x+39*s} {y-29*s} {x+33*s} {y-8*s} C {x+62*s} {y-11*s} {x+56*s} {y+18*s} {x+32*s} {y+16*s} L {x-24*s} {y+16*s} Z",
        fill=d.surface,
        color=d.muted,
        width=1.5,
    )


def _hill(d, points, fill=None):
    d.poly(
        points + [(points[-1][0], 280), (points[0][0], 280)],
        closed=True,
        fill=fill or d.gold,
    )


def _house(d, x, y, w=45, h=40):
    d.rect(x, y, w, h, fill=d.surface)
    d.poly([(x - 5, y), (x + w / 2, y - 22), (x + w + 5, y)], closed=True, fill=d.red)
    d.rect(x + w * 0.4, y + h * 0.45, w * 0.2, h * 0.55, fill=d.muted, width=1)
    d.rect(x + w * 0.08, y + h * 0.2, w * 0.2, h * 0.25, fill=d.glass, width=1)


def _roots(d, x, y, h=60):
    for dx in [-25, -10, 7, 23]:
        d.path(f"M {x} {y} Q {x+dx} {y+h*.4} {x+dx*.6} {y+h}", color=d.gold, width=1.5)
        d.line(
            x + dx * 0.6, y + h * 0.5, x + dx * 1.5, y + h * 0.7, color=d.gold, width=1
        )


def _flow_loop(d, labels, positions, colors=None):
    for i, ((x, y), label) in enumerate(zip(positions, labels)):
        node(d, label, x, y, w=100, h=42, fill=(colors or [d.glass] * len(labels))[i])
    for i in range(len(labels)):
        a, b = positions[i], positions[(i + 1) % len(labels)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        connect(
            d,
            (a[0] + dx / length * 55, a[1] + dy / length * 26),
            (b[0] - dx / length * 55, b[1] - dy / length * 26),
            color=d.blue,
        )


def draw(v, p, mono=False):
    d = canvas(mono)
    if v == "meander":
        d.rect(25, 35, 430, 250, fill=d.surface)
        river = "M 50 45 C 235 45 96 139 215 144 C 430 116 189 267 419 270"
        d.path(river, color=d.blue, width=8)
        d.path(
            "M 160 95 Q 141 141 207 154 M 283 210 Q 260 251 352 266",
            color=d.gold,
            width=6,
        )
        d.arrow(80, 47, 121, 52, color=d.ink)
        d.arrow(190, 143, 235, 146, color=d.ink)
        d.arrow(357, 265, 397, 269, color=d.ink)
        text(d, "erosion / deposition", 240, 309, size=12)
    elif v in {"delta", "alluvial_fan"}:
        if v == "delta":
            d.rect(25, 45, 430, 240, fill=d.glass)
            d.poly(
                [(25, 45), (210, 45), (360, 200), (190, 285), (25, 285)],
                closed=True,
                fill=d.gold,
            )
            for end in [(350, 186), (283, 235), (205, 276)]:
                d.poly([(55, 78), (143, 125), (178, 165), end], color=d.blue, width=5)
            text(d, "river", 84, 61)
            text(d, "sea", 400, 250)
        else:
            _hill(d, [(30, 100), (140, 43), (235, 170), (355, 43), (450, 108)])
            d.poly([(235, 150), (95, 265), (390, 265)], closed=True, fill=d.surface)
            for end in [(105, 257), (176, 265), (247, 268), (318, 265), (381, 256)]:
                d.path(
                    f"M 235 65 Q 217 156 235 158 L {end[0]} {end[1]}",
                    color=d.blue,
                    width=2,
                )
            text(d, "mountain front", 236, 305)
    elif v == "karst":
        d.rect(30, 170, 420, 110, fill=d.gold)
        d.path(
            "M 30 170 L 65 170 Q 77 45 99 52 Q 125 81 129 170 L 213 170 Q 233 32 254 49 Q 282 93 284 170 L 345 170 Q 366 88 390 98 L 424 170 L 450 170",
            fill=d.green,
        )
        d.path(
            "M 54 246 C 152 211 161 266 230 230 Q 331 192 423 238",
            color=d.blue,
            width=7,
        )
        d.path("M 203 170 Q 204 218 234 224", color=d.blue, width=3)
        for x in [110, 145, 283, 310]:
            d.poly(
                [(x, 219), (x + 6, 237), (x + 12, 219)],
                closed=True,
                fill=d.surface,
                width=1,
            )
        text(d, "limestone dissolution", 240, 305)
    elif v == "glacier":
        _hill(d, [(25, 150), (135, 42), (240, 95), (356, 40), (455, 157)])
        d.path(
            "M 150 80 Q 245 76 337 73 L 317 161 Q 304 258 185 271 Q 162 177 150 80 Z",
            fill=d.glass,
            color=d.blue,
        )
        for y in [135, 175, 217]:
            d.path(f"M 183 {y} Q 236 {y+20} 313 {y-4}", color=d.blue, width=1)
        d.arrow(245, 109, 239, 227, color=d.blue, width=3)
        for x, y in [(184, 257), (223, 275), (271, 262)]:
            d.poly(
                [(x - 7, y), (x, y - 10), (x + 10, y + 3)],
                closed=True,
                fill=d.muted,
                width=1,
            )
    elif v == "isobar_wind":
        for r in [40, 73, 110]:
            d.ellipse(240, 159, r * 1.6, r, color=d.blue, width=1.5)
        text(d, "L", 240, 165, size=28)
        for angle in [0, math.pi / 2, math.pi, 3 * math.pi / 2]:
            a = (240 + 145 * math.cos(angle), 159 + 87 * math.sin(angle))
            b = (240 + 130 * math.cos(angle - 0.5), 159 + 78 * math.sin(angle - 0.5))
            d.arrow(*a, *b, color=d.gold, width=3)
        text(d, "Northern hemisphere · low pressure", 240, 305, size=12)
    elif v == "pressure_belts":
        latitudes = [90, 60, 30, 0, -30, -60, -90]
        for i, lat in enumerate(latitudes):
            y = 35 + i * 40
            d.rect(100, y, 300, 24, fill=d.blue if i % 2 else d.red, width=1)
            text(d, f"{lat}°", 65, y + 17, size=12)
            text(d, "L" if i % 2 else "H", 250, y + 17)
        for i in range(6):
            d.arrow(
                335 if i % 2 else 170,
                66 + i * 40,
                200 if i % 2 else 310,
                72 + i * 40,
                color=d.gold,
            )
        d.facts["idealized_pressure_belts"] = (
            "polar_high_subpolar_low_subtropical_high_equatorial_low"
        )
    elif v == "monsoon":
        for x, summer in [(25, True), (260, False)]:
            d.rect(x, 55, 195, 220, fill=d.glass)
            d.poly(
                [(x, 55), (x + 124, 55), (x + 107, 275), (x, 275)],
                closed=True,
                fill=d.gold,
            )
            a, b = (x + 175, 210), (x + 52, 120)
            d.arrow(*(a if summer else b), *(b if summer else a), color=d.blue, width=5)
            text(d, "L" if summer else "H", x + 47, 82, size=20)
            text(d, "H" if summer else "L", x + 172, 256, size=20)
            text(d, "summer" if summer else "winter", x + 96, 305)
    elif v == "front_section":
        d.rect(35, 160, 410, 115, fill=d.red)
        d.poly(
            [(35, 160), (270, 160), (445, 60), (445, 275), (35, 275)],
            closed=True,
            fill=d.glass,
        )
        d.path("M 50 149 Q 180 93 303 100", color=d.red, width=3)
        d.arrow(295, 101, 329, 87, color=d.red)
        _cloud(d, 300, 73)
        d.arrow(372, 226, 281, 226, color=d.blue, width=3)
        text(d, "warm air", 127, 206)
        text(d, "cold air", 348, 262)
    elif v == "orographic_rain":
        _hill(d, [(30, 265), (160, 153), (247, 75), (335, 169), (455, 266)], d.gold)
        _cloud(d, 159, 100)
        for x in [129, 153, 176]:
            d.line(x, 124, x - 8, 146, color=d.blue, width=2)
        d.arrow(42, 180, 153, 136, color=d.blue, width=4)
        d.arrow(302, 125, 409, 225, color=d.red, width=4)
        text(d, "windward", 96, 306)
        text(d, "rain shadow", 379, 306)
    elif v == "climate_plot":
        temp = p["temperature"]
        rain = p["rainfall"]
        if (
            len(temp) != 12
            or len(rain) != 12
            or any(type(n) not in {int, float} or not -60 <= n <= 60 for n in temp)
            or any(type(n) not in {int, float} or not 0 <= n <= 1000 for n in rain)
        ):
            raise DiagramError("diagram_invalid_climate_data")
        xy = axes(d, x_label="month", y_label="°C", ticks=False)
        maxrain = max(10, max(rain))
        lo = min(-10, min(temp) - 5)
        hi = max(30, max(temp) + 5)
        for i, n in enumerate(rain):
            x = 66 + i * 29
            h = n / maxrain * 185
            d.rect(x, 260 - h, 19, h, fill=d.blue, width=1)
            text(d, str(i + 1), x + 10, 283, size=10)
        d.poly(
            [
                (75 + i * 29, 250 - (t - lo) / (hi - lo) * 195)
                for i, t in enumerate(temp)
            ],
            color=d.red,
            width=3,
        )
        text(d, f"rain max: {maxrain:g} mm", 336, 31, size=12, color=d.blue)
        text(d, f"{lo:g} … {hi:g} °C", 114, 31, size=12, color=d.red)
        d.facts.update(temperature=temp, rainfall=rain, data_source="task_parameters")
    elif v == "earthquake":
        _hill(d, [(25, 113), (120, 105), (225, 132), (322, 95), (455, 103)], d.surface)
        d.line(137, 277, 330, 103, color=d.red, width=4)
        focus = (208, 213)
        for r in [25, 50, 75]:
            d.circle(*focus, r, color=d.gold, width=1)
        d.circle(*focus, 5, fill=d.red)
        d.line(208, 213, 208, 129, dashed=True)
        text(d, "epicenter", 209, 107)
        d.arrow(163, 175, 123, 207, color=d.blue)
        d.arrow(288, 205, 329, 166, color=d.blue)
    elif v == "travel_time":
        pts = p["points"]
        if not 2 <= len(pts) <= 30 or any(
            not isinstance(a, list)
            or len(a) != 3
            or any(type(n) not in {int, float} or n < 0 for n in a)
            or a[1] >= a[2]
            for a in pts
        ):
            raise DiagramError("diagram_invalid_travel_data")
        mx = max(a[0] for a in pts)
        my = max(a[2] for a in pts)
        if min(mx, my) <= 0:
            raise DiagramError("diagram_invalid_travel_data")
        xy = axes(d, x_label="distance", y_label="time")
        for col, c, label in [(1, d.blue, "P"), (2, d.red, "S")]:
            d.poly([xy(a[0] / mx * 0.9, a[col] / my * 0.9) for a in pts], color=c)
            text(
                d,
                label,
                xy(0.8, pts[-1][col] / my * 0.9)[0],
                xy(0.8, pts[-1][col] / my * 0.9)[1] - 15,
                color=c,
            )
        d.facts["data_source"] = "task_parameters"
    elif v == "rock_cycle":
        _flow_loop(
            d,
            ["magma", "igneous", "sedimentary", "metamorphic"],
            [(100, 70), (380, 70), (380, 245), (100, 245)],
            [d.red, d.muted, d.gold, d.glass],
        )
        for x, y, col in [(95, 142, d.red), (380, 151, d.gold), (245, 240, d.muted)]:
            d.poly(
                [(x - 14, y), (x, y - 14), (x + 16, y + 7), (x - 6, y + 15)],
                closed=True,
                fill=col,
            )
        text(d, "cooling", 240, 56)
        text(d, "weathering", 387, 188, size=11)
        text(d, "heat / pressure", 240, 278, size=11)
        text(d, "melting", 91, 190, size=11)
    elif v == "ocean_gyres":
        d.rect(35, 45, 410, 235, fill=d.glass)
        for pts in [
            [(40, 50), (133, 50), (164, 110), (116, 151), (154, 270), (68, 250)],
            [(317, 49), (431, 50), (435, 255), (358, 266), (350, 185), (288, 131)],
        ]:
            d.poly(pts, closed=True, fill=d.gold)
        for cy, sign in [(115, 1), (222, -1)]:
            d.ellipse(234, cy, 55, 38, color=d.blue)
            d.arrow(
                210 if sign > 0 else 250,
                cy - 35,
                250 if sign > 0 else 210,
                cy - 35,
                color=d.red if sign > 0 else d.blue,
            )
            d.arrow(
                257 if sign > 0 else 217,
                cy + 35,
                217 if sign > 0 else 257,
                cy + 35,
                color=d.blue if sign > 0 else d.red,
            )
        d.line(42, 168, 437, 168, dashed=True, color=d.muted)
        text(d, "equator", 220, 187, size=11)
    elif v == "time_zones":
        for i in range(13):
            x = 35 + i * 32
            offset = i - 6
            d.rect(x, 80, 31, 155, fill=d.glass if offset % 2 else d.surface, width=0.8)
            text(d, f"{offset:+d}", x + 15, 64, size=11)
            text(d, str((p["utc_hour"] + offset) % 24), x + 15, 161, size=13)
        d.line(242, 40, 242, 268, color=d.red, width=2)
        text(d, "UTC offsets · conceptual zones", 240, 303, size=12)
        d.facts.update(
            utc_hour=p["utc_hour"], zone_width_degrees=15, political_boundaries=False
        )
    elif v == "solar_altitude":
        altitude = 90 - abs(p["latitude"] - p["declination"])
        angle = math.radians(altitude)
        d.line(55, 255, 430, 255, width=3)
        d.line(160, 255, 160, 50, dashed=True, color=d.muted)
        d.arrow(
            160 + 180 * math.cos(angle),
            255 - 180 * math.sin(angle),
            160,
            255,
            color=d.gold,
            width=3,
        )
        d.poly(
            [
                (160 + 50 * math.cos(t), 255 - 50 * math.sin(t))
                for t in [i * angle / 30 for i in range(31)]
            ],
            color=d.blue,
        )
        ball(d, 160 + 185 * math.cos(angle), 255 - 185 * math.sin(angle), 18, d.gold)
        text(d, f"h = {altitude:.2f}°", 292, 52)
        text(d, "local solar noon", 240, 299)
        d.facts.update(
            latitude=p["latitude"], declination=p["declination"], noon_altitude=altitude
        )
    elif v == "projection_distortion":
        for x in [128, 354]:
            if x < 200:
                d.circle(x, 159, 96, fill=d.glass)
                for rx in [25, 60, 85]:
                    d.ellipse(x, 159, rx, 96, color=d.muted, width=1)
                for dy in [-60, 0, 60]:
                    d.ellipse(
                        x,
                        159 + dy,
                        math.sqrt(96**2 - dy**2),
                        10,
                        color=d.muted,
                        width=1,
                    )
            else:
                d.rect(x - 85, 45, 170, 230, fill=d.glass)
                for xx in [x - 42, x, x + 42]:
                    d.line(xx, 45, xx, 275, color=d.muted, width=1)
                for y in [76, 130, 160, 190, 244]:
                    d.line(x - 85, y, x + 85, y, color=d.muted, width=1)
        connect(d, (231, 159), (259, 159))
        text(d, "projection changes area / shape", 240, 309, size=12)
    elif v == "urban_zones":
        for r, c in [(125, d.green), (99, d.gold), (70, d.glass), (34, d.red)]:
            d.circle(240, 157, r, fill=c, width=1)
        for angle in [0, 0.8, 2.1, 3.6, 5]:
            d.line(
                240,
                157,
                240 + 125 * math.cos(angle),
                157 + 125 * math.sin(angle),
                color=d.muted,
                width=1,
            )
        _house(d, 218, 145, 43, 30)
        text(d, "conceptual land-use model", 240, 309, size=12)
    elif v == "migration_network":
        points = [(95, 85), (360, 70), (240, 174), (98, 261), (389, 265)]
        for i, (x, y) in enumerate(points):
            d.circle(x, y, [20, 30, 38, 17, 25][i], fill=d.glass)
            text(d, chr(65 + i), x, y + 5)
        for i, j, width in [(0, 2, 3), (1, 2, 5), (3, 2, 2), (2, 4, 4)]:
            a, b = points[i], points[j]
            dx, dy = b[0] - a[0], b[1] - a[1]
            r = math.hypot(dx, dy)
            d.arrow(
                a[0] + dx / r * 35,
                a[1] + dy / r * 35,
                b[0] - dx / r * 42,
                b[1] - dy / r * 42,
                color=d.blue,
                width=width,
            )
        d.facts["schematic_network"] = True
    elif v == "soil_profile":
        for y, h, col, label in [
            (50, 32, d.green, "O"),
            (82, 58, d.muted, "A"),
            (140, 68, d.gold, "B"),
            (208, 47, d.surface, "C"),
            (255, 34, d.glass, "R"),
        ]:
            d.rect(110, y, 260, h, fill=col, width=1)
            text(d, label, 78, y + h / 2 + 5)
        plant(d, 237, 55, 32)
        _roots(d, 237, 70, 91)
        for x, y in [(141, 233), (239, 241), (316, 227)]:
            d.poly(
                [(x - 13, y), (x, y - 9), (x + 14, y + 4)],
                closed=True,
                fill=d.muted,
                width=1,
            )
    elif v == "soil_aggregate":
        for x, y, r in [
            (140, 115, 38),
            (225, 85, 40),
            (295, 129, 46),
            (191, 172, 48),
            (281, 218, 41),
            (355, 204, 31),
        ]:
            ball(d, x, y, r, d.gold)
        d.path("M 176 97 Q 204 132 257 141 Q 229 190 323 224", color=d.blue, width=5)
        d.path("M 329 62 Q 222 130 159 247", color=d.green, width=3)
        text(d, "particles · pores · roots", 240, 302, size=13)
    elif v == "soil_triangle":
        sand, clay = p["sand"], p["clay"]
        silt = 100 - sand - clay
        if silt < 0:
            raise DiagramError("diagram_invalid_soil_composition")
        a, b, c = (65, 270), (415, 270), (240, 38)
        d.poly([a, b, c], closed=True, fill=d.surface)
        for i in range(1, 5):
            t = i / 5
            for aa, bb, cc in [(a, b, c), (b, c, a), (c, a, b)]:
                d.line(
                    aa[0] + (bb[0] - aa[0]) * t,
                    aa[1] + (bb[1] - aa[1]) * t,
                    cc[0] + (bb[0] - cc[0]) * t,
                    cc[1] + (bb[1] - cc[1]) * t,
                    color=d.muted,
                    width=0.7,
                )
        point = (
            (a[0] * sand + b[0] * silt + c[0] * clay) / 100,
            (a[1] * sand + b[1] * silt + c[1] * clay) / 100,
        )
        d.circle(*point, 6, fill=d.red)
        text(d, "sand", 60, 298)
        text(d, "silt", 419, 298)
        text(d, "clay", 240, 25)
        d.facts.update(
            sand_percent=sand,
            silt_percent=silt,
            clay_percent=clay,
            classification_boundaries=False,
        )
    elif v == "root_depth":
        d.rect(35, 140, 410, 145, fill=d.surface)
        d.line(35, 140, 445, 140, color=d.green, width=3)
        for x, h in [(110, 55), (245, 90), (380, 130)]:
            plant(d, x, 139, 80)
            _roots(d, x, 144, h)
            d.arrow(x + 48, 145, x + 48, 144 + h, double=True, color=d.blue)
        text(d, "contrasting root systems", 240, 311, size=12)
    elif v == "plant_spacing":
        rows, cols = p["rows"], p["columns"]
        d.rect(55, 45, 370, 225, fill=d.surface)
        for row in range(rows):
            for col in range(cols):
                x = 87 + col * 305 / (cols - 1)
                y = 73 + row * 163 / (rows - 1)
                leaf(d, x, y, w=16, h=9, rotate=-30)
                leaf(d, x, y, w=16, h=9, rotate=200)
        d.arrow(87, 288, 87 + 305 / (cols - 1), 288, double=True, color=d.blue)
        d.arrow(38, 73, 38, 73 + 163 / (rows - 1), double=True, color=d.gold)
        d.facts.update(rows=rows, columns=cols)
    elif v in {"rotation_layout", "intercropping"}:
        if v == "rotation_layout":
            for i, (x, y) in enumerate([(80, 55), (265, 55), (265, 185), (80, 185)]):
                d.rect(x, y, 135, 85, fill=[d.green, d.gold, d.glass, d.surface][i])
                text(
                    d,
                    ["legume", "grain", "root crop", "cover crop"][i],
                    x + 67,
                    y + 47,
                    size=12,
                )
            for a, b in [
                ((223, 96), (257, 96)),
                ((332, 148), (332, 177)),
                ((257, 226), (223, 226)),
                ((147, 177), (147, 148)),
            ]:
                connect(d, a, b, color=d.blue)
        else:
            d.rect(55, 45, 370, 225, fill=d.surface)
            for row in range(6):
                y = 67 + row * 35
                d.line(64, y, 414, y, color=d.muted, width=1)
                for col in range(7):
                    x = 82 + col * 48
                    if row % 2:
                        leaf(d, x, y, w=14, h=7, rotate=25)
                    else:
                        d.line(x, y - 10, x, y + 7, color=d.gold, width=2)
                        d.ellipse(x, y - 8, 4, 7, fill=d.gold, width=1)
            text(d, "alternating crop rows", 240, 305)
    elif v == "seedling_tray":
        box(d, 72, 100, 318, 140, 30)
        for row in range(3):
            for col in range(6):
                x = 95 + col * 49 + row * 6
                y = 118 + row * 37
                d.ellipse(x, y, 19, 10, fill=d.gold, width=1)
                plant(d, x, y, 25)
        text(d, "separate root plugs", 240, 305)
    elif v == "transplant_rootball":
        plant(d, 120, 152, 92)
        d.path("M 85 148 L 155 148 L 145 220 L 95 220 Z", fill=d.gold)
        _roots(d, 120, 154, 59)
        d.rect(278, 165, 155, 110, fill=d.surface)
        d.path("M 300 165 L 312 237 L 395 237 L 408 165", fill=d.gold)
        plant(d, 355, 162, 95)
        _roots(d, 355, 166, 63)
        connect(d, (172, 176), (263, 176))
        text(d, "keep root ball intact", 240, 307)
    elif v == "greenhouse":
        d.poly(
            [(80, 275), (80, 116), (240, 38), (400, 116), (400, 275)],
            closed=True,
            fill=d.glass,
        )
        for x in [80, 160, 240, 320, 400]:
            d.line(x, 275, x, 116, width=2)
        d.line(80, 116, 400, 116)
        d.poly([(160, 275), (160, 74), (240, 38), (320, 74), (320, 275)], color=d.muted)
        for x in [117, 202, 287, 371]:
            plant(d, x, 262, 75)
        d.rect(203, 46, 75, 15, fill=d.surface)
        d.arrow(230, 39, 230, 17, color=d.red)
        text(d, "ventilation", 395, 45, size=12)
    elif v in {"drip_irrigation", "sprinkler"}:
        d.rect(40, 216, 400, 60, fill=d.surface)
        for x in [100, 200, 300, 400]:
            plant(d, x, 216, 65)
            _roots(d, x, 220, 45)
        if v == "drip_irrigation":
            d.line(47, 196, 420, 196, color=d.blue, width=5)
            for x in [100, 200, 300, 400]:
                d.circle(x, 196, 4, fill=d.muted)
                d.circle(x, 205, 3, fill=d.blue, width=1)
        else:
            d.line(240, 210, 240, 140, color=d.muted, width=5)
            d.rect(220, 132, 40, 9, fill=d.blue)
            for end in [70, 120, 170, 310, 360, 410]:
                d.path(
                    f"M 240 135 Q {(240+end)/2} 35 {end} 209", color=d.blue, width=1.5
                )
        text(d, "water delivered to crop", 240, 309, size=12)
    elif v == "water_balance":
        d.rect(120, 153, 240, 125, fill=d.gold)
        plant(d, 240, 154, 84)
        _roots(d, 240, 162, 70)
        _cloud(d, 83, 62, 0.7)
        d.arrow(83, 90, 132, 157, color=d.blue, width=3)
        text(d, "P", 54, 133)
        d.arrow(247, 67, 280, 30, color=d.red, width=3)
        text(d, "ET", 323, 42)
        d.arrow(129, 162, 55, 213, color=d.blue, width=3)
        text(d, "runoff", 60, 239, size=12)
        d.arrow(320, 228, 320, 299, color=d.blue, width=3)
        text(d, "drainage", 390, 304, size=12)
        text(d, "ΔS = P + I − ET − R − D", 240, 20, size=12)
    elif v == "compost":
        d.path("M 68 250 Q 115 105 242 135 Q 353 81 423 250 Z", fill=d.gold)
        for i in range(28):
            x = 104 + (i * 49) % 270
            y = 170 + (i * 31) % 65
            d.line(x, y, x + 13, y + 4, color=d.green if i % 2 else d.muted, width=2)
        for x in [150, 240, 330]:
            d.path(
                f"M {x} 143 Q {x-15} 125 {x} 109 Q {x+15} 93 {x} 79",
                color=d.red,
                width=1.5,
            )
        d.line(260, 68, 277, 190, width=4)
        d.rect(254, 53, 9, 27, fill=d.glass)
        d.arrow(58, 225, 109, 214, color=d.blue)
        text(d, "air + moisture + organic matter", 240, 300, size=12)
    elif v == "pollination":
        for x in [120, 360]:
            for i in range(5):
                a = i * 2 * math.pi / 5
                d.ellipse(
                    x + 23 * math.cos(a),
                    170 + 23 * math.sin(a),
                    17,
                    22,
                    fill=d.red,
                    width=1,
                )
            d.circle(x, 170, 18, fill=d.gold)
            d.line(x, 208, x, 274, color=d.green, width=3)
        d.ellipse(240, 105, 23, 13, fill=d.gold)
        d.ellipse(231, 87, 10, 15, fill=d.glass)
        d.ellipse(253, 88, 10, 15, fill=d.glass)
        for x in [229, 240, 251]:
            d.line(x, 96, x, 115, width=2)
        d.path(
            "M 147 156 Q 206 55 214 103 M 266 105 Q 310 105 339 152",
            color=d.blue,
            dashed=True,
        )
        text(d, "pollen transfer", 240, 305)
    elif v == "grafting":
        for x, stage in [(110, 0), (360, 1)]:
            d.rect(x - 12, 158, 24, 117, fill=d.gold)
            d.poly(
                [(x - 12, 159), (x, 191), (x + 12, 159)], closed=True, fill=d.surface
            )
            d.line(x, 84, x, 175 if stage else 140, color=d.green, width=7)
            leaf(d, x, 101, rotate=-30)
            if stage:
                d.rect(x - 17, 153, 34, 39, fill=d.glass, width=1)
                d.line(x - 17, 164, x + 17, 164)
                d.line(x - 17, 176, x + 17, 176)
        connect(d, (170, 166), (296, 166))
        text(d, "aligned cambium", 360, 306)
    elif v == "cutting":
        for x, rooted in [(110, False), (360, True)]:
            vessel(d, x - 43, 150, 86, 123, 0.57)
            plant(d, x, 171, 110)
            if rooted:
                _roots(d, x, 178, 70)
        connect(d, (175, 163), (295, 163))
        text(d, "adventitious roots", 348, 309, size=12)
    elif v == "nutrient_sites":
        plant(d, 240, 251, 170)
        _roots(d, 240, 253, 49)
        for x, y, w, rot in [
            (234, 201, 66, 20),
            (242, 151, 66, 190),
            (243, 106, 43, -25),
        ]:
            leaf(d, x, y, w=w, h=22, rotate=rot)
        d.line(201, 146, 80, 106, color=d.muted)
        text(d, "older leaves", 83, 84, size=12)
        d.line(264, 94, 378, 64, color=d.muted)
        text(d, "new growth", 379, 45, size=12)
        text(d, "mobile / immobile nutrient sites", 240, 315, size=12)
    elif v == "crop_stages":
        d.line(35, 248, 445, 248, color=d.gold, width=3)
        for i, x in enumerate([65, 180, 295, 410]):
            if not i:
                d.ellipse(x, 235, 12, 7, fill=d.gold)
            else:
                plant(d, x, 247, [0, 45, 105, 155][i])
                _roots(d, x, 250, 30)
            if i == 3:
                for dx in [-7, 0, 7]:
                    d.ellipse(x + dx, 97 + abs(dx), 4, 9, fill=d.gold, width=1)
            text(
                d,
                ["seed", "seedling", "vegetative", "reproductive"][i],
                x,
                302,
                size=11,
            )
            if i:
                connect(d, (x - 78, 190), (x - 37, 190), color=d.blue)
    elif v == "harvest_storage":
        box(d, 45, 152, 130, 105, 25)
        for x in [73, 105, 138]:
            d.path(f"M {x} 155 Q {x-20} 120 {x} 93 Q {x+20} 120 {x} 155", fill=d.gold)
        _house(d, 290, 121, 125, 141)
        d.rect(320, 153, 62, 47, fill=d.glass)
        for x in [312, 385]:
            d.arrow(x, 243, x, 171, color=d.blue)
        connect(d, (208, 191), (272, 191))
        text(d, "dry · cool · ventilated", 336, 306, size=12)
    elif v == "nitrogen_cycle":
        positions = [(95, 55), (95, 250), (375, 250), (375, 55)]
        for label, pos in zip(
            ["atmospheric N₂", "NH₄⁺", "NO₃⁻", "plants / animals"], positions
        ):
            node(d, label, *pos, w=132, h=42)
        d.arrow(95, 80, 95, 222, color=d.blue)
        text(d, "fixation", 55, 159, size=11)
        d.arrow(165, 250, 304, 250, color=d.blue)
        text(d, "nitrification", 240, 278, size=11)
        d.arrow(375, 222, 375, 80, color=d.green)
        text(d, "uptake", 412, 157, size=11)
        d.arrow(337, 220, 131, 85, color=d.gold)
        text(d, "denitrification", 189, 127, size=11)
        d.arrow(341, 85, 131, 220, color=d.muted)
        text(d, "ammonification", 270, 190, size=11)
        d.facts["cycle"] = "nitrogen"
    elif v in {"carbon_cycle", "phosphorus_cycle"}:
        labels = {
            "carbon_cycle": ["atmospheric CO₂", "plants", "animals", "soil / fuels"],
            "nitrogen_cycle": [
                "atmospheric N₂",
                "NH₄⁺ / soil",
                "NO₃⁻",
                "plants / animals",
            ],
            "phosphorus_cycle": [
                "rock phosphate",
                "soil phosphate",
                "organisms",
                "sediment",
            ],
        }[v]
        _flow_loop(d, labels, [(110, 60), (369, 60), (369, 251), (110, 251)])
        plant(d, 238, 190, 83)
        d.rect(178, 193, 126, 24, fill=d.gold, width=1)
        texts = {
            "carbon_cycle": [
                "fixation",
                "feeding",
                "decomposition",
                "respiration / burning",
            ],
            "phosphorus_cycle": ["weathering", "uptake", "burial", "uplift"],
        }[v]
        for label, x, y in zip(texts, [240, 375, 240, 99], [42, 157, 293, 157]):
            text(d, label, x, y, size=11)
        if v == "carbon_cycle":
            d.arrow(336, 229, 146, 81, color=d.red, width=1.5)
            text(d, "respiration", 288, 175, size=10, color=d.red)
        d.facts["cycle"] = v
    elif v == "water_layers":
        for y, h, col, label in [
            (60, 66, d.glass, "epilimnion"),
            (126, 55, d.blue, "thermocline"),
            (181, 105, d.muted, "hypolimnion"),
        ]:
            d.rect(65, y, 260, h, fill=col, width=1)
            text(d, label, 195, y + h / 2 + 5, size=12)
        d.poly([(378, 65), (375, 122), (349, 180), (345, 282)], color=d.red, width=3)
        text(d, "T", 374, 41)
    elif v == "eutrophication":
        d.rect(32, 151, 415, 130, fill=d.glass)
        d.poly([(32, 151), (176, 151), (162, 105), (32, 105)], closed=True, fill=d.gold)
        for x in range(205, 441, 18):
            d.ellipse(x, 160, 10, 4, fill=d.green, width=1)
        d.arrow(110, 116, 211, 149, color=d.blue, width=4)
        text(d, "N / P", 111, 91)
        for x, y in [(223, 242), (339, 251), (405, 219)]:
            d.ellipse(x, y, 17, 7, fill=d.muted)
            d.poly(
                [(x + 17, y), (x + 29, y - 8), (x + 29, y + 8)],
                closed=True,
                fill=d.muted,
                width=1,
            )
        d.arrow(295, 174, 295, 228, color=d.red)
        text(d, "O₂ ↓", 265, 217)
        text(d, "nutrients → bloom → decomposition", 240, 306, size=12)
    elif v in {"wastewater", "drinking_water"}:
        labels = (
            ["screen", "settle", "aerate", "clarify", "disinfect"]
            if v == "wastewater"
            else ["coagulate", "settle", "filter", "disinfect"]
        )
        spacing = 400 / len(labels)
        for i, label in enumerate(labels):
            x = 45 + i * spacing
            vessel(d, x, 130, spacing - 23, 94, 0.6)
            text(d, label, x + (spacing - 23) / 2, 260, size=11)
            if i:
                connect(d, (x - 18, 169), (x - 4, 169), color=d.blue)
            if "sett" in label or "clar" in label:
                for j in range(4):
                    d.circle(x + 9 + j * 9, 218, 3, fill=d.gold, width=1)
            elif label == "aerate":
                for j in range(6):
                    d.circle(
                        x + 12 + (j % 3) * 13,
                        190 - (j // 3) * 20,
                        3,
                        fill=d.surface,
                        width=0.7,
                    )
            elif label == "filter":
                d.rect(x + 6, 182, spacing - 35, 23, fill=d.gold, width=1)
            elif label == "screen":
                for dx in range(10, int(spacing - 25), 10):
                    d.line(x + dx, 144, x + dx, 211, color=d.muted, width=2)
        d.arrow(35, 104, 444, 104, color=d.blue)
        text(d, "treatment sequence", 240, 66)
    elif v == "waste_sorting":
        for i, (label, col) in enumerate(
            [
                ("recyclable", d.blue),
                ("organic", d.green),
                ("hazardous", d.red),
                ("residual", d.muted),
            ]
        ):
            x = 34 + i * 112
            d.path(f"M {x} 106 L {x+86} 106 L {x+75} 261 L {x+11} 261 Z", fill=col)
            d.rect(x - 5, 96, 96, 12, fill=d.surface, radius=3)
            d.rect(x + 24, 86, 37, 9, fill=d.muted)
            text(d, label, x + 43, 291, size=11)
            text(d, ["↻", "leaf", "!", "…"][i], x + 43, 186, size=20, color="#fff")
    elif v == "recycling_loop":
        _flow_loop(
            d,
            ["collect", "sort", "reprocess", "make / use"],
            [(110, 65), (370, 65), (370, 250), (110, 250)],
            [d.glass, d.gold, d.green, d.surface],
        )
        d.circle(240, 159, 42, color=d.blue, width=3)
        d.arrow(202, 142, 218, 120, color=d.blue)
        d.arrow(276, 176, 262, 198, color=d.blue)
        text(d, "material", 240, 164, size=12)
    elif v == "greenhouse_energy":
        d.rect(30, 246, 420, 35, fill=d.green)
        d.path("M 30 90 Q 240 17 450 90", color=d.blue, width=4)
        ball(d, 69, 50, 20, d.gold)
        d.arrow(92, 73, 206, 244, color=d.gold, width=5)
        d.arrow(221, 244, 316, 98, color=d.red, width=3)
        d.arrow(313, 99, 352, 227, color=d.red, width=3)
        d.arrow(321, 98, 362, 39, color=d.red, width=3)
        text(d, "shortwave", 123, 164, size=12)
        text(d, "longwave", 335, 165, size=12)
        text(d, "atmosphere", 238, 52, size=12)
    elif v == "urban_heat":
        d.line(30, 260, 450, 260, color=d.gold, width=3)
        for x, w, h in [(151, 35, 74), (193, 52, 120), (253, 40, 92), (302, 36, 68)]:
            d.rect(x, 260 - h, w, h, fill=d.muted)
            d.rect(x + 8, 268 - h, 12, 19, fill=d.glass, width=1)
        for x in [64, 391, 434]:
            plant(d, x, 260, 42)
        d.poly(
            [
                (35, 143),
                (104, 132),
                (170, 102),
                (244, 67),
                (315, 102),
                (385, 134),
                (443, 147),
            ],
            color=d.red,
            width=3,
        )
        text(d, "temperature profile", 240, 36)
        text(d, "rural", 62, 295)
        text(d, "urban", 240, 295)
        text(d, "rural", 417, 295)
    elif v == "rain_garden":
        _house(d, 40, 121, 70, 73)
        d.poly(
            [
                (28, 250),
                (140, 218),
                (196, 254),
                (279, 254),
                (350, 215),
                (452, 232),
                (452, 282),
                (28, 282),
            ],
            closed=True,
            fill=d.gold,
        )
        d.path("M 141 217 Q 247 285 349 215", fill=d.glass, color=d.blue)
        for x in [193, 240, 287]:
            plant(d, x, 247, 58)
            _roots(d, x, 253, 28)
        d.arrow(117, 193, 181, 229, color=d.blue)
        d.arrow(247, 261, 247, 299, color=d.blue)
        text(d, "infiltration", 341, 307)
    elif v == "sponge_city":
        _house(d, 35, 110, 63, 75)
        d.rect(131, 190, 75, 70, fill=d.glass)
        plant(d, 168, 190, 33)
        d.rect(246, 190, 87, 70, fill=d.surface)
        d.hatch(246, 190, 87, 14, spacing=14)
        d.rect(367, 211, 78, 48, fill=d.glass, radius=15)
        for x in [79, 174, 288, 405]:
            _cloud(d, x, 62, 0.45)
            d.arrow(x, 84, x, 173, color=d.blue)
        text(d, "capture", 79, 292, size=12)
        text(d, "retain", 174, 292, size=12)
        text(d, "infiltrate", 288, 292, size=12)
        text(d, "store", 405, 292, size=12)
    elif v in {"ecological_corridor", "fragmentation"}:
        d.rect(25, 45, 430, 235, fill=d.surface)
        if v == "ecological_corridor":
            d.path(
                "M 63 113 Q 143 80 194 148 Q 253 219 398 154", color=d.green, width=8
            )
            for x, y in [(85, 132), (380, 152)]:
                d.ellipse(x, y, 60, 75, fill=d.green)
            for x, y in [(173, 137), (258, 189), (325, 177)]:
                plant(d, x, y, 25)
        else:
            for x, y, rx, ry in [
                (95, 107, 55, 46),
                (214, 155, 37, 43),
                (372, 117, 62, 44),
                (329, 246, 68, 22),
                (102, 244, 45, 25),
            ]:
                d.ellipse(x, y, rx, ry, fill=d.green)
            d.line(43, 192, 444, 192, color=d.muted, width=8)
            d.line(280, 53, 280, 275, color=d.muted, width=8)
        text(d, "habitat connectivity", 240, 309, size=12)
    elif v == "succession":
        d.line(30, 250, 450, 250, color=d.gold, width=3)
        for i, x in enumerate([65, 177, 290, 410]):
            if i == 0:
                leaf(d, x, 244, w=18, h=5)
            elif i == 1:
                for dx in [-14, 0, 14]:
                    plant(d, x + dx, 250, 24)
            elif i == 2:
                plant(d, x, 250, 82)
            else:
                plant(d, x, 250, 155)
                d.circle(x, 125, 37, fill=d.green)
                d.circle(x - 24, 152, 29, fill=d.green)
            text(d, ["pioneer", "grass", "shrub", "woodland"][i], x, 302, size=12)
            if i:
                connect(d, (x - 80, 209), (x - 43, 209), color=d.blue)
    elif v == "energy_conversion":
        ball(d, 65, 105, 25, d.gold)
        d.poly(
            [(125, 189), (191, 151), (208, 208), (143, 247)], closed=True, fill=d.blue
        )
        for i in range(1, 4):
            d.line(
                125 + i * 16,
                189 - i * 9,
                143 + i * 16,
                247 - i * 9,
                color=d.glass,
                width=1,
            )
        d.arrow(95, 115, 143, 166, color=d.gold, width=4)
        node(d, "electricity", 300, 185, w=105)
        d.arrow(216, 202, 245, 187, color=d.blue)
        d.line(402, 102, 402, 250, width=4)
        for a in [0, 2 * math.pi / 3, 4 * math.pi / 3]:
            d.line(402, 102, 402 + 47 * math.cos(a), 102 + 47 * math.sin(a), width=5)
        d.arrow(390, 155, 348, 179, color=d.blue)
        text(d, "renewable conversion", 240, 300)
    elif v == "lifecycle_flow":
        _flow_loop(
            d,
            ["raw material", "manufacture", "use", "recovery"],
            [(105, 70), (375, 70), (375, 247), (105, 247)],
        )
        box(d, 206, 117, 59, 68, 15)
        text(d, "product", 240, 220, size=12)
        for x, y in [(231, 70), (375, 160), (238, 247)]:
            d.arrow(x, y, x, y + 32, color=d.red)
            text(d, "impact", x + 35, y + 26, size=10)
    elif v == "air_dispersion":
        d.rect(60, 175, 64, 93, fill=d.muted)
        d.rect(93, 94, 17, 88, fill=d.surface)
        d.path(
            "M 110 103 Q 226 62 445 42 L 445 184 Q 247 146 110 112 Z",
            fill=d.glass,
            color=d.blue,
        )
        for i in range(30):
            x = 130 + (i * 53) % 300
            y = 103 + (i % 5 - 2) * (x - 100) / 16
            d.circle(x, y, 2, fill=d.muted, width=0.5)
        d.arrow(211, 29, 322, 29, color=d.blue)
        text(d, "wind", 366, 34, size=12)
        text(d, "schematic plume", 293, 237)
        d.facts["quantitative_dispersion_model"] = False
    elif v == "footprint":
        d.path(
            "M 193 280 Q 165 241 193 204 Q 205 176 201 141 Q 199 94 241 79 Q 285 82 288 122 Q 285 161 264 204 Q 246 240 255 268 Q 231 296 193 280 Z",
            fill=d.green,
        )
        for x, y, r in [
            (211, 55, 15),
            (242, 42, 17),
            (274, 49, 14),
            (300, 70, 12),
            (314, 95, 10),
        ]:
            d.circle(x, y, r, fill=d.green)
        for x, y, label in [
            (108, 118, "food"),
            (379, 116, "travel"),
            (99, 242, "housing"),
            (381, 252, "goods"),
        ]:
            node(d, label, x, y, w=86, h=35)
            d.line(
                x + 40 if x < 200 else x - 40,
                y,
                190 if x < 200 else 282,
                y,
                color=d.muted,
                width=1,
            )
        text(d, "resource / carbon accounting", 240, 314, size=12)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
