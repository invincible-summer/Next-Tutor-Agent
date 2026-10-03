"""Original textbook apparatus and physical objects, built from editable geometry."""
from __future__ import annotations

import math

from .drawing import Drawing, num


def _liquid(d: Drawing, polygon, level: float, color: str):
    if not level:
        return
    top, bottom = min(y for _, y in polygon), max(y for _, y in polygon)
    y0 = bottom - (bottom - top) * level
    clipped = []
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        inside1, inside2 = start[1] >= y0, end[1] >= y0
        if inside1:
            clipped.append(start)
        if inside1 != inside2:
            t = (y0 - start[1]) / (end[1] - start[1])
            clipped.append((start[0] + t * (end[0] - start[0]), y0))
    if len(clipped) >= 3:
        d.poly(clipped, closed=True, fill=color, color=color, width=.5)
        xs = [x for x, y in clipped if abs(y - y0) < .01]
        if len(xs) >= 2:
            d.line(min(xs), y0, max(xs), y0, color=d.blue, width=1.4)
    d.facts["liquid_height_fraction"] = level


def vessel(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    fill = p.get("fill", 0)
    liquid_color = p.get("liquid_color", d.blue)
    if mono:
        liquid_color = d.blue
    if variant == "drying_tube":
        d.path("M 74 12 L 74 42 C 43 47 43 112 74 119 L 74 148 L 86 148 L 86 119 C 117 112 117 47 86 42 L 86 12 Z", fill=d.glass)
        for x, y in [(68, 60), (84, 65), (71, 78), (89, 85), (69, 99), (85, 105)]:
            d.circle(x, y, 5, fill=d.surface, width=1)
        d.line(79, 49, 79, 112, color="#fff", width=2)
        d.anchors.update({"tube_inlet": (80, 12), "tube_outlet": (80, 148)})
        return d
    if variant in {"beaker", "tall_beaker", "measuring_cup", "overflow_cup", "calorimeter", "cup", "bucket"}:
        left, right, top, bottom = (48, 112, 24, 135) if variant == "tall_beaker" else (35, 125, 45, 135)
        outline = [(left, top), (left + 3, bottom - 6), (left + 9, bottom),
                   (right - 9, bottom), (right - 3, bottom - 6), (right, top)]
        d.poly(outline, closed=True, fill=d.glass, color=d.glass)
        _liquid(d, outline, fill, liquid_color)
        d.poly(outline)
        d.path(f"M {left - 4} {top} Q 80 {top - 8} {right + 4} {top} L {right + 9} {top - 4}")
        d.line(left + 9, top + 13, left + 11, bottom - 18, color="#fff", width=3)
        if variant == "overflow_cup":
            d.poly([(right, top + 20), (145, top + 20), (145, top + 37)])
            d.anchors["tube_outlet"] = (145, top + 37)
        if variant in {"cup", "measuring_cup"}:
            d.path(f"M {right} {top + 15} C 152 {top + 5} 153 117 {right - 1} 113")
        if variant == "bucket":
            d.path(f"M {left} {top + 4} C 38 3 121 3 {right} {top + 4}")
        if variant == "calorimeter":
            d.rect(left - 7, top - 4, right - left + 14, 8, fill=d.surface, radius=2)
        if p.get("show_scale", False):
            for i in range(1, 5):
                y = bottom - i * (bottom - top) / 5
                d.line(right - 20, y, right - 8, y, width=1)
        d.anchors.update({"mouth": (80, top), "support_bottom": (80, bottom)})
    elif variant in {"test_tube", "hard_test_tube", "sidearm_test_tube", "centrifuge_tube", "drying_tube"}:
        x1, x2 = (59, 101)
        bottom = 140
        polygon = [(x1, 22), (x2, 22), (x2, 119)] + [
            (80 + 21 * math.cos(t), 119 + 21 * math.sin(t))
            for t in [i * math.pi / 16 for i in range(17)]] + [(x1, 22)]
        if variant == "centrifuge_tube":
            polygon = [(x1, 22), (x2, 22), (x2, 101), (80, 140), (x1, 101)]
        d.poly(polygon, closed=True, fill=d.glass, color=d.glass)
        _liquid(d, polygon, fill, liquid_color)
        d.path("M 59 22 L 59 119 Q 59 140 80 140 Q 101 140 101 119 L 101 22") if variant != "centrifuge_tube" else d.poly(polygon)
        d.ellipse(80, 22, 24, 4, fill=d.surface)
        d.line(65, 34, 65, 111, color="#fff", width=3)
        if variant == "sidearm_test_tube":
            d.rect(101, 48, 38, 7, fill=d.glass)
            d.anchors["tube_outlet"] = (139, 51.5)
        if variant == "drying_tube":
            for x, y in [(70, 68), (87, 79), (73, 91), (88, 103)]:
                d.circle(x, y, 5, fill=d.surface, width=1)
        d.anchors.update({"mouth": (80, 22), "tube_inlet": (80, 28), "support_bottom": (80, bottom)})
    elif variant in {"conical_flask", "volumetric_flask", "round_flask", "flat_flask", "distilling_flask", "three_neck_flask"}:
        if variant == "conical_flask":
            polygon = [(69, 18), (91, 18), (91, 61), (126, 125), (123, 137), (37, 137), (34, 125), (69, 61)]
            outline = "M 69 18 L 69 61 L 34 125 Q 30 137 42 137 L 118 137 Q 130 137 126 125 L 91 61 L 91 18"
        else:
            radius = 44 if variant != "volumetric_flask" else 37
            polygon = [(70, 16), (90, 16), (90, 57)] + [
                (80 + radius * math.cos(t), 99 + radius * math.sin(t))
                for t in [(-math.pi / 2 + .24) + i * (2 * math.pi - .48) / 40 for i in range(41)]] + [(70, 57)]
            if variant == "flat_flask":
                polygon = [(x, min(y, 133)) for x, y in polygon]
            outline = "M 70 16 L 70 56 C 24 64 20 121 53 137 Q 80 151 107 137 C 140 121 136 64 90 56 L 90 16"
            if variant == "volumetric_flask":
                outline = "M 71 16 L 71 65 C 38 75 38 127 57 137 Q 80 149 103 137 C 122 127 122 75 89 65 L 89 16"
            if variant == "flat_flask":
                outline = "M 70 16 L 70 56 C 27 63 22 106 48 133 L 112 133 C 138 106 133 63 90 56 L 90 16"
        d.poly(polygon, closed=True, fill=d.glass, color=d.glass)
        _liquid(d, polygon, fill, liquid_color)
        d.path(outline)
        d.ellipse(80, 16, 13, 3, fill=d.surface)
        if variant == "volumetric_flask":
            d.line(71, 44, 89, 44, color=d.blue, width=1.3)
            d.rect(73, 7, 14, 9, fill=d.surface, radius=2)
        if variant == "distilling_flask":
            d.poly([(90, 36), (131, 55), (135, 50), (90, 30)], closed=True, fill=d.glass)
            d.anchors["tube_outlet"] = (133, 52)
        if variant == "three_neck_flask":
            for x in [46, 101]:
                d.rect(x, 38, 13, 32, fill=d.glass)
            d.anchors.update({"mouth_left": (52.5, 38), "mouth_right": (107.5, 38)})
        d.anchors.update({"mouth": (80, 16), "tube_inlet": (80, 20), "support_bottom": (80, 143)})
    elif variant in {"cylinder", "gas_cylinder"}:
        polygon = [(64, 18), (96, 18), (96, 128), (64, 128)]
        d.rect(64, 18, 32, 110, fill=d.glass)
        _liquid(d, polygon, fill, liquid_color)
        d.ellipse(80, 18, 19, 4, fill=d.surface)
        d.ellipse(80, 137, 40, 7, fill=d.surface)
        d.rect(75, 128, 10, 7, fill=d.surface)
        if p.get("show_scale", True):
            capacity = p.get("capacity", 100)
            for i in range(1, 11):
                y = 128 - i * 11
                d.line(83 if i % 2 else 77, y, 95, y, width=1)
                if p.get("scale_labels", False) and i % 2 == 0:
                    d.text(f"{capacity * i / 10:g}", 59, y + 4, size=12, anchor="end")
            d.facts["capacity"] = capacity
        d.anchors.update({"mouth": (80, 18), "support_bottom": (80, 144)})
    elif variant in {"gas_jar", "reagent_bottle", "wide_bottle", "amber_bottle", "wash_bottle", "gas_wash_bottle", "drop_bottle", "bell_jar", "desiccator", "tank"}:
        body_color = d.glass if variant != "amber_bottle" else d.gold
        if variant == "desiccator":
            d.path("M 29 63 L 41 132 Q 80 145 119 132 L 131 63 Z", fill=d.glass)
            d.ellipse(80, 63, 51, 13, fill=d.surface)
            d.path("M 32 58 Q 80 14 128 58", fill=d.glass)
            d.rect(69, 22, 22, 12, fill=d.surface, radius=4)
            d.line(37, 97, 123, 97, color=d.muted)
            for x in range(51, 120, 17):
                d.circle(x, 114, 4, fill=d.surface)
            return d
        if variant == "bell_jar":
            d.path("M 25 137 L 25 70 C 25 13 135 13 135 70 L 135 137 Z", fill=d.glass)
            d.rect(16, 137, 128, 7, fill=d.surface)
        else:
            left, right = (43, 117) if variant != "gas_jar" else (37, 123)
            polygon = [(left, 51), (left + 8, 38), (65, 38), (65, 20), (95, 20), (95, 38), (right - 8, 38), (right, 51), (right, 136), (left, 136)]
            if variant == "wide_bottle":
                polygon = [(left, 51), (left+4, 38), (left+4, 20), (right-4, 20), (right-4, 38), (right, 51), (right, 136), (left, 136)]
            if variant in {"gas_jar", "tank"}:
                polygon = [(left, 28), (right, 28), (right, 136), (left, 136)]
            d.poly(polygon, closed=True, fill=body_color, color=body_color)
            _liquid(d, polygon, fill, liquid_color)
            d.poly(polygon, closed=True)
            if variant not in {"gas_jar", "tank"}:
                d.rect(left+2 if variant == "wide_bottle" else 63, 12, right-left-4 if variant == "wide_bottle" else 34, 8, fill=d.surface, radius=2)
            if variant == "drop_bottle":
                d.path("M 75 18 L 75 101 L 80 115 L 85 101 L 85 18", fill=d.glass)
                d.path("M 71 12 C 69 -1 91 -1 89 12 Z", fill=d.surface)
            if variant in {"wash_bottle", "gas_wash_bottle"}:
                d.poly([(76, 117), (76, 18), (104, 18), (135, 47)])
                d.anchors["tube_outlet"] = (135, 47)
                if variant == "gas_wash_bottle":
                    d.poly([(89, 62), (89, 12), (126, 12)])
                    d.anchors["tube_inlet"] = (135, 47)
                    d.anchors["tube_outlet"] = (126, 12)
            d.anchors.update({"mouth": (80, 28 if variant in {"gas_jar", "tank"} else 20), "support_bottom": (80, 136)})
    else:
        # Porcelain dishes, mortars and watch glasses have distinct profiles.
        if variant in {"crucible", "evaporating_dish", "mortar"}:
            d.path("M 34 62 Q 42 125 80 129 Q 118 125 126 62", fill=d.surface)
            d.ellipse(80, 62, 46, 10, fill="#fff")
            if variant == "crucible":
                d.ellipse(80, 38, 42, 7, fill=d.surface)
                d.rect(73, 28, 14, 7, fill=d.surface, radius=3)
            if variant == "mortar":
                d.path("M 71 89 L 117 28 Q 126 21 129 32 L 90 98 Z", fill=d.surface)
        elif variant == "watch_glass":
            d.ellipse(80, 77, 62, 16, fill=d.glass, color=d.blue)
            d.path("M 18 77 Q 80 128 142 77", fill=d.glass, color=d.blue)
        elif variant == "petri_dish":
            d.ellipse(80, 82, 62, 17, fill=d.glass)
            d.path("M 18 82 L 18 96 Q 80 126 142 96 L 142 82")
            if variant == "petri_dish":
                d.ellipse(80, 51, 62, 17, fill=d.glass)
        d.anchors.update({"mouth": (80, 62), "support_bottom": (80, 129)})
    return d


def apparatus(variant: str, p: dict, mono=False) -> Drawing:
    if variant == "drying_tube":
        return vessel("drying_tube", p, mono)
    d = Drawing(monochrome=mono)
    if variant in {"alcohol_lamp", "bunsen_burner"}:
        if variant == "alcohol_lamp":
            d.ellipse(80, 118, 37, 25, fill=d.glass)
            d.path("M 45 115 L 48 89 Q 80 74 112 89 L 115 115", fill=d.glass)
            d.ellipse(80, 88, 32, 9, fill=d.surface)
            d.rect(73, 67, 14, 19, fill=d.surface, radius=2)
            d.line(80, 69, 80, 60, width=4)
        else:
            d.ellipse(80, 135, 45, 9, fill=d.surface)
            d.rect(73, 68, 14, 64, fill=d.surface, radius=2)
            d.circle(80, 113, 4, fill=d.ink)
            d.poly([(87, 119), (133, 119), (139, 130)], width=4)
            d.anchors["gas_inlet"] = (139, 130)
        if p.get("lit", False):
            d.path("M 80 14 C 61 35 65 50 78 60 C 99 54 102 37 89 27 Q 87 43 80 14 Z", fill=d.gold, color=d.gold)
            d.path("M 80 35 Q 72 49 80 60 Q 89 53 80 35 Z", fill=d.blue, color=d.blue)
        d.anchors.update({"flame_tip": (80, 14 if p.get("lit") else 60), "support_bottom": (80, 144)})
    elif variant in {"tripod", "stand", "iron_ring", "clamp", "tube_holder", "burette_clamp", "tube_rack", "wire_mesh"}:
        if variant == "tripod":
            d.ellipse(80, 35, 48, 10)
            d.line(36, 39, 24, 140, width=4)
            d.line(123, 39, 137, 140, width=4)
            d.line(80, 45, 80, 121, width=3)
            d.anchors.update({"support_top": (80, 25), "support_bottom": (80, 140)})
        elif variant == "wire_mesh":
            d.poly([(23, 60), (118, 39), (144, 95), (48, 119)], closed=True, fill=d.surface)
            for i in range(1, 7):
                t = i / 7
                d.line(23 + 95*t, 60 - 21*t, 48 + 96*t, 119 - 24*t, color=d.muted, width=.7)
                d.line(23 + 25*t, 60 + 59*t, 118 + 26*t, 39 + 56*t, color=d.muted, width=.7)
            d.ellipse(83, 78, 31, 21, fill="#fff", color=d.muted)
            d.anchors["support_top"] = (83, 78)
        elif variant == "stand":
            d.poly([(24, 130), (112, 130), (140, 143), (43, 143)], closed=True, fill=d.surface)
            d.rect(47, 14, 6, 119, fill=d.surface)
            d.rect(42, 50, 16, 12, fill=d.surface)
            d.line(55, 56, 119, 56, width=3)
            d.path("M 119 48 Q 135 48 135 63 Q 119 70 110 62")
            d.anchors.update({"clamp": (121, 57), "support_bottom": (80, 143)})
        elif variant == "tube_rack":
            d.rect(22, 58, 116, 10, fill=d.surface)
            d.rect(22, 127, 116, 10, fill=d.surface)
            d.line(28, 68, 28, 127, width=4)
            d.line(132, 68, 132, 127, width=4)
            for x in [43, 68, 93, 118]:
                d.ellipse(x, 62, 7, 3, fill="#fff")
        elif variant == "iron_ring":
            d.ellipse(94, 84, 43, 16)
            d.line(51, 84, 17, 84, width=4)
            d.anchors["support_top"] = (94, 68)
        else:
            d.line(20, 80, 88, 80, width=5)
            d.rect(26, 70, 13, 20, fill=d.surface)
            d.path("M 88 80 L 116 55 L 139 57 M 88 80 L 116 105 L 139 103", width=4)
            d.circle(87, 80, 6, fill=d.surface)
            d.anchors["grip"] = (129, 80)
    elif variant in {"funnel", "thistle_funnel", "separatory_funnel", "dropping_funnel", "buchner_funnel", "filter_paper"}:
        if variant == "filter_paper":
            d.circle(80, 80, 57, fill=d.surface)
            d.line(23, 80, 137, 80, dashed=True)
            d.line(80, 23, 80, 137, dashed=True)
        elif variant in {"separatory_funnel", "dropping_funnel"}:
            d.path("M 70 24 C 31 47 42 84 73 105 L 73 129 L 87 129 L 87 105 C 118 84 129 47 90 24 Z", fill=d.glass)
            d.ellipse(80, 24, 14, 4, fill=d.surface)
            d.rect(69, 14, 22, 9, fill=d.surface, radius=2)
            d.line(62, 118, 102, 118, width=3)
            d.circle(80, 118, 5, fill=d.surface)
            d.line(80, 129, 80, 145, width=3)
        elif variant == "buchner_funnel":
            d.rect(42, 27, 76, 63, fill=d.surface)
            d.line(42, 76, 118, 76, dashed=True)
            d.poly([(42, 90), (74, 103), (74, 143), (86, 143), (86, 103), (118, 90)])
            d.ellipse(80, 27, 38, 7, fill="#fff")
        elif variant == "thistle_funnel":
            d.path("M 49 25 Q 49 50 75 52 L 75 145 L 85 145 L 85 52 Q 111 50 111 25", fill=d.glass)
            d.ellipse(80, 25, 31, 7, fill=d.glass)
        else:
            y = 37 if variant == "funnel" else 23
            d.poly([(28, y), (73, 89), (73, 143), (87, 143), (87, 89), (132, y)], fill=d.glass)
            d.ellipse(80, y, 52, 9, fill=d.glass)
        d.anchors.update({"mouth": (80, 27), "tube_outlet": (80, 143)})
    elif variant in {"pipette", "graduated_pipette", "dropper", "burette", "base_burette", "micropipette", "thermometer", "glass_rod", "pestle", "spatula", "combustion_spoon"}:
        if variant == "micropipette":
            d.rect(61, 25, 38, 65, fill=d.surface, radius=8)
            d.rect(72, 10, 16, 15, fill=d.blue, radius=3)
            d.rect(66, 43, 28, 20, fill="#fff", radius=2)
            d.poly([(71, 90), (89, 90), (85, 132), (80, 148), (75, 132)], closed=True, fill=d.glass)
        elif variant == "dropper":
            d.path("M 67 17 Q 80 1 93 17 L 93 43 Q 80 55 67 43 Z", fill=d.surface)
            d.poly([(74, 49), (86, 49), (84, 124), (80, 144), (76, 124)], closed=True, fill=d.glass)
        elif variant == "pipette":
            d.path("M 76 13 L 76 61 C 53 70 53 94 76 103 L 77 139 L 80 150 L 83 139 L 84 103 C 107 94 107 70 84 61 L 84 13 Z", fill=d.glass)
            d.line(74, 37, 86, 37, color=d.blue)
        elif variant == "thermometer":
            d.rect(74, 14, 12, 112, fill=d.glass, radius=6)
            d.circle(80, 132, 12, fill=d.red)
            reading = p.get("reading", 25)
            d.line(80, 125, 80, 116 - (reading + 20) * .8, color=d.red, width=4)
            for i in range(11):
                d.line(88, 116-i*8, 99 if i % 2 == 0 else 95, 116-i*8, width=1)
                if p.get("scale_labels", False) and i % 2 == 0:
                    d.text(str(i*10-20), 105, 120-i*8, size=12, anchor="start")
            d.facts["reading"] = reading
        elif variant == "glass_rod":
            d.rect(75, 13, 10, 134, fill=d.glass, radius=5)
            d.line(78, 21, 78, 140, color="#fff", width=1.3)
        elif variant in {"pestle", "spatula", "combustion_spoon"}:
            d.line(83, 22, 67, 114, width=7 if variant == "pestle" else 3)
            d.ellipse(66, 125, 12 if variant == "pestle" else 16, 14 if variant == "pestle" else 8, fill=d.surface)
        else:
            d.rect(75, 13, 10, 112, fill=d.glass, radius=2)
            d.poly([(75, 125), (80, 145), (85, 125)])
            if variant in {"burette", "base_burette", "graduated_pipette"}:
                for i in range(15):
                    d.line(76, 23 + i*6, 84 if i % 5 == 0 else 80, 23+i*6, width=.8)
                if variant == "base_burette":
                    d.rect(75, 117, 10, 25, fill=d.surface, radius=3)
                    d.circle(80, 128, 4, fill=d.glass)
                elif variant != "graduated_pipette":
                    d.line(62, 126, 101, 126, width=3)
                    d.circle(80, 126, 5, fill=d.surface)
        d.anchors.update({"tip": (80, 145), "mouth": (80, 13), "tube_outlet": (80, 145)})
    elif variant in {"condenser", "allihn_condenser", "graham_condenser", "air_condenser"}:
        d.rect(57, 20, 46, 116, fill=d.glass, radius=10)
        if variant == "allihn_condenser":
            for y in [41, 64, 87, 110]:
                d.ellipse(80, y, 15, 11)
            d.line(80, 9, 80, 30)
            d.line(80, 121, 80, 149)
        elif variant == "graham_condenser":
            points = [(80 + 15*math.sin(i*math.pi/4), 27+i*2.7) for i in range(39)]
            d.poly(points)
            d.line(80, 9, 80, 27)
            d.line(80, 130, 80, 149)
        else:
            d.rect(75, 9, 10, 140, fill="none")
        if variant != "air_condenser":
            d.rect(36, 112, 21, 7, fill=d.glass)
            d.rect(103, 36, 21, 7, fill=d.glass)
        d.anchors.update({"tube_inlet": (80, 9), "tube_outlet": (80, 149),
                          "water_inlet": (36, 115.5), "water_outlet": (124, 39.5)})
    elif variant in {"stopper", "one_hole_stopper", "two_hole_stopper", "lamp_cap"}:
        d.poly([(43, 47), (117, 47), (103, 122), (57, 122)], closed=True, fill=d.surface)
        d.ellipse(80, 47, 37, 8, fill=d.surface)
        for x in ([80] if variant == "one_hole_stopper" else [67, 93] if variant == "two_hole_stopper" else []):
            d.ellipse(x, 47, 5, 3, fill="#fff")
            d.anchors[f"hole_{int(x)}"] = (x, 47)
    elif variant in {"tube", "bent_tube", "u_tube", "tee_tube", "hose"}:
        points = {"tube": [(20, 80), (140, 80)], "bent_tube": [(30, 130), (30, 40), (140, 40)],
                  "u_tube": [(40, 20), (40, 112), (55, 133), (105, 133), (120, 112), (120, 20)],
                  "tee_tube": [(20, 80), (140, 80)], "hose": [(20, 100), (45, 40), (90, 125), (140, 45)]}[variant]
        d.poly(points, color=d.muted, width=7)
        d.poly(points, color=d.glass, width=3)
        if variant == "tee_tube":
            d.line(80, 80, 80, 20, width=6)
            d.anchors["branch"] = (80, 20)
        d.anchors.update({"tube_inlet": points[0], "tube_outlet": points[-1]})
    elif variant in {"tongs", "tweezers", "tube_brush", "stir_bar", "filter_fold", "weighing_paper"}:
        if variant == "stir_bar":
            d.rect(23, 65, 114, 26, fill=d.surface, radius=12)
            d.line(80, 65, 80, 91, color=d.muted)
        elif variant in {"weighing_paper", "filter_fold"}:
            d.poly([(25, 32), (135, 40), (121, 132), (20, 122)], closed=True, fill=d.surface)
            d.line(25, 32, 121, 132, color=d.muted, dashed=True)
        elif variant == "tube_brush":
            d.line(80, 31, 80, 145, color=d.muted, width=2)
            d.circle(80, 22, 9, width=2)
            for y in range(73, 133, 5):
                d.line(67, y-4, 93, y+4, color=d.gold, width=2)
        elif variant == "tweezers":
            d.path("M 76 17 Q 66 66 43 143 M 84 17 Q 94 66 117 143 M 76 17 Q 80 13 84 17", width=4)
        else:
            d.path("M 48 135 Q 20 84 70 24 Q 80 14 90 24 Q 140 84 112 135", width=3)
    return d


def mechanics(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    angle = math.radians(p.get("angle", 25))
    if variant in {"block", "metal_block", "cart", "collision_cart"}:
        d.rect(30, 45, 100, 64, fill=d.surface, radius=4)
        if variant == "metal_block":
            d.line(35, 49, 124, 49, color="#fff", width=3)
        if variant in {"cart", "collision_cart"}:
            for x in [48, 112]:
                d.circle(x, 116, 14, fill="#fff")
                d.circle(x, 116, 4, fill=d.ink)
        d.anchors.update({"rope_attach": (130, 77), "support_bottom": (80, 109 if variant == "block" else 130)})
    elif variant in {"ball", "hollow_ball", "disc", "ring", "pendulum_bob"}:
        radius = p.get("radius", 34)
        d.circle(80, 85, radius, fill=d.surface)
        if variant == "hollow_ball":
            d.ellipse(80, 85, radius, radius*.26, color=d.muted)
        if variant == "ring":
            d.circle(80, 85, radius*.56, fill="#fff")
        if variant == "pendulum_bob":
            d.circle(80, 81-radius, 4, fill=d.surface)
        d.anchors.update({"rope_attach": (80, 85-radius), "center": (80, 85), "support_bottom": (80, 85+radius)})
    elif variant in {"spring", "vertical_spring", "spring_oscillator", "vertical_oscillator"}:
        length = p.get("length", 100)
        horizontal = variant not in {"vertical_spring", "vertical_oscillator"}
        pts = [(25, 80), (33, 80)] + [(33+i*length/24, 80 + (9 if i % 2 else -9)) for i in range(1, 24)] + [(33+length, 80), (41+length, 80)]
        if not horizontal:
            pts = [(80 + y-80, 12+x-25) for x, y in pts]
        d.poly(pts)
        d.anchors.update({"attach_start": pts[0], "attach_end": pts[-1]})
        if "oscillator" in variant:
            if horizontal:
                d.rect(pts[-1][0], 63, 35, 34, fill=d.surface)
            else:
                d.rect(63, pts[-1][1], 34, 30, fill=d.surface)
    elif variant in {"pendulum", "physical_pendulum", "double_pendulum"}:
        length = p.get("length", 95)
        origin = (80, 19)
        end = (80 + length*math.sin(angle), 19 + length*math.cos(angle))
        d.rect(49, 8, 62, 8, fill=d.surface)
        d.hatch(49, 8, 62, 8, spacing=10)
        d.line(*origin, 80, 141, color=d.muted, dashed=True)
        d.line(*origin, *end, width=4 if variant == "physical_pendulum" else 1.8)
        d.circle(*end, 13, fill=d.blue)
        d.circle(*origin, 3, fill=d.ink)
        if p.get("show_angle", False):
            x, y = 80+28*math.sin(angle), 19+28*math.cos(angle)
            d.path(f"M 80 47 A 28 28 0 0 {0 if angle >= 0 else 1} {num(x)} {num(y)}", color=d.gold)
            d.text(f"{p.get('angle', 25):g}°", 103, 58, size=14)
        if variant == "double_pendulum":
            d.line(*end, end[0]-28, end[1]+25)
            d.circle(end[0]-28, end[1]+25, 10, fill=d.surface)
        d.anchors.update({"pivot": origin, "bob": end, "rope_attach": origin})
        d.facts.update({"angle_degrees": p.get("angle", 25), "length": length, "bob": end})
    elif variant in {"pulley", "moving_pulley", "wheel_axle"}:
        d.circle(80, 81, 35, fill=d.surface)
        d.circle(80, 81, 13 if variant == "wheel_axle" else 26)
        d.circle(80, 81, 5, fill=d.ink)
        if variant != "moving_pulley":
            d.line(80, 46, 80, 15, width=3)
            d.path("M 70 15 Q 70 5 80 5 Q 93 5 91 18")
        d.anchors.update({"rope_left": (45, 81), "rope_right": (115, 81), "pivot": (80, 81), "hook": (80, 15)})
        if variant == "moving_pulley":
            d.line(80, 116, 80, 138)
            d.path("M 80 138 Q 98 137 91 148 Q 85 157 76 148")
            d.anchors["hook"] = (80, 148)
    elif variant in {"plane", "rough_plane", "incline", "wall", "rail", "step", "table", "lever", "rod", "plate", "weight", "hook", "rope", "slack_rope", "trajectory", "circular_track", "conveyor"}:
        if variant == "incline":
            rise = min(110, 125*math.tan(angle))
            d.poly([(15, 139), (140, 139-rise), (140, 139)], closed=True, fill=d.surface)
            d.anchors.update({"slope_left": (15, 139), "slope_right": (140, 139-rise)})
            d.facts["angle_degrees"] = p.get("angle", 25)
        elif variant in {"plane", "rough_plane", "rail", "conveyor"}:
            d.line(10, 82, 150, 82, width=3)
            d.hatch(10, 85, 140, 12)
            if variant == "rough_plane":
                for x in range(15, 150, 9):
                    d.poly([(x, 82), (x+3, 77), (x+6, 82)], width=1)
            if variant == "conveyor":
                d.rect(14, 64, 132, 30, radius=15)
                for x in [29, 131]:
                    d.circle(x, 79, 10)
            d.anchors["support_top"] = (80, 82)
        elif variant in {"lever", "rod", "plate"}:
            d.rect(13, 68, 134, 13 if variant != "plate" else 57, fill=d.surface, radius=3)
            if variant == "lever":
                d.poly([(80, 84), (60, 116), (100, 116)], closed=True, fill=d.surface)
                d.anchors["pivot"] = (80, 84)
            d.anchors.update({"attach_start": (13, 74), "attach_end": (147, 74)})
        elif variant in {"weight", "hook"}:
            if variant == "hook":
                d.circle(80, 26, 10, fill=d.surface)
                d.path("M 80 36 L 80 81 C 127 88 122 138 81 138 Q 53 138 56 111", width=5, color=d.muted)
            else:
                d.path("M 70 36 Q 70 19 83 23 Q 94 30 80 36 L 80 50")
                d.poly([(56, 50), (104, 50), (118, 132), (42, 132)], closed=True, fill=d.surface)
            d.anchors["rope_attach"] = (80, 23)
        elif variant in {"rope", "slack_rope", "trajectory"}:
            if variant == "rope":
                d.line(15, 80, 145, 80, width=1.6)
            else:
                d.path("M 15 45 Q 80 160 145 45", dashed=variant == "trajectory")
            d.anchors.update({"attach_start": (15, 80 if variant == "rope" else 45), "attach_end": (145, 80 if variant == "rope" else 45)})
        elif variant == "circular_track":
            d.circle(80, 80, 57)
            d.circle(80, 80, 50, color=d.muted)
        elif variant == "wall":
            d.line(80, 15, 80, 145, width=3)
            d.hatch(63, 15, 15, 130)
            d.anchors["fixed_end"] = (80, 80)
        elif variant == "step":
            d.poly([(12, 120), (58, 120), (58, 76), (103, 76),
                    (103, 32), (148, 32), (148, 141), (12, 141)],
                   closed=True, fill=d.surface)
            d.anchors.update({"support_lower": (35, 120), "support_middle": (80, 76),
                              "support_upper": (125, 32), "edge": (103, 76)})
        elif variant == "table":
            d.poly([(15, 55), (145, 55), (145, 134)])
            d.line(28, 55, 28, 134)
            d.anchors.update({"support_top": (80, 55), "edge": (145, 55)})
    return d
