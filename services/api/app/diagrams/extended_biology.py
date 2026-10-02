"""Independent biological section, interaction and experimental illustrations."""

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
    sequence,
    numeric,
)
from .schema import DiagramError


def parameters(v):
    if v == "electrophoresis":
        return {"lanes": {"type": "list", "required": True}}
    if v == "mark_recapture":
        return (
            numeric("marked", 20, 1, 100, True)
            | numeric("sample", 30, 1, 100, True)
            | numeric("recaptured", 5, 1, 100, True)
        )
    if v == "niche_resources":
        return {"points": {"type": "list", "required": True}}
    return {}


def _cell(d, x, y, rx=42, ry=32, fill=None):
    d.ellipse(x, y, rx, ry, fill=fill or d.glass)
    d.ellipse(x - 7, y + 3, rx * 0.28, ry * 0.36, fill=d.red, width=1)


def _dna(d, x, y, h=110, w=28, pairs=7):
    a = []
    b = []
    for i in range(81):
        t = i / 80
        dx = w * math.sin(t * math.pi * 3)
        a.append((x + dx, y + t * h))
        b.append((x - dx, y + t * h))
    d.poly(a, color=d.blue)
    d.poly(b, color=d.red)
    for i in range(pairs):
        t = (i + 0.5) / pairs
        dx = w * math.sin(t * math.pi * 3)
        d.line(x - dx, y + t * h, x + dx, y + t * h, color=d.gold, width=1.5)


def _chromosome(d, x, y, col):
    d.path(
        f"M {x-12} {y-43} Q {x-22} {y-31} {x-3} {y} Q {x-24} {y+30} {x-12} {y+43} M {x+12} {y-43} Q {x+22} {y-31} {x+3} {y} Q {x+24} {y+30} {x+12} {y+43}",
        color=col,
        width=8,
    )
    d.circle(x, y, 5, fill=d.ink)


def draw(v, p, mono=False):
    d = canvas(mono)
    if v == "flower_section":
        d.path(
            "M 232 275 L 228 215 Q 180 195 140 158 Q 120 100 171 108 Q 200 125 228 174 L 232 122 Q 207 90 219 62 Q 240 40 261 62 Q 273 90 248 122 L 251 174 Q 283 117 318 104 Q 356 114 335 158 Q 296 194 253 214 L 248 275 Z",
            fill=d.glass,
        )
        d.path(
            "M 232 213 L 234 91 Q 220 76 233 70 Q 244 62 251 73 L 244 93 L 246 214",
            fill=d.green,
        )
        d.ellipse(240, 220, 24, 29, fill=d.green)
        for x, y in [(232, 213), (247, 223), (237, 233)]:
            d.circle(x, y, 5, fill=d.gold)
        for x, top in [(184, 113), (201, 90), (281, 90), (298, 113)]:
            d.line(x, top + 13, 225 if x < 240 else 255, 203, color=d.gold, width=3)
            d.ellipse(x, top, 9, 15, fill=d.gold)
        for a, b, label in [
            ((250, 73), (372, 53), "stigma"),
            ((261, 220), (383, 244), "ovary"),
            ((185, 110), (78, 82), "anther"),
        ]:
            d.line(*a, *b, color=d.muted, width=1)
            text(d, label, b[0], b[1] - 8, size=12)
    elif v in {"dicot_seed", "monocot_seed"}:
        if v == "dicot_seed":
            d.path(
                "M 237 45 C 60 24 84 292 225 272 Q 306 236 255 168 Q 307 85 237 45 Z",
                fill=d.gold,
            )
            d.path(
                "M 231 57 C 81 50 105 274 224 259 Q 265 225 235 187 Q 270 89 231 57 Z",
                fill=d.surface,
            )
            d.path(
                "M 224 173 Q 202 105 226 83 Q 246 133 242 175 L 245 214 Q 226 242 227 263",
                fill=d.green,
            )
            text(d, "two cotyledons", 359, 125)
            d.line(215, 120, 306, 120, color=d.muted)
        else:
            d.path(
                "M 240 35 C 125 42 122 271 231 286 C 359 279 355 51 240 35 Z",
                fill=d.gold,
            )
            d.path(
                "M 237 51 C 151 55 153 257 233 270 C 332 261 332 67 237 51 Z",
                fill=d.surface,
            )
            d.path("M 213 210 Q 190 117 224 126 Q 257 184 223 240 Z", fill=d.green)
            d.path(
                "M 214 221 Q 226 181 207 172 M 213 219 Q 218 246 205 263",
                color=d.green,
                width=5,
            )
            text(d, "endosperm", 367, 119)
            d.line(280, 119, 319, 119, color=d.muted)
        text(d, "seed coat", 75, 281)
        d.line(165, 242, 91, 258, color=d.muted)
    elif v == "xylem_phloem":
        for x, col in [(135, d.blue), (335, d.green)]:
            d.rect(x - 35, 40, 70, 235, fill=d.glass)
            for y in range(50, 276, 45):
                d.line(x - 35, y, x + 35, y, color=col, width=3)
                if x > 200:
                    for dx in [-21, -7, 7, 21]:
                        d.circle(x + dx, y, 2, fill=d.surface, width=0.5)
            d.arrow(x, 245, x, 65, color=col, width=4)
        text(d, "xylem: water", 135, 304)
        text(d, "phloem: source → sink", 335, 304, size=12)
        d.facts["phloem_direction"] = "depends_on_source_and_sink"
    elif v == "root_hair":
        d.path(
            "M 70 90 L 230 90 Q 270 100 286 128 L 390 128 Q 421 137 390 148 L 286 148 Q 270 172 230 180 L 70 180 Z",
            fill=d.glass,
        )
        d.ellipse(153, 133, 44, 31, fill=d.surface)
        d.circle(217, 134, 12, fill=d.red)
        for x, y in [(325, 76), (395, 204), (295, 219), (422, 99), (350, 240)]:
            d.poly(
                [(x - 15, y - 10), (x + 11, y - 13), (x + 20, y + 7), (x - 4, y + 15)],
                closed=True,
                fill=d.gold,
                width=1,
            )
        d.arrow(375, 190, 327, 148, color=d.blue)
        text(d, "H₂O + minerals", 321, 282)
    elif v == "leaf_exchange":
        d.rect(55, 95, 370, 45, fill=d.green)
        for i in range(7):
            d.rect(59 + i * 52, 100, 48, 35, fill=d.glass, width=1)
        for x, y in [(85, 185), (155, 177), (236, 191), (316, 174), (384, 196)]:
            _cell(d, x, y, 27, 20, d.green)
        d.rect(55, 238, 145, 25, fill=d.green)
        d.rect(282, 238, 143, 25, fill=d.green)
        for x in [222, 260]:
            d.ellipse(x, 250, 12, 20, fill=d.glass)
        d.arrow(239, 296, 239, 210, color=d.blue)
        d.arrow(265, 215, 285, 293, color=d.red)
        text(d, "CO₂", 217, 310, size=12)
        text(d, "O₂ / H₂O", 321, 301, size=12)
    elif v == "annual_rings":
        for r, col in [
            (125, d.muted),
            (116, d.gold),
            (102, d.surface),
            (86, d.gold),
            (70, d.surface),
            (52, d.gold),
            (31, d.surface),
        ]:
            d.ellipse(240, 160, r, r * 0.91, fill=col, width=1)
        d.circle(240, 160, 9, fill=d.red)
        d.line(240, 160, 355, 160, color=d.ink, width=1)
        for r in [31, 52, 70, 86, 102]:
            d.line(240 + r, 155, 240 + r, 165, width=1)
        text(d, "growth rings", 240, 306)
    elif v == "asexual_reproduction":
        for cx in [100, 370]:
            plant(d, cx, 220, 92)
        d.path("M 100 215 Q 230 265 370 216", color=d.green, width=4)
        for x in [100, 240, 370]:
            for dx in [-20, -8, 9, 23]:
                d.line(x, 220, x + dx, 264, color=d.gold, width=1.5)
        plant(d, 240, 230, 45)
        d.line(35, 225, 440, 225, color=d.muted, dashed=True)
        text(d, "stolon", 240, 292)
    elif v == "vessels_comparison":
        for x, w, label in [
            (105, 23, "artery"),
            (240, 8, "capillary"),
            (375, 15, "vein"),
        ]:
            d.rect(x - 45, 60, 90, 185, fill=d.red if x < 200 else d.glass, radius=25)
            d.rect(x - w, 60, 2 * w, 185, fill=d.surface, radius=10)
            if x > 300:
                for y in [115, 190]:
                    d.poly([(x - w, y - 12), (x, y), (x + w, y - 12)], color=d.blue)
            d.arrow(x, 228, x, 82, color=d.blue, width=2)
            text(d, label, x, 285)
    elif v == "circulations":
        d.path(
            "M 225 118 C 162 76 142 171 239 226 C 346 164 323 77 257 118 Z", fill=d.red
        )
        d.line(240, 117, 240, 211)
        d.line(197, 159, 286, 159)
        panel(d, 180, 28, 120, 46, d.glass)
        text(d, "lungs", 240, 57)
        panel(d, 140, 263, 200, 38, d.surface)
        text(d, "body", 240, 289)
        for points, col in [
            ([(219, 127), (114, 127), (114, 50), (180, 50)], d.blue),
            ([(300, 50), (370, 50), (370, 137), (266, 137)], d.red),
            ([(267, 188), (401, 188), (401, 282), (340, 282)], d.red),
            ([(140, 282), (80, 282), (80, 189), (211, 189)], d.blue),
        ]:
            d.poly(points, color=col, width=4)
            d.arrow(*points[-2], *points[-1], color=col, width=4)
    elif v == "alveolar_exchange":
        d.path(
            "M 218 33 L 218 100 C 84 71 104 263 238 256 C 374 266 392 77 257 100 L 257 33",
            fill=d.glass,
        )
        d.path(
            "M 129 99 C 72 115 85 290 240 288 C 407 291 410 127 346 99",
            color=d.red,
            width=14,
        )
        for x, y in [(126, 160), (158, 258), (273, 280), (363, 221)]:
            d.ellipse(x, y, 8, 5, fill=d.red, width=1)
        d.arrow(205, 214, 183, 261, color=d.red, width=3)
        d.arrow(298, 259, 279, 215, color=d.blue, width=3)
        text(d, "O₂", 170, 239)
        text(d, "CO₂", 311, 229)
    elif v == "eye_refraction":
        d.ellipse(276, 160, 130, 100, fill=d.surface)
        d.path("M 178 99 Q 134 159 178 222", fill=d.glass, color=d.blue)
        d.ellipse(199, 160, 17, 49, fill=d.glass)
        d.path("M 370 101 Q 413 160 371 219", color=d.red, width=5)
        for y in [130, 160, 190]:
            d.poly([(35, y), (198, y), (390, 160)], color=d.gold, width=1.5)
        d.line(390, 160, 443, 182, color=d.red, width=4)
        text(d, "retina", 398, 272)
    elif v == "skin_layers":
        d.rect(55, 65, 370, 30, fill=d.gold)
        d.rect(55, 95, 370, 125, fill=d.glass)
        d.rect(55, 220, 370, 65, fill=d.surface)
        for x in range(68, 421, 30):
            d.path(f"M {x} 94 Q {x+9} 111 {x+18} 94", color=d.gold, width=2)
        d.path("M 166 266 Q 119 137 170 111 Q 191 172 184 247", fill=d.muted)
        d.line(176, 243, 157, 31, width=5)
        d.path(
            "M 294 209 C 265 189 319 169 293 154 C 266 129 325 117 296 111 L 299 66",
            color=d.blue,
            width=3,
        )
        for x in range(80, 401, 38):
            d.ellipse(x, 250, 15, 19, fill=d.gold, width=1)
        d.poly([(350, 267), (345, 167), (368, 134)], color=d.red, width=3)
        text(d, "epidermis", 75, 47, size=12)
        text(d, "dermis", 377, 157, size=12)
        text(d, "hypodermis", 372, 310, size=12)
    elif v == "antagonistic_muscles":
        d.poly([(130, 65), (172, 180), (330, 228)], color=d.surface, width=8)
        d.circle(172, 180, 15, fill=d.surface)
        d.path("M 133 82 Q 194 98 179 169 Q 148 143 133 82 Z", fill=d.red)
        d.path("M 121 83 Q 113 153 162 184 Q 132 130 121 83 Z", fill=d.blue)
        d.line(179, 162, 202, 188, color=d.red, width=3)
        d.line(157, 171, 165, 190, color=d.blue, width=3)
        text(d, "flexor", 274, 89)
        d.line(172, 123, 231, 85, color=d.muted)
        text(d, "extensor", 62, 220)
        d.line(138, 137, 60, 195, color=d.muted)
        d.path("M 290 253 Q 353 207 324 175", color=d.gold)
    elif v == "immune_cells":
        _cell(d, 105, 155, 64, 56)
        d.path("M 64 142 Q 111 95 139 151 Q 101 183 64 142 Z", fill=d.red)
        _cell(d, 240, 155, 59, 58)
        d.circle(239, 155, 42, fill=d.red)
        _cell(d, 378, 155, 60, 55)
        d.path(
            "M 345 146 Q 355 117 373 137 Q 385 121 402 149 Q 386 178 373 159 Q 357 180 345 146 Z",
            fill=d.red,
        )
        for x, label in [(105, "macrophage"), (240, "lymphocyte"), (378, "neutrophil")]:
            text(d, label, x, 262, size=12)
        for x, y in [(96, 158), (78, 169), (118, 146)]:
            ball(d, x, y, 5, d.gold)
    elif v == "synapse":
        d.path(
            "M 190 30 L 190 97 Q 114 145 135 211 L 345 211 Q 367 145 290 97 L 290 30",
            fill=d.glass,
        )
        d.path("M 110 250 Q 240 233 370 250 L 370 280 L 110 280 Z", fill=d.green)
        for x, y in [(190, 155), (244, 172), (294, 145)]:
            d.circle(x, y, 15, fill=d.surface)
            for dx, dy in [(-5, -3), (5, -3), (0, 6)]:
                d.circle(x + dx, y + dy, 3, fill=d.gold, width=0.5)
        for x in [193, 240, 283]:
            d.circle(x, 222, 3, fill=d.gold, width=1)
            d.path(
                f"M {x-7} 247 L {x-7} 236 L {x+7} 236 L {x+7} 247",
                color=d.blue,
                width=3,
            )
        d.arrow(240, 44, 240, 115, color=d.blue)
        text(d, "synaptic cleft", 393, 224, size=11)
    elif v in {"crossing_over", "gene_linkage"}:
        if v == "crossing_over":
            _chromosome(d, 95, 157, d.blue)
            _chromosome(d, 151, 157, d.red)
            connect(d, (200, 157), (259, 157))
            _chromosome(d, 319, 157, d.blue)
            _chromosome(d, 375, 157, d.red)
            d.line(308, 185, 305, 196, color=d.red, width=8)
            d.line(386, 185, 389, 196, color=d.blue, width=8)
            d.path("M 145 154 Q 182 200 100 194", color=d.gold, dashed=True)
            text(d, "homologous exchange", 240, 272)
        else:
            for x, col in [(185, d.blue), (290, d.red)]:
                d.rect(x, 40, 25, 240, fill=col, radius=12)
                for y, label in [
                    (80, "A" if x < 200 else "a"),
                    (155, "B" if x < 200 else "b"),
                    (240, "C" if x < 200 else "c"),
                ]:
                    d.line(x - 12, y, x + 37, y)
                    text(d, label, x - 29, y + 5)
            d.arrow(350, 80, 350, 155, double=True, color=d.gold)
            d.arrow(350, 155, 350, 240, double=True, color=d.gold)
    elif v == "mendel":
        for i, letter in enumerate(["A", "a"]):
            text(d, letter, 220 + i * 85, 77)
            text(d, letter, 139, 139 + i * 85)
        for row in range(2):
            for col in range(2):
                genotype = ["AA", "Aa", "Aa", "aa"][row * 2 + col]
                d.rect(
                    180 + col * 85,
                    95 + row * 85,
                    85,
                    85,
                    fill=d.glass if genotype != "aa" else d.gold,
                )
                text(d, genotype, 222 + col * 85, 145 + row * 85, size=20)
        text(d, "Aa × Aa", 265, 308)
        d.facts["example_cross"] = "Aa x Aa"
    elif v == "sex_linkage":
        for row in range(2):
            for col in range(2):
                label = [["XᴬXᴬ", "XᴬY"], ["XᴬXᵃ", "XᵃY"]][row][col]
                d.rect(
                    135 + col * 115,
                    100 + row * 75,
                    115,
                    75,
                    fill=d.red if label == "XᵃY" else d.glass,
                )
                text(d, label, 192 + col * 115, 145 + row * 75, size=18)
        for x, label in [(192, "Xᴬ"), (307, "Y")]:
            text(d, label, x, 78)
        text(d, "Xᴬ", 95, 145)
        text(d, "Xᵃ", 95, 220)
        text(d, "example: XᴬXᵃ × XᴬY", 240, 295)
    elif v == "pcr":
        for i, (x, label) in enumerate(
            [(80, "denature"), (240, "anneal"), (400, "extend")]
        ):
            if i == 0:
                for xx, col in [(x - 18, d.blue), (x + 18, d.red)]:
                    d.poly(
                        [(xx + 4 * math.sin(j / 3), 72 + j * 7) for j in range(16)],
                        color=col,
                        width=3,
                    )
            else:
                for xx, col in [(x - 18, d.blue), (x + 18, d.red)]:
                    d.line(xx, 70, xx, 180, color=col, width=3)
                d.line(x - 18, 165, x - 2, 165, color=d.gold, width=5)
                d.line(x + 2, 85, x + 18, 85, color=d.gold, width=5)
                if i == 2:
                    d.line(x - 2, 75, x - 2, 165, color=d.red, width=3)
                    d.line(x + 2, 85, x + 2, 178, color=d.blue, width=3)
                    for y in range(90, 164, 15):
                        d.line(x - 18, y, x - 2, y, color=d.muted, width=1)
                        d.line(x + 2, y, x + 18, y, color=d.muted, width=1)
            text(d, label, x, 232, size=13)
            if i:
                connect(d, (x - 120, 125), (x - 40, 125))
        d.path("M 414 253 Q 240 316 66 253", color=d.gold)
        text(d, "repeat", 240, 298, size=12)
    elif v == "electrophoresis":
        lanes = p["lanes"]
        if not 2 <= len(lanes) <= 8 or any(
            not isinstance(a, list)
            or not 1 <= len(a) <= 10
            or any(type(n) not in {int, float} or not 50 <= n <= 10000 for n in a)
            for a in lanes
        ):
            raise DiagramError("diagram_invalid_bands")
        panel(d, 85, 55, 330, 235, d.glass)
        spacing = 280 / len(lanes)
        for i, bands in enumerate(lanes):
            x = 110 + (i + 0.5) * spacing
            d.rect(x - 12, 70, 24, 9, fill=d.muted, width=1)
            for size in bands:
                y = 105 + 160 * (math.log(10000 / size) / math.log(200))
                d.line(x - 12, y, x + 12, y, color=d.blue, width=4)
            text(d, str(i + 1), x, 47, size=11)
        text(d, "−", 56, 80, size=22)
        text(d, "+", 56, 272, size=22)
        d.arrow(48, 105, 48, 250, color=d.gold)
        d.facts.update(
            fragment_sizes_bp=lanes,
            migration="smaller_fragments_further_toward_positive",
        )
    elif v == "restriction_sites":
        for y, sequence_text in [(95, "G A A T T C"), (146, "C T T A A G")]:
            for i, c in enumerate(sequence_text.split()):
                text(d, c, 105 + i * 54, y, size=22)
        d.poly([(125, 72), (125, 120), (342, 120), (342, 168)], color=d.red, width=3)
        d.arrow(240, 180, 240, 232, color=d.gold)
        text(d, "5′ sticky overhang: AATT", 240, 282)
        text(d, "5′", 60, 95)
        text(d, "3′", 420, 95)
        text(d, "3′", 60, 146)
        text(d, "5′", 420, 146)
    elif v == "plasmid":
        d.circle(220, 160, 106, color=d.blue, width=4)
        d.circle(220, 160, 97, color=d.blue, width=2)
        d.path("M 220 54 A 106 106 0 0 1 320 125", color=d.red, width=7)
        d.path("M 160 247 A 106 106 0 0 1 114 160", color=d.green, width=7)
        text(d, "insert", 380, 89)
        d.line(288, 88, 344, 88, color=d.muted)
        text(d, "marker", 68, 291)
        d.line(141, 230, 68, 268, color=d.muted)
        text(d, "ori", 300, 292)
        d.circle(280, 247, 5, fill=d.gold)
    elif v == "transect":
        d.rect(40, 55, 400, 215, fill=d.surface)
        d.line(60, 160, 422, 160, color=d.red, dashed=True)
        for x in [75, 185, 295]:
            d.rect(x, 95, 95, 120, color=d.blue, width=2)
        for i in range(19):
            x = 60 + (i * 67) % 352
            y = 75 + (i * 43) % 178
            leaf(d, x, y, w=18, h=8, rotate=(i * 41) % 360)
        text(d, "fixed sampling intervals", 240, 303)
    elif v == "mark_recapture":
        m, n, r = p["marked"], p["sample"], p["recaptured"]
        if r > min(m, n):
            raise DiagramError("diagram_invalid_recapture")
        for x, label, count in [
            (90, "mark", m),
            (245, "sample", n),
            (400, "recapture", r),
        ]:
            d.path(
                f"M {x-48} 79 Q {x} 39 {x+48} 79 L {x+40} 215 Q {x} 240 {x-40} 215 Z",
                fill=d.glass,
            )
            for i in range(8):
                xx = x - 25 + (i % 3) * 25
                yy = 103 + (i // 3) * 35
                d.ellipse(
                    xx, yy, 9, 5, fill=d.gold if x != 245 or i < 2 else d.blue, width=1
                )
            text(d, label, x, 263)
            text(d, str(count), x, 286)
        d.facts.update(marked=m, sample=n, recaptured=r, estimate=m * n / r)
    elif v == "serial_dilution":
        for i, x in enumerate([70, 180, 290, 400]):
            vessel(
                d,
                x - 25,
                100,
                50,
                130,
                0.65,
                color=[d.ink, d.blue, d.glass, d.surface][i],
            )
            text(d, ["stock", "10⁻¹", "10⁻²", "10⁻³"][i], x, 276)
            if i:
                d.arrow(x - 80, 74, x - 25, 74, color=d.gold)
                text(d, "1 + 9", x - 55, 55, size=11)
        text(d, "equal transfer + diluent volumes", 240, 310, size=12)
    elif v == "niche_resources":
        points = p["points"]
        if (
            len(points) < 3
            or len(points) > 30
            or any(
                not isinstance(a, list)
                or len(a) != 3
                or any(type(q) not in {int, float} or not 0 <= q <= 1 for q in a)
                for a in points
            )
        ):
            raise DiagramError("diagram_invalid_niche_data")
        xy = axes(d, x_label="resource", y_label="use")
        for col, color in [(1, d.blue), (2, d.red)]:
            d.poly([xy(a[0], a[col]) for a in points], color=color)
        d.facts["data_source"] = "task_parameters"
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
