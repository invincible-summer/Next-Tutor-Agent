"""Original schematic life-science and Earth-science illustrations."""
from __future__ import annotations

import math

from .drawing import Drawing
from .schema import DiagramError


def biology(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    if variant == "reflex":
        d.ellipse(115, 78, 31, 57, fill=d.surface, color=d.muted)
        d.path("M 100 44 Q 115 59 130 44 L 128 68 Q 110 80 129 91 L 130 112 Q 115 98 100 112 L 101 91 Q 117 78 101 67 Z", fill=d.glass, color=d.blue)
        d.circle(18, 35, 9, fill=d.glass, color=d.green)
        d.path("M 27 35 Q 61 21 104 51", color=d.green, width=3)
        d.arrow(82, 39, 101, 50, color=d.green)
        d.path("M 104 51 Q 125 77 104 100", color=d.blue, width=3)
        d.path("M 104 100 Q 71 141 37 126", color=d.red, width=3)
        d.arrow(61, 128, 40, 127, color=d.red)
        d.rect(10, 116, 29, 19, fill=d.surface, color=d.red, radius=8)
        return d
    if variant in {"mitosis", "meiosis"}:
        d = Drawing(480, 240, mono)
        def cell(x, y, replicated, colors, radius=36):
            d.circle(x, y, radius, fill=d.glass, color=d.blue, width=1.5)
            for i, color in enumerate(colors):
                at = x+(i-(len(colors)-1)/2)*22
                if replicated:
                    d.line(at-6, y-17, at+6, y+17, color=color, width=4)
                    d.line(at+6, y-17, at-6, y+17, color=color, width=4)
                else:
                    d.line(at, y-16, at, y+16, color=color, width=4)
                d.circle(at, y, 3, fill=d.gold, color=d.gold, width=1)
        cell(63, 120, True, [d.blue, d.red])
        d.arrow(107, 120, 169, 120, color=d.muted)
        if variant == "mitosis":
            cell(241, 65, False, [d.blue, d.red])
            cell(241, 175, False, [d.blue, d.red])
        else:
            cell(211, 65, True, [d.blue])
            cell(211, 175, True, [d.red])
            for y, color in [(65, d.blue), (175, d.red)]:
                d.arrow(254, y, 298, y, color=d.muted)
                for x in [341, 425]:
                    cell(x, y, False, [color], 31)
        return d
    if variant in {"base_pair", "replication", "transcription", "translation"}:
        if variant == "base_pair":
            for x, color in [(44, d.blue), (116, d.red)]:
                d.circle(x, 80, 15, fill=d.glass, color=color)
                d.rect(65 if x == 44 else 82, 68, 13, 24, fill=color, color=color, radius=3)
            d.line(78, 80, 82, 80, color=d.gold, dashed=True)
        elif variant == "translation":
            d.poly([(12, 100), (39, 94), (63, 102), (88, 94), (114, 100), (148, 94)], color=d.blue, width=3)
            d.ellipse(82, 84, 33, 24, fill=d.glass, color=d.gold)
            d.ellipse(82, 111, 26, 14, fill=d.surface, color=d.gold)
            d.poly([(83, 86), (83, 50), (70, 42), (83, 29), (96, 42), (83, 50)], color=d.green)
            for x, y in [(82, 19), (105, 21), (123, 35), (139, 51)]:
                d.circle(x, y, 7, fill=d.red, color=d.red)
            d.line(89, 19, 99, 21, color=d.red)
            d.line(110, 25, 119, 31, color=d.red)
            d.line(128, 39, 135, 47, color=d.red)
        else:
            d.line(72, 145, 72, 84, color=d.blue, width=3)
            d.line(88, 145, 88, 84, color=d.red, width=3)
            for y in range(94, 140, 12):
                d.line(72, y, 88, y, color=d.gold)
            d.path("M 72 84 Q 49 58 32 15", color=d.blue, width=3)
            d.path("M 88 84 Q 111 58 128 15", color=d.red, width=3)
            if variant == "replication":
                d.path("M 66 75 Q 55 48 46 12", color=d.green, width=3)
                d.path("M 94 75 Q 105 48 114 12", color=d.gold, width=3)
                for y in [30, 46, 62]:
                    t = (75-y)/63
                    d.line(66-20*t, y, 72-40*(84-y)/69, y, color=d.muted, width=1)
                    d.line(94+20*t, y, 88+40*(84-y)/69, y, color=d.muted, width=1)
            else:
                d.poly([(86, 86), (109, 97), (119, 113), (140, 122)], color=d.green, width=3)
                d.ellipse(81, 80, 23, 17, color=d.gold, width=3)
        return d
    if variant == "potometer":
        d = Drawing(240, 180, mono)
        d.rect(42, 79, 13, 48, fill=d.glass, color=d.blue, radius=4)
        d.rect(42, 120, 180, 13, fill=d.glass, color=d.blue, radius=4)
        d.rect(40, 74, 17, 8, fill=d.surface, radius=2)
        d.path("M 48 80 Q 45 41 70 18", color=d.green, width=4)
        d.path("M 53 56 Q 18 24 24 54 Q 33 68 53 56 Z M 57 35 Q 83 11 90 31 Q 80 48 57 35 Z", fill=d.glass, color=d.green)
        d.ellipse(153, 126, 6, 4, fill="#fff", color=d.blue, width=1)
        for x in range(96, 210, 12):
            d.line(x, 113, x, 119, color=d.muted, width=1)
        return d
    if variant == "respirometer":
        d.path("M 45 32 L 45 64 L 20 132 Q 20 142 30 142 L 102 142 Q 112 142 110 132 L 84 64 L 84 32 Z", fill=d.glass)
        d.rect(44, 25, 41, 9, fill=d.surface, radius=2)
        for x, y in [(43, 115), (57, 125), (72, 117), (88, 129)]:
            d.ellipse(x, y, 6, 4, fill=d.gold, color=d.gold)
        d.poly([(65, 25), (65, 14), (139, 14), (139, 134)], color=d.blue, width=3)
        d.rect(128, 84, 24, 54, fill=d.glass, color=d.blue)
        d.rect(129, 114, 22, 23, fill=d.blue, color=d.blue)
        return d
    if variant == "culture_flask":
        d.path("M 24 50 L 117 34 L 137 21 L 145 36 L 124 50 L 134 121 L 37 138 Z", fill=d.glass, color=d.blue)
        d.poly([(132, 19), (141, 13), (154, 36), (145, 42)], closed=True, fill=d.surface)
        d.path("M 40 118 L 124 102 L 128 116 L 42 132 Z", fill=d.glass, color=d.green)
        return d
    if variant in {"animal_cell", "plant_cell", "bacterium", "virus", "membrane", "cell_wall", "nucleus", "vacuole", "mitochondrion", "chloroplast", "ribosome", "er", "golgi"}:
        if variant == "mitochondrion":
            d.ellipse(80, 80, 65, 34, fill=d.glass, color=d.blue)
            d.path("M 24 79 C 30 55 50 55 54 68 L 57 92 Q 65 103 69 73 Q 75 51 79 82 Q 84 107 93 72 Q 101 54 110 78 Q 123 99 135 77", color=d.blue)
        elif variant == "chloroplast":
            d = Drawing(240, 160, mono)
            membrane = d.ink if mono else d.green
            d.ellipse(120, 80, 106, 60, fill="#e4efe8" if not mono else d.glass, color=membrane)
            d.ellipse(120, 80, 99, 53, color=membrane, width=1)
            # Lamellae join stacks without crossing the double envelope.
            d.line(69, 83, 102, 83, color=membrane, width=2)
            d.line(138, 92, 172, 74, color=membrane, width=2)
            for x, y in [(51, 66), (120, 75), (190, 57)]:
                for i in range(4):
                    d.rect(x-18, y+i*6, 36, 5, fill=d.surface, color=membrane, radius=2, width=1)
            d.facts["structure"] = ["double_envelope", "grana", "stroma_lamellae", "stroma"]
        elif variant in {"membrane", "cell_wall"}:
            for i in range(12):
                x = 15+i*12
                d.circle(x, 55, 4, fill=d.blue)
                d.circle(x, 103, 4, fill=d.blue)
                d.line(x-2, 59, x-2, 77, color=d.gold, width=1)
                d.line(x+2, 59, x+2, 77, color=d.gold, width=1)
                d.line(x-2, 99, x-2, 81, color=d.gold, width=1)
                d.line(x+2, 99, x+2, 81, color=d.gold, width=1)
            if variant == "cell_wall":
                d.rect(10, 27, 140, 18, fill=d.surface)
                d.hatch(10, 27, 140, 18)
        elif variant == "golgi":
            for i in range(5):
                d.path(f"M {32-i*2} {42+i*13} Q 80 {61+i*13} {128+i*2} {42+i*13}", width=5, color=d.gold)
            for x, y in [(25, 38), (140, 55), (29, 119), (135, 129)]:
                d.circle(x, y, 6, fill=d.glass)
        elif variant == "er":
            for y in [35, 55, 75, 95, 115]:
                d.path(f"M 25 {y} C 45 {y-15} 70 {y+15} 90 {y} S 124 {y-13} 140 {y}", color=d.blue, width=3)
                for x in [44, 83, 125]:
                    d.circle(x, y-4, 2, fill=d.ink)
        elif variant in {"nucleus", "vacuole", "ribosome"}:
            d.ellipse(80, 80, 48, 42, fill=d.glass, color=d.blue)
            if variant == "nucleus":
                d.circle(86, 83, 13, fill=d.gold)
                d.path("M 50 62 Q 75 41 104 69 M 54 98 Q 86 119 115 97", color=d.muted)
            if variant == "ribosome":
                d.ellipse(80, 111, 31, 17, fill=d.gold)
        elif variant == "virus":
            d.poly([(80, 18), (117, 41), (117, 83), (80, 104), (43, 83), (43, 41)], closed=True, fill=d.glass, color=d.blue)
            d.path("M 65 47 Q 94 34 91 67 Q 52 78 84 86", color=d.gold)
            d.line(80, 104, 80, 132, width=4)
            for end in [(45, 150), (80, 151), (115, 150)]:
                d.poly([(80, 132), end])
        else:
            if variant == "plant_cell":
                d.rect(16, 18, 128, 124, fill=d.surface, radius=16, color=d.green, width=4)
                d.rect(23, 25, 114, 110, fill="#eff6ef" if not mono else "#fff", radius=13, color=d.green)
                d.ellipse(80, 84, 34, 39, fill=d.glass, color=d.blue)
                for x, y in [(37, 52), (117, 51), (38, 113), (120, 115)]:
                    d.ellipse(x, y, 8, 13, fill=d.green, color=d.green)
                d.circle(106, 93, 14, fill=d.gold)
            elif variant == "bacterium":
                d.rect(26, 48, 106, 61, fill=d.glass, radius=29, color=d.green, width=3)
                d.path("M 51 73 Q 84 51 109 75 Q 88 95 61 87 Q 99 77 66 68", color=d.gold)
                d.path("M 131 80 Q 151 70 144 116 Q 137 139 154 150", color=d.green)
                for i in range(7):
                    x = 42+i*12
                    d.line(x, 48, x-4, 35, color=d.green, width=1)
                    d.line(x, 109, x+4, 122, color=d.green, width=1)
            else:
                d.path("M 20 88 C 4 40 48 13 83 22 C 146 14 158 75 133 116 C 115 149 46 155 20 88 Z", fill=d.glass, color=d.blue)
                d.ellipse(76, 75, 24, 21, fill=d.surface, color=d.gold)
                d.circle(80, 78, 7, fill=d.gold)
                for x, y, r in [(40, 68, -20), (113, 92, 30), (68, 122, 10)]:
                    d.ellipse(x, y, 12, 6, fill=d.green, color=d.green)
                    d.path(f"M {x-7} {y} Q {x} {y-5} {x+7} {y}", color="#fff", width=1)
    elif variant in {"red_cell", "white_cell", "neuron", "muscle", "stomata", "leaf", "leaf_section", "root", "stem", "flower", "seed", "root_tip"}:
        if variant == "red_cell":
            d.ellipse(80, 80, 55, 35, fill=d.red, color=d.red)
            d.ellipse(80, 80, 26, 16, fill="#e9ada6" if not mono else d.surface, color=d.red)
        elif variant == "white_cell":
            d.circle(80, 80, 53, fill=d.glass)
            for x, y in [(61, 75), (88, 57), (96, 94)]:
                d.ellipse(x, y, 17, 14, fill=d.blue, color=d.blue)
        elif variant == "neuron":
            d.path("M 42 65 Q 24 80 42 98 Q 62 104 68 88 Q 87 78 68 62 Z", fill=d.glass, color=d.blue)
            d.circle(51, 79, 9, fill=d.gold)
            d.line(68, 80, 134, 80, color=d.blue)
            for x in [82, 104, 124]:
                d.rect(x, 72, 14, 16, fill=d.surface, radius=5)
            for y in [25, 45, 115, 141]:
                d.poly([(40, 76), (24, y), (12, y-7)], color=d.blue)
            for y in [55, 80, 107]:
                d.poly([(134, 80), (145, y), (155, y)], color=d.blue)
        elif variant == "muscle":
            d.rect(15, 45, 130, 69, fill=d.surface, radius=24)
            for x in range(30, 136, 9):
                d.line(x, 48, x, 111, color=d.red, width=1)
            for y in [58, 80, 101]:
                d.line(21, y, 139, y, color=d.muted, width=1)
        elif variant == "stomata":
            d.path("M 69 26 C 22 39 24 123 69 136 Q 41 79 69 26 Z", fill=d.green, color=d.green)
            d.path("M 91 26 C 138 39 136 123 91 136 Q 119 79 91 26 Z", fill=d.green, color=d.green)
            for x, y in [(51, 48), (41, 79), (54, 113), (108, 48), (120, 79), (107, 113)]:
                d.circle(x, y, 4, fill=d.gold)
        elif variant in {"leaf", "leaf_section"}:
            if variant == "leaf":
                d.path("M 29 131 C 6 73 39 22 137 20 C 146 104 83 159 29 131 Z", fill="#e4efe8" if not mono else d.glass, color=d.green)
                d.line(17, 144, 125, 33, color=d.green, width=3)
                for i in range(5):
                    x, y = 40+i*15, 119-i*16
                    d.line(x, y, x-8, y-32, color=d.green, width=1)
                    d.line(x, y, x+34, y+4, color=d.green, width=1)
            else:
                for x in range(15, 143, 16):
                    d.rect(x, 31, 15, 18, fill=d.surface, width=1)
                    d.rect(x, 53, 15, 42, fill=d.glass, radius=6, width=1)
                    d.circle(x+7, 66, 3, fill=d.green)
                    d.circle(x+7, 82, 3, fill=d.green)
                    d.ellipse(x+6, 109+(x % 3)*7, 9, 12, fill=d.glass, width=1)
                    d.rect(x, 136, 15, 11, fill=d.surface, width=1)
        elif variant == "flower":
            for i in range(5):
                a = i*2*math.pi/5
                d.ellipse(80+27*math.cos(a), 66+27*math.sin(a), 21, 24, fill="#f0dedb" if not mono else d.glass, color=d.red)
            d.circle(80, 66, 17, fill=d.gold)
            d.line(80, 93, 80, 149, color=d.green, width=3)
            d.path("M 80 127 Q 109 101 124 114 Q 101 137 80 127", fill=d.green, color=d.green)
        elif variant == "seed":
            d.path("M 40 30 C 12 55 20 128 73 140 C 148 151 148 34 99 26 Q 72 18 40 30 Z", fill=d.surface, color=d.gold)
            d.path("M 82 40 Q 100 69 76 90 Q 50 103 77 125", color=d.green, width=4)
            d.ellipse(69, 69, 21, 30, fill=d.glass, color=d.green)
        else:
            d.line(80, 15, 80, 121, color=d.green, width=8)
            if variant in {"root", "root_tip"}:
                d.path("M 80 57 Q 54 83 29 128 M 80 71 Q 106 91 135 140 M 80 96 L 67 148", color=d.gold, width=3)
                if variant == "root_tip":
                    d.ellipse(80, 123, 12, 22, fill=d.glass)
            else:
                for y in [45, 88, 123]:
                    d.path(f"M 80 {y} Q 115 {y-35} 139 {y-22} Q 122 {y+2} 80 {y} Z", fill=d.glass, color=d.green)
    elif variant in {"heart", "lungs", "alveoli", "digestion", "kidney", "nephron", "reflex", "ear", "joint"}:
        if variant == "heart":
            d.path("M 80 145 C 34 112 10 58 35 35 Q 64 14 80 42 Q 102 12 130 37 C 151 65 126 113 80 145 Z", fill=d.surface, color=d.red)
            d.path("M 38 46 Q 56 29 71 49 L 69 68 L 40 65 Z", fill=d.blue, color=d.blue)
            d.path("M 41 78 L 70 78 L 71 126 Q 43 102 41 78 Z", fill=d.blue, color=d.blue)
            d.path("M 90 47 Q 114 30 130 47 L 127 69 L 93 68 Z", fill=d.red, color=d.red)
            d.path("M 91 78 L 127 78 Q 113 115 88 134 Z", fill=d.red, color=d.red)
            d.line(79, 55, 79, 130, color=d.muted, width=3)
            d.path("M 79 62 L 79 15 Q 100 4 113 23", color=d.red, width=8)
            d.path("M 62 62 L 62 17", color=d.blue, width=7)
        elif variant in {"lungs", "alveoli"}:
            if variant == "lungs":
                d.path("M 72 42 C 45 7 15 49 20 119 Q 36 143 65 115 Z", fill=d.glass, color=d.blue)
                d.path("M 88 42 C 115 7 145 49 140 119 Q 124 143 95 115 Z", fill=d.glass, color=d.blue)
                d.line(80, 8, 80, 72, color=d.muted, width=8)
                for end in [(43, 90), (38, 64), (115, 90), (124, 64)]:
                    d.line(80, 70, *end, color=d.muted, width=3)
            else:
                for x, y in [(50, 53), (82, 40), (112, 58), (125, 91), (101, 117), (65, 117), (40, 89)]:
                    d.circle(x, y, 22, fill=d.glass, color=d.blue)
                d.line(80, 20, 80, 84, color=d.muted, width=7)
                d.path("M 18 50 C 11 150 154 151 147 57", color=d.red, width=3)
        elif variant in {"kidney", "nephron"}:
            if variant == "kidney":
                d.path("M 80 24 C 14 2 8 145 80 139 Q 129 136 108 94 Q 71 86 106 62 Q 118 35 80 24 Z", fill=d.surface, color=d.red)
                d.path("M 55 46 Q 23 86 57 121 L 83 104 L 60 85 L 82 63 Z", fill=d.glass, color=d.gold)
                d.line(104, 92, 119, 145, color=d.gold, width=5)
            else:
                d.circle(42, 43, 24, fill=d.glass)
                d.path("M 25 41 Q 56 12 53 43 Q 31 70 27 39", color=d.red)
                d.path("M 61 46 C 87 23 89 76 72 66 L 72 126 Q 97 150 100 126 L 100 76 Q 128 44 137 75 L 137 149", color=d.gold, width=4)
        elif variant == "digestion":
            d.line(80, 10, 80, 57, color=d.red, width=6)
            d.path("M 80 56 C 133 36 133 90 104 91 Q 71 103 68 69", fill=d.surface, color=d.red)
            d.path("M 38 65 L 38 135 L 125 135 L 125 99", color=d.gold, width=9)
            for y in [91, 107, 123]:
                d.path(f"M 51 {y} Q 84 {y-15} 115 {y} Q 84 {y+13} 51 {y}", color=d.red, width=3)
        elif variant == "ear":
            d.path("M 57 20 C 18 16 10 79 41 102 Q 56 143 76 123 Q 88 116 85 90 Q 65 69 56 85", fill=d.surface)
            d.path("M 53 43 Q 28 63 49 86 L 107 86", color=d.gold, width=4)
            d.path("M 111 87 C 147 39 161 113 121 120 Q 104 117 124 100", color=d.blue, width=3)
        else:
            d.path("M 58 17 Q 80 8 98 19 L 91 64 Q 116 78 97 91 L 64 91 Q 45 77 68 64 Z", fill=d.surface)
            d.path("M 64 96 Q 45 104 65 115 L 62 145 L 98 145 L 94 115 Q 112 101 94 96 Z", fill=d.surface)
            d.path("M 53 72 Q 32 93 57 116 M 104 73 Q 130 95 103 114", color=d.blue, width=3)
    elif variant in {"dna", "rna", "base_pair", "chromosome", "homologous", "chromatids", "mitosis", "meiosis", "replication", "transcription", "translation"}:
        if variant in {"chromosome", "homologous", "chromatids", "mitosis", "meiosis"}:
            centers = [80] if variant in {"chromosome", "chromatids"} else [53, 108]
            if variant in {"mitosis", "meiosis"}:
                d.ellipse(80, 80, 69, 57, fill=d.glass)
            for i, x in enumerate(centers):
                color = d.blue if i == 0 else d.red
                d.line(x-18, 33, x+18, 127, width=9, color=color)
                d.line(x+18, 33, x-18, 127, width=9, color=color)
                d.circle(x, 80, 6, fill=d.gold, color=d.gold)
        else:
            for i in range(24):
                y = 13+i*5.5
                x = 80+33*math.sin(i*.38)
                if i:
                    previous = 80+33*math.sin((i-1)*.38)
                    d.line(previous, y-5.5, x, y, color=d.blue, width=3)
                    if variant != "rna":
                        d.line(160-previous, y-5.5, 160-x, y, color=d.red, width=3)
                if i % 2 == 0:
                    d.line(x, y, 160-x if variant != "rna" else x+18, y, color=d.gold, width=2)
            if variant in {"replication", "transcription", "translation"}:
                d.arrow(16, 145, 145, 145, color=d.muted)
    elif variant in {"microscope", "slide", "coverslip", "inoculation_loop", "dialysis_bag", "respirometer", "potometer", "quadrat", "culture_flask", "germination", "phototropism", "osmosis", "plasmolysis", "enzyme"}:
        if variant == "microscope":
            d.path("M 91 34 C 140 44 139 104 103 126", width=14, color=d.muted)
            d.rect(60, 17, 27, 47, fill=d.surface, radius=3)
            d.rect(63, 8, 21, 10, fill=d.ink, radius=2)
            d.poly([(56, 61), (90, 61), (99, 82), (82, 87), (65, 77)], closed=True, fill=d.surface)
            d.rect(27, 94, 87, 8, fill=d.surface)
            d.circle(121, 80, 11, fill=d.surface)
            d.ellipse(65, 119, 19, 10, fill=d.glass)
            d.path("M 103 121 L 108 140 L 21 140 Q 16 130 30 127 Z", fill=d.surface)
        elif variant in {"slide", "coverslip"}:
            d.poly([(19, 60), (125, 34), (145, 93), (39, 119)], closed=True, fill=d.glass, color=d.blue)
            if variant == "slide":
                d.poly([(66, 62), (94, 55), (103, 82), (75, 89)], closed=True, fill="#fff", color=d.blue)
                d.ellipse(83, 73, 8, 5, fill=d.green)
        elif variant == "inoculation_loop":
            d.line(80, 70, 80, 147, color=d.muted, width=5)
            d.line(80, 42, 80, 70, width=1)
            d.circle(80, 32, 11, width=1)
        elif variant == "quadrat":
            d.rect(18, 18, 124, 124, color=d.gold, width=4)
            for i in range(1, 4):
                d.line(18+i*31, 18, 18+i*31, 142, color=d.muted, width=1)
                d.line(18, 18+i*31, 142, 18+i*31, color=d.muted, width=1)
        elif variant in {"germination", "phototropism"}:
            d.poly([(35, 99), (125, 99), (112, 144), (48, 144)], closed=True, fill=d.surface)
            d.path("M 79 107 Q 79 69 97 46", color=d.green, width=3)
            d.path("M 90 59 Q 44 29 45 57 Q 66 77 90 59 Z", fill=d.green, color=d.green)
            d.path("M 94 49 Q 111 21 134 28 Q 133 55 94 49 Z", fill=d.glass, color=d.green)
            if variant == "phototropism":
                d.circle(146, 14, 8, fill=d.gold, color=d.gold)
                d.arrow(142, 30, 113, 42, color=d.gold, width=1)
        elif variant in {"osmosis", "plasmolysis"}:
            d.rect(22, 24, 116, 112, radius=9, fill=d.surface, color=d.green)
            d.rect(42 if variant == "plasmolysis" else 28, 43 if variant == "plasmolysis" else 30, 76 if variant == "plasmolysis" else 104, 73 if variant == "plasmolysis" else 100, fill=d.glass, color=d.blue, radius=13)
            d.circle(105 if variant == "osmosis" else 91, 92, 10, fill=d.gold)
        elif variant == "enzyme":
            d.path("M 24 73 Q 34 31 74 33 L 80 56 L 98 45 Q 145 55 135 103 Q 109 136 55 123 Q 16 110 24 73 Z", fill=d.glass, color=d.blue)
            d.poly([(75, 16), (88, 6), (101, 20), (82, 32)], closed=True, fill=d.gold)
        else:
            d.path("M 45 41 L 45 120 Q 80 143 115 120 L 115 41", fill=d.glass)
            d.line(45, 40, 115, 40)
            d.rect(63, 24, 34, 17, fill=d.surface)
            if variant == "dialysis_bag":
                d.path("M 65 53 Q 45 113 80 125 Q 115 113 95 53 Z", fill=d.surface, color=d.blue, dashed=True)
            else:
                d.poly([(80, 24), (80, 12), (137, 12), (137, 120)])
    else:
        raise DiagramError("diagram_unknown_variant")
    return d


def earth(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    if variant in {"eclipse", "lunar_eclipse"}:
        d.circle(23, 80, 18, fill=d.gold, color=d.gold)
        if variant == "eclipse":
            d.poly([(77, 71), (134, 80), (77, 89)], closed=True, fill=d.surface, color=d.surface)
            d.circle(131, 80, 16, fill=d.glass, color=d.blue)
            d.circle(77, 80, 9, fill=d.muted, color=d.muted)
        else:
            d.poly([(83, 64), (155, 66), (155, 94), (83, 96)], closed=True, fill=d.surface, color=d.surface)
            d.circle(83, 80, 16, fill=d.glass, color=d.blue)
            d.circle(139, 80, 7, fill=d.red, color=d.red)
        return d
    if variant in {"groundwater", "runoff"}:
        d.poly([(12, 62), (148, 112), (148, 146), (12, 146)], closed=True, fill=d.surface, color=d.gold)
        if variant == "runoff":
            d.arrow(35, 62, 129, 96, color=d.blue, width=3)
            for x in [42, 78, 111]:
                d.line(x, 13+(x-42)*.3, x-5, 31+(x-42)*.3, color=d.blue)
        else:
            d.path("M 14 114 Q 64 91 146 121 L 146 146 L 14 146 Z", fill=d.glass, color=d.blue)
            d.arrow(28, 54, 28, 104, color=d.blue)
            d.arrow(46, 130, 130, 130, color=d.blue)
        return d
    if variant == "saddle":
        for offset in [0, 10]:
            d.path(f"M {13+offset} 80 C {13+offset} {18+offset} {51-offset} {18+offset} 80 {53+offset} C {109+offset} {18+offset} {147-offset} {18+offset} {147-offset} 80 C {147-offset} {142-offset} {109+offset} {142-offset} 80 {107-offset} C {51-offset} {142-offset} {13+offset} {142-offset} {13+offset} 80 Z", color=d.gold, width=1.4)
        for x in [42, 118]:
            d.ellipse(x, 80, 16, 24, color=d.gold, width=1.4)
            d.ellipse(x, 80, 8, 12, color=d.gold, width=1.4)
        return d
    if variant == "fault":
        for i, color in enumerate([d.surface, d.gold, d.glass]):
            y = 24+i*30
            d.poly([(12, y), (91-(y-24)*.3, y), (82-(y-24)*.3, y+30), (12, y+30)], closed=True, fill=color, width=1)
            d.poly([(90-(y-24)*.3, y+20), (148, y+20), (148, y+50), (81-(y-24)*.3, y+50)], closed=True, fill=color, width=1)
        d.line(94, 14, 57, 146, color=d.red, width=2)
        return d
    if variant == "water_cycle":
        from .templates import template
        return template("water_cycle", p, mono)
    if variant in {"sun", "earth", "moon", "globe", "latitude", "longitude", "day_night", "earth_layers"}:
        if variant == "sun":
            d.circle(80, 80, 42, fill=d.gold, color=d.gold)
            for i in range(12):
                a = i*math.pi/6
                d.line(80+49*math.cos(a), 80+49*math.sin(a), 80+64*math.cos(a), 80+64*math.sin(a), color=d.gold)
        elif variant == "moon":
            d.circle(80, 80, 52, fill=d.surface)
            for x, y, r in [(56, 58, 9), (104, 63, 12), (82, 113, 7), (48, 99, 5)]:
                d.circle(x, y, r, color=d.muted, width=1)
        else:
            d.circle(80, 80, 55, fill=d.glass, color=d.blue)
            if variant in {"earth", "day_night"}:
                d.path("M 43 39 L 65 32 L 85 48 L 77 66 L 55 69 L 45 90 L 31 69 Z M 83 93 L 113 83 L 128 105 L 108 121 L 87 112 Z", fill=d.green, color=d.green, width=1)
            if variant in {"globe", "latitude", "longitude"}:
                for ry in [15, 35]:
                    d.ellipse(80, 80, 55, ry, color=d.muted, width=1)
                for rx in [18, 38]:
                    d.ellipse(80, 80, rx, 55, color=d.muted, width=1)
                if variant == "latitude":
                    d.ellipse(80, 80, 55, 15, color=d.blue, width=3)
                if variant == "longitude":
                    d.ellipse(80, 80, 18, 55, color=d.blue, width=3)
            if variant == "day_night":
                d.path("M 80 25 A 55 55 0 0 1 80 135 Z", fill=d.ink, color=d.ink)
            if variant == "earth_layers":
                for r, color in [(44, d.gold), (31, d.red), (15, d.gold)]:
                    d.circle(80, 80, r, fill=color, color=color)
    elif variant in {"orbit", "seasons", "moon_phase", "eclipse", "lunar_eclipse"}:
        d.ellipse(80, 80, 65, 47, color=d.muted, width=1, fill="none")
        d.circle(80, 80, 19, fill=d.gold, color=d.gold)
        for i in range(4 if variant == "seasons" else 1):
            a = i*math.pi/2
            x, y = 80+65*math.cos(a), 80+47*math.sin(a)
            d.circle(x, y, 11, fill=d.glass, color=d.blue)
            d.line(x-4, y+16, x+4, y-16, color=d.muted, width=1)
        if variant in {"eclipse", "lunar_eclipse"}:
            d.circle(117, 80, 5, fill=d.surface)
    elif variant in {"mountain", "valley", "ridge", "saddle", "contour", "terrain_profile", "river", "watershed", "volcano", "strata", "fold", "fault", "plate_boundary", "subduction", "coast"}:
        if variant in {"contour", "ridge", "saddle", "watershed"}:
            for i in range(5):
                d.ellipse(80, 80, 67-i*9 if variant == "ridge" else 61-i*10, 29-i*5 if variant == "ridge" else 46-i*7, color=d.gold, width=1.4)
            if variant == "watershed":
                d.path("M 34 32 Q 110 61 71 137", color=d.blue, width=3)
            if variant == "saddle":
                d.ellipse(43, 80, 18, 12, color=d.gold)
                d.ellipse(117, 80, 18, 12, color=d.gold)
        elif variant in {"strata", "fold", "fault", "subduction", "plate_boundary"}:
            for i, color in enumerate([d.surface, d.gold, d.glass, d.surface]):
                y = 36+i*26
                if variant == "fold":
                    d.path(f"M 12 {y+18} C 45 {y-20} 70 {y-20} 89 {y+12} S 132 {y+34} 148 {y} L 148 {y+21} C 130 {y+55} 110 {y+50} 89 {y+33} S 45 {y} 12 {y+39} Z", fill=color, width=1)
                else:
                    d.rect(12, y, 136, 26, fill=color, width=1)
            if variant in {"fault", "subduction", "plate_boundary"}:
                d.line(90, 27, 51, 148, color=d.red, width=3)
        elif variant == "river":
            d.path("M 76 13 C 12 36 145 67 63 87 C 11 116 129 114 96 147", color=d.blue, width=6)
            d.path("M 27 32 L 59 47 M 131 89 L 93 94", color=d.blue, width=2)
        elif variant == "coast":
            d.path("M 12 76 Q 45 52 71 82 T 148 93 L 148 146 L 12 146 Z", fill=d.glass, color=d.blue)
            d.path("M 12 76 Q 45 52 71 82 T 148 93", color=d.gold, width=3)
        else:
            pts = [(10, 131), (52, 38), (82, 84), (115, 51), (150, 131)]
            if variant == "valley":
                pts = [(10, 52), (77, 125), (150, 47)]
            d.poly(pts+[(150, 143), (10, 143)], closed=True, fill=d.surface, color=d.gold)
            if variant == "volcano":
                d.path("M 51 42 Q 73 68 94 51", color=d.red, width=4)
                d.path("M 69 31 Q 39 6 75 7 Q 110 2 100 31", fill=d.surface, color=d.muted)
    elif variant in {"cloud", "rain", "evaporation", "condensation", "runoff", "groundwater", "convection", "sea_breeze", "valley_breeze", "cold_front", "warm_front", "wind", "water_cycle"}:
        if variant in {"cloud", "rain", "condensation"}:
            d.path("M 29 96 C 3 81 15 59 37 60 C 36 20 87 19 98 46 C 124 26 150 60 130 81 Q 160 105 120 111 L 40 111 Q 15 111 29 96 Z", fill=d.surface, color=d.muted)
            if variant == "rain":
                for x in [43, 74, 105, 132]:
                    d.line(x, 120, x-7, 138, color=d.blue)
        elif variant in {"cold_front", "warm_front"}:
            d.line(13, 90, 147, 90, color=d.blue if variant == "cold_front" else d.red, width=3)
            for x in [32, 70, 108]:
                if variant == "cold_front":
                    d.poly([(x, 90), (x+14, 68), (x+28, 90)], closed=True, fill=d.blue, color=d.blue)
                else:
                    d.path(f"M {x} 90 A 14 14 0 0 1 {x+28} 90 Z", fill=d.red, color=d.red)
        else:
            d.rect(12, 119, 136, 24, fill=d.glass, color=d.blue)
            if variant in {"evaporation", "groundwater", "runoff"}:
                for x in [38, 80, 122]:
                    d.arrow(x, 114, x, 38, color=d.blue)
            else:
                d.path("M 28 111 C 2 24 153 21 137 111", color=d.blue, width=2)
                d.arrow(29, 110, 22, 78, color=d.blue)
                d.arrow(138, 83, 137, 110, color=d.blue)
    elif variant in {"north_arrow", "scale_bar", "map_pin", "route", "region", "migration"}:
        if variant == "north_arrow":
            d.poly([(80, 19), (116, 127), (80, 100), (44, 127)], closed=True, fill=d.surface)
            d.poly([(80, 19), (80, 100), (44, 127)], closed=True, fill=d.blue)
        elif variant == "scale_bar":
            for i in range(4):
                d.rect(15+i*32, 68, 32, 18, fill=d.ink if i % 2 == 0 else "#fff")
        elif variant == "map_pin":
            d.path("M 80 144 C 60 112 32 87 36 59 C 43 13 117 13 124 59 C 128 87 100 112 80 144 Z", fill=d.red, color=d.red)
            d.circle(80, 62, 19, fill="#fff", color="#fff")
        elif variant == "region":
            d.poly([(22, 47), (70, 18), (110, 34), (145, 72), (119, 132), (64, 144), (20, 105)], closed=True, fill=d.glass, color=d.blue)
        else:
            d.path("M 15 120 C 35 21 65 146 101 58 Q 126 13 145 45", color=d.blue, dashed=True)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
