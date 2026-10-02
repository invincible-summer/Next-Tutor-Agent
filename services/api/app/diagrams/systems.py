"""Graph layouts, chemistry structures and concrete context objects."""
from __future__ import annotations

import math

from .drawing import Drawing
from .schema import DiagramError


def graph(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(480, 300, mono)
    def label(value, x, y, width, height):
        text = str(value)
        units = sum(1 if ord(c) > 255 else .6 for c in text)
        size = min(17, max(9, (width-8)/max(1, units)))
        if units*size > width-8:
            lines = [text[i:i+4] for i in range(0, len(text), 4)]
            for i, line in enumerate(lines):
                d.text(line, x, y+(i-(len(lines)-1)/2)*11+3, size=9)
        else:
            d.text(text, x, y+size*.35, size=size)
    items = p.get("items")
    if not isinstance(items, list) or not items:
        raise DiagramError("diagram_graph_data_required")
    if variant in {"array", "stack", "queue", "circular_queue", "matrix", "table", "payoff", "place_value", "comparison"}:
        for i, item in enumerate(items):
            if variant == "stack":
                x, y, w, h = 165, 242-i*34, 140, 32
            elif variant == "circular_queue":
                a = i*2*math.pi/len(items)-math.pi/2
                x, y, w, h = 211+103*math.cos(a), 129+89*math.sin(a), 58, 34
            elif variant in {"matrix", "table", "payoff", "place_value", "comparison"}:
                columns = p.get("columns", 3)
                x, y, w, h = 36+(i % columns)*404/columns, 30+(i//columns)*39, 404/columns, 39
            else:
                x, y, w, h = 22+i*436/len(items), 118, 436/len(items), 49
            d.rect(x, y, w, h, fill=d.glass if i % 2 == 0 else d.surface, radius=0 if variant in {"matrix", "table"} else 3, width=1.4)
            label(item, x+w/2, y+h/2, w, h)
    elif variant in {"venn2", "venn3", "euler"}:
        d.rect(25, 25, 430, 250, radius=5, color=d.muted)
        for i, (x, y, rx, ry) in enumerate([(184, 128, 104, 80), (292, 128, 104, 80)] + ([(238, 192, 104, 70)] if variant == "venn3" else [])):
            d.ellipse(x, y, rx, ry, color=[d.blue, d.gold, d.green][i], width=2.4)
            if i < len(items):
                d.text(items[i], x, y-29, size=20)
    else:
        n = len(items)
        positions = []
        for i in range(n):
            if variant in {"timeline", "cashflow", "gantt", "sentence", "morpheme", "story"}:
                positions.append((38+(i+.5)*402/n, 136))
            elif variant in {"tree", "binary_tree", "heap", "probability_tree", "syntax_tree", "organization", "pedigree", "argument", "paragraph", "ecosystem"}:
                level = int(math.log2(i+1))
                atlevel = i-(2**level-1)
                positions.append((35+(atlevel+.5)*410/2**level, 39+level*62))
            else:
                a = -math.pi/2+i*2*math.pi/max(n, 1)
                positions.append((240+145*math.cos(a), 145+99*math.sin(a)))
        edges = p.get("edges")
        if edges is None:
            edges = [[(i-1)//2, i] for i in range(1, n)] if variant in {"tree", "binary_tree", "heap", "probability_tree", "syntax_tree", "organization", "pedigree", "argument", "paragraph", "ecosystem"} else [[i, i+1] for i in range(n-1)]
        for edge in edges:
            start, end = edge[:2]
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < n or not 0 <= end < n:
                raise DiagramError("diagram_invalid_graph_edge")
            x1, y1 = positions[start]
            x2, y2 = positions[end]
            dist = math.hypot(x2-x1, y2-y1) or 1
            ux, uy = (x2-x1)/dist, (y2-y1)/dist
            rect_node = variant in {"flow", "state_machine", "dataflow", "linked_list", "network", "process", "comparison", "sentence", "morpheme"}
            def trim(index):
                w = max(62, min(104, len(str(items[index]))*15+18))
                return min(w/2/max(abs(ux), 1e-9), 20/max(abs(uy), 1e-9))+2 if rect_node else 24
            a = (x1+ux*trim(start), y1+uy*trim(start))
            b = (x2-ux*trim(end), y2-uy*trim(end))
            if variant in {"undirected", "bipartite", "hasse", "tree", "binary_tree", "heap", "pedigree"}:
                d.line(*a, *b, color=d.muted)
            else:
                d.arrow(*a, *b, color=d.muted)
            if len(edge) > 2:
                d.text(edge[2], (x1+x2)/2+8, (y1+y2)/2-8, size=15)
        for i, (item, (x, y)) in enumerate(zip(items, positions)):
            if variant in {"timeline", "cashflow", "gantt", "story"}:
                d.circle(x, y, 5, fill=d.blue)
                d.line(x, y, x, y-35 if i % 2 == 0 else y+35, color=d.muted)
                d.text(item, x, y-43 if i % 2 == 0 else y+58, size=16)
            elif variant in {"flow", "state_machine", "dataflow", "class", "er", "linked_list", "double_list", "network", "process", "comparison", "sentence", "morpheme"}:
                w = max(62, min(104, len(str(item))*15+18))
                d.rect(x-w/2, y-20, w, 40, fill=d.glass, radius=6)
                label(item, x, y, w, 40)
            else:
                d.circle(x, y, 22, fill=d.glass)
                label(item, x, y, 44, 44)
    d.facts.update({"items": items, "edges": p.get("edges", [])})
    return d


def chemistry(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    if variant in {"atom", "nucleus", "proton", "neutron", "electron", "ion", "electron_pair", "isotope"}:
        if variant in {"proton", "neutron", "electron"}:
            d.circle(80, 80, 34, fill=d.red if variant == "proton" else d.blue if variant == "electron" else d.surface)
            if p.get("show_labels", False):
                d.text("+" if variant == "proton" else "−" if variant == "electron" else "0", 80, 89, size=30)
        else:
            d.circle(80, 80, 18, fill=d.gold, color=d.gold)
            if variant not in {"nucleus", "electron_pair"}:
                for radius in [37, 61]:
                    d.circle(80, 80, radius, color=d.muted, width=1)
                    for i in range(4 if radius == 61 else 2):
                        a = i*math.pi/2+.35
                        d.circle(80+radius*math.cos(a), 80+radius*math.sin(a), 4, fill=d.blue)
            if variant == "electron_pair":
                d.circle(58, 80, 5, fill=d.blue)
                d.circle(102, 80, 5, fill=d.blue)
    elif variant in {"single_bond", "double_bond", "triple_bond", "wedge", "hashed_wedge", "chain", "branched_chain", "benzene", "ring", "chair", "hydroxyl", "aldehyde", "carboxyl", "ester", "amino", "polymer"}:
        if variant in {"single_bond", "double_bond", "triple_bond"}:
            for dy in [0] if variant == "single_bond" else [-4, 4] if variant == "double_bond" else [-7, 0, 7]:
                d.line(25, 80+dy, 135, 80+dy)
        elif variant == "wedge":
            d.poly([(25, 80), (135, 67), (135, 93)], closed=True, fill=d.ink)
        elif variant == "hashed_wedge":
            for i in range(10):
                d.line(30+i*11, 80-i*1.3, 30+i*11, 80+i*1.3, width=1.6)
        elif variant in {"benzene", "ring", "chair"}:
            pts = [(80+53*math.cos(-math.pi/6+i*math.pi/3), 80+53*math.sin(-math.pi/6+i*math.pi/3)) for i in range(6)]
            if variant == "chair":
                pts = [(20, 94), (54, 59), (110, 69), (140, 46), (115, 106), (57, 98)]
            d.poly(pts, closed=True)
            if variant == "benzene":
                for i in [0, 2, 4]:
                    a, b = pts[i], pts[(i+1) % 6]
                    d.line(80+(a[0]-80)*.81, 80+(a[1]-80)*.81, 80+(b[0]-80)*.81, 80+(b[1]-80)*.81)
        else:
            d.poly([(15, 91), (44, 66), (73, 91), (102, 66), (130, 91)])
            if variant == "branched_chain":
                d.line(73, 91, 73, 131)
            groups = {"hydroxyl": "OH", "aldehyde": "CHO", "carboxyl": "COOH", "ester": "COO", "amino": "NH₂"}
            if variant in groups:
                d.text(groups[variant], 112, 45, size=17)
                d.line(102, 66, 113, 51)
            if variant == "polymer":
                d.poly([(33, 41), (26, 41), (26, 120), (33, 120)])
                d.poly([(127, 41), (134, 41), (134, 120), (127, 120)])
                d.text("n", 147, 131, size=16)
    elif variant in {"h2", "o2", "n2", "water", "co2", "ammonia", "methane", "hcl", "ethanol", "acetic_acid", "ethylene", "acetylene"}:
        layouts = {
            "h2": [(53, 80, "H"), (107, 80, "H")], "o2": [(53, 80, "O"), (107, 80, "O")],
            "n2": [(53, 80, "N"), (107, 80, "N")], "water": [(80, 65, "O"), (32, 111, "H"), (128, 111, "H")],
            "co2": [(26, 80, "O"), (80, 80, "C"), (134, 80, "O")], "ammonia": [(80, 74, "N"), (80, 23, "H"), (32, 119, "H"), (128, 119, "H")],
            "methane": [(80, 80, "C"), (80, 22, "H"), (22, 80, "H"), (80, 138, "H"), (138, 80, "H")],
            "hcl": [(48, 80, "H"), (108, 80, "Cl")],
            "ethanol": [(45, 80, "C"), (95, 80, "C"), (140, 80, "O"), (18, 43, "H"), (18, 117, "H"), (45, 20, "H"), (95, 30, "H"), (95, 130, "H"), (140, 130, "H")],
            "acetic_acid": [(40, 90, "C"), (92, 90, "C"), (136, 105, "O"), (92, 32, "O"), (141, 141, "H"), (16, 55, "H"), (16, 120, "H"), (40, 145, "H")],
            "ethylene": [(53, 80, "C"), (107, 80, "C"), (27, 34, "H"), (27, 126, "H"), (133, 34, "H"), (133, 126, "H")],
            "acetylene": [(60, 80, "C"), (100, 80, "C"), (20, 80, "H"), (140, 80, "H")]}
        atoms = layouts[variant]
        bonds = [(0, i) for i in range(1, len(atoms))] if variant in {"water", "ammonia", "methane"} else [(i, i+1) for i in range(len(atoms)-1)]
        if variant == "acetic_acid":
            bonds = [(0, 1), (1, 2), (1, 3), (2, 4), (0, 5), (0, 6), (0, 7)]
        if variant == "ethanol":
            bonds = [(0, 1), (1, 2), (0, 3), (0, 4), (0, 5), (1, 6), (1, 7), (2, 8)]
        if variant == "ethylene":
            bonds = [(0, 1), (0, 2), (0, 3), (1, 4), (1, 5)]
        if variant == "acetylene":
            bonds = [(0, 1), (0, 2), (1, 3)]
        for a, b in bonds:
            x1, y1, _ = atoms[a]
            x2, y2, _ = atoms[b]
            order = 3 if variant in {"n2", "acetylene"} and (a, b) == (0, 1) else 2 if (
                variant in {"o2", "ethylene"} and (a, b) == (0, 1) or variant == "co2" or
                variant == "acetic_acid" and (a, b) == (1, 3)) else 1
            length = math.hypot(x2-x1, y2-y1)
            for offset in [0] if order == 1 else [-4, 4] if order == 2 else [-6, 0, 6]:
                dx, dy = -(y2-y1)*offset/length, (x2-x1)*offset/length
                d.line(x1+dx, y1+dy, x2+dx, y2+dy, width=2)
        d.facts["atoms"] = [atom for _, _, atom in atoms]
        d.facts["bonds"] = bonds
        for x, y, atom in atoms:
            color = d.red if atom == "O" else d.blue if atom == "N" else d.green if atom == "Cl" else d.surface
            d.circle(x, y, 17 if atom != "H" else 12, fill=color, color=d.ink, width=1.4)
            if p.get("show_labels", False):
                d.text(atom, x, y+5, size=14)
    elif variant in {"pure", "mixture", "solution", "gas_mixture", "precipitate", "crystal", "powder", "suspension", "emulsion", "nacl", "diamond", "graphite", "metal_lattice", "bubbles", "drop", "flame", "diffusion"}:
        if variant == "drop":
            d.path("M 80 17 C 62 56 31 82 41 116 C 49 147 112 147 121 116 C 129 84 100 56 80 17 Z", fill=d.blue, color=d.blue)
            d.path("M 52 96 Q 46 119 65 125", color="#fff", width=3)
        elif variant == "flame":
            d.path("M 80 15 C 64 56 17 86 42 125 Q 80 157 118 123 C 148 73 95 51 97 34 Q 101 79 80 15 Z", fill=d.gold, color=d.gold)
            d.path("M 80 64 Q 48 120 80 139 Q 112 119 80 64 Z", fill=d.red, color=d.red)
        elif variant == "graphite":
            for dy in [0, 37, 74]:
                pts = [(26, 36+dy), (56, 19+dy), (86, 36+dy), (116, 19+dy), (143, 36+dy)]
                d.poly(pts, color=d.muted)
                for x, y in pts:
                    d.circle(x, y, 5, fill=d.ink)
        else:
            for i in range(16 if variant in {"nacl", "metal_lattice", "diamond", "crystal"} else p.get("count", 12)):
                x = 25+(i % 4)*36 if variant in {"nacl", "metal_lattice", "diamond", "crystal"} else 22+(i*37 % 119)
                y = 26+(i//4)*36 if variant in {"nacl", "metal_lattice", "diamond", "crystal"} else 24+(i*29 % 119)
                parity = (i//4+i%4) % 2 if variant == "nacl" else i % 2
                color = d.blue if variant in {"pure", "metal_lattice", "powder", "bubbles"} or parity == 0 else d.gold
                if variant in {"nacl", "diamond", "metal_lattice"}:
                    if i % 4 < 3:
                        d.line(x, y, x+36, y, color=d.muted, width=1)
                    if i < 12:
                        d.line(x, y, x, y+36, color=d.muted, width=1)
                d.circle(x, y, 5 if variant == "bubbles" else 7, fill="none" if variant == "bubbles" else color, color=color)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d


def objects(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    if variant in {"person", "hand"}:
        if variant == "person":
            d.circle(80, 27, 15, fill=d.surface)
            d.path("M 64 48 Q 80 41 96 48 L 103 99 L 91 99 L 93 145 L 81 145 L 79 107 L 70 145 L 58 145 L 65 96 L 54 96 Z", fill=d.glass)
            d.line(61, 55, 40, 87, width=7, color=d.muted)
            d.line(98, 55, 122, 85, width=7, color=d.muted)
        else:
            d.path("M 42 133 L 33 93 Q 28 77 39 76 L 56 96 L 54 32 Q 57 17 65 31 L 69 72 L 71 20 Q 81 11 86 25 L 88 72 L 94 28 Q 104 23 108 37 L 105 78 L 119 49 Q 130 43 132 58 L 120 110 Q 111 130 99 135 Z", fill=d.surface)
    elif variant in {"apple", "orange", "tree", "balloon"}:
        if variant == "tree":
            d.rect(73, 86, 15, 60, fill=d.gold, color=d.gold)
            for x, y, r in [(48, 70, 30), (105, 66, 31), (78, 39, 35), (81, 89, 33)]:
                d.circle(x, y, r, fill=d.green, color=d.green)
        elif variant == "balloon":
            d.ellipse(80, 61, 42, 49, fill=d.glass, color=d.blue)
            d.poly([(75, 109), (85, 109), (80, 118)], closed=True, fill=d.blue, color=d.blue)
            d.path("M 80 118 Q 64 135 82 151", color=d.muted, width=1)
        elif variant == "orange":
            d.circle(80, 86, 53, fill=d.gold, color=d.gold)
            d.path("M 80 32 Q 89 18 95 19", width=3)
            d.path("M 88 27 Q 110 9 124 28 Q 105 43 88 27 Z", fill=d.green, color=d.green)
            d.path("M 47 55 Q 32 80 44 102", color="#fff", width=3)
        else:
            d.path("M 78 40 C 23 19 16 106 47 134 Q 76 150 80 136 Q 85 150 113 134 C 147 107 136 16 83 40 Z", fill=d.red if variant == "apple" else d.gold, color=d.red if variant == "apple" else d.gold)
            d.path("M 80 40 Q 88 19 94 15", color=d.ink, width=3)
            d.path("M 86 28 Q 112 9 123 26 Q 101 45 86 28 Z", fill=d.green, color=d.green)
            d.path("M 45 57 Q 30 80 43 97", color="#fff", width=3)
    elif variant in {"car", "bus", "truck", "train", "bicycle", "boat"}:
        if variant == "bicycle":
            d.circle(38, 108, 28)
            d.circle(126, 108, 28)
            d.poly([(38, 108), (66, 59), (95, 108), (38, 108), (88, 66), (126, 108)], color=d.blue, width=3)
            d.line(60, 59, 76, 59, width=4)
            d.poly([(117, 106), (108, 42), (123, 39)])
        elif variant == "boat":
            d.path("M 15 104 L 145 104 L 123 133 L 43 133 Z", fill=d.surface)
            d.line(80, 104, 80, 18)
            d.poly([(80, 25), (80, 96), (134, 96)], closed=True, fill=d.glass, color=d.blue)
            d.path("M 8 145 Q 20 133 34 145 T 61 145 T 88 145 T 115 145 T 144 145", color=d.blue)
        else:
            if variant in {"bus", "train", "truck"}:
                d.rect(15, 43, 134, 68, fill=d.surface, color=d.blue, radius=5)
            else:
                d.path("M 15 111 L 15 80 Q 15 68 32 69 L 55 39 L 111 39 L 133 72 Q 149 72 149 88 L 149 111 Z", fill=d.surface, color=d.blue)
            for x in [39, 118]:
                d.circle(x, 114, 17, fill=d.ink)
                d.circle(x, 114, 7, fill=d.surface)
            for x in [46, 76, 106]:
                d.rect(x, 50, 24, 23, fill=d.glass, color=d.blue, radius=2)
            d.line(21, 89, 139, 89, color=d.muted, width=1)
    elif variant in {"book", "pen", "backpack", "box", "basket", "bag", "house", "stairs", "bridge", "pool", "tap", "ticket", "paper"}:
        if variant == "book":
            d.path("M 80 42 Q 51 21 17 38 L 17 129 Q 51 112 80 134 Q 112 112 144 129 L 144 38 Q 112 21 80 42 Z", fill=d.surface)
            d.line(80, 42, 80, 134, color=d.muted)
            for y in [60, 80, 100]:
                d.path(f"M 29 {y} Q 51 {y-6} 67 {y+3} M 94 {y+3} Q 114 {y-6} 134 {y}", color=d.muted, width=1)
        elif variant == "pen":
            d.poly([(57, 127), (115, 25), (129, 34), (71, 136), (51, 146)], closed=True, fill=d.glass, color=d.blue)
            d.poly([(57, 127), (71, 136), (51, 146)], closed=True, fill=d.gold)
        elif variant in {"backpack", "bag", "basket", "box"}:
            if variant == "box":
                d.poly([(25, 49), (110, 38), (140, 63), (52, 76)], closed=True, fill=d.surface)
                d.poly([(25, 49), (52, 76), (52, 139), (25, 112)], closed=True, fill=d.gold)
                d.poly([(52, 76), (140, 63), (140, 126), (52, 139)], closed=True, fill=d.surface)
            else:
                d.rect(30, 45, 100, 96, fill=d.glass, color=d.blue, radius=16)
                d.path("M 53 45 C 52 3 108 3 107 45", color=d.blue, width=3)
                if variant == "backpack":
                    d.rect(44, 86, 72, 43, fill=d.surface, radius=7)
                if variant == "basket":
                    for x in range(43, 126, 14):
                        d.line(x, 53, x, 134, color=d.gold, width=1)
        elif variant == "house":
            d.poly([(25, 67), (80, 18), (139, 67)], closed=True, fill=d.red, color=d.red)
            d.rect(34, 67, 95, 75, fill=d.surface)
            d.rect(73, 98, 25, 44, fill=d.gold)
            d.rect(44, 84, 22, 24, fill=d.glass, color=d.blue)
        elif variant == "stairs":
            d.poly([(12, 140), (12, 112), (45, 112), (45, 85), (79, 85), (79, 58), (113, 58), (113, 30), (147, 30), (147, 140)], closed=True, fill=d.surface)
        elif variant == "bridge":
            d.line(8, 70, 152, 70, width=5)
            d.path("M 15 134 Q 80 8 145 134", color=d.gold, width=7)
            for x in [33, 57, 81, 105, 129]:
                d.line(x, 70, x, 43+abs(x-80)*.75, color=d.muted)
        elif variant == "pool":
            d.poly([(12, 48), (111, 27), (148, 109), (41, 138)], closed=True, fill=d.glass, color=d.blue)
            for y in [72, 89, 105]:
                d.path(f"M 40 {y} Q 60 {y-9} 82 {y} T 128 {y}", color=d.blue, width=1)
        elif variant == "tap":
            d.path("M 39 125 L 39 65 Q 39 46 61 46 L 126 46 L 126 83 L 106 83 L 106 66 L 61 66 L 61 125 Z", fill=d.surface)
            d.line(74, 46, 74, 27, width=4)
            d.line(51, 27, 97, 27, width=4)
        else:
            d.rect(25, 31, 110, 101, fill=d.surface, radius=4)
            for y in [52, 76, 100]:
                d.line(42, y, 117, y, color=d.muted, width=1)
    elif variant in {"server", "computer", "router", "network_switch", "database", "gear", "gears", "bearing", "beam", "valve", "pump", "heat_exchanger", "turbine", "tank"}:
        if variant in {"gear", "gears", "bearing"}:
            if variant == "bearing":
                d.circle(80, 80, 43, fill=d.surface)
                d.circle(80, 80, 17, fill="#fff")
                for i in range(12):
                    a = i*math.pi/6
                    d.circle(80+31*math.cos(a), 80+31*math.sin(a), 7, fill=d.glass)
            else:
                gears = [(80, 80, 54, 12, 0)] if variant == "gear" else [(52, 80, 39, 12, 0), (113, 80, 24, 8, math.pi/8)]
                for cx, cy, radius, teeth, phase in gears:
                    points = []
                    for i in range(teeth):
                        for offset, r in [(0, radius-6), (.2, radius), (.55, radius), (.75, radius-6)]:
                            a = phase+(i+offset)*2*math.pi/teeth
                            points.append((cx+r*math.cos(a), cy+r*math.sin(a)))
                    d.poly(points, closed=True, fill=d.surface, width=1.7)
                    d.circle(cx, cy, radius*.42, fill="#fff", width=1.7)
                    d.circle(cx, cy, 3, fill=d.ink)
        elif variant == "computer":
            d.rect(16, 21, 128, 86, fill=d.surface, radius=6)
            d.rect(24, 29, 112, 68, fill=d.glass, color=d.blue, radius=2)
            d.line(80, 107, 80, 133, width=7, color=d.muted)
            d.line(53, 135, 107, 135, width=5)
        elif variant in {"server", "router", "network_switch"}:
            for y in ([29, 67, 105] if variant == "server" else [71]):
                d.rect(21, y, 119, 29, fill=d.surface, radius=4)
                for x in [34, 47, 60]:
                    d.circle(x, y+15, 2, fill=d.green)
                for x in [96, 108, 120]:
                    d.rect(x, y+9, 7, 11, fill=d.ink)
            if variant == "router":
                d.line(38, 71, 28, 22, width=4)
                d.line(122, 71, 132, 22, width=4)
        elif variant in {"database", "tank"}:
            d.rect(32, 42, 96, 89, fill=d.glass, color=d.blue)
            d.ellipse(80, 42, 48, 13, fill=d.surface, color=d.blue)
            d.path("M 32 130 Q 80 156 128 130", color=d.blue)
            if variant == "database":
                for y in [73, 103]:
                    d.path(f"M 32 {y} Q 80 {y+24} 128 {y}", color=d.blue)
        elif variant == "beam":
            d.rect(17, 66, 126, 24, fill=d.surface)
            for x in [26, 131]:
                d.poly([(x, 93), (x-12, 117), (x+12, 117)], closed=True, fill=d.surface)
        elif variant == "valve":
            d.poly([(23, 56), (80, 80), (23, 104)], closed=True, fill=d.surface)
            d.poly([(137, 56), (80, 80), (137, 104)], closed=True, fill=d.surface)
            d.line(80, 80, 80, 36)
            d.line(60, 36, 100, 36, width=3)
        else:
            d.circle(80, 80, 43, fill=d.glass)
            d.line(8, 80, 37, 80)
            d.line(123, 80, 152, 80)
            for i in range(4):
                a = i*math.pi/2
                d.path(f"M 80 80 Q {80+35*math.cos(a)} {80+35*math.sin(a)} {80+25*math.cos(a+.8)} {80+25*math.sin(a+.8)}", color=d.blue, width=3)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d


def logic(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    if variant in {"and", "nand"}:
        d.path("M 45 43 L 83 43 C 139 43 139 117 83 117 L 45 117 Z", fill=d.surface)
    elif variant in {"or", "nor", "xor", "xnor"}:
        d.path("M 38 43 Q 105 31 132 80 Q 105 129 38 117 Q 68 80 38 43 Z", fill=d.surface)
        if variant in {"xor", "xnor"}:
            d.path("M 29 43 Q 59 80 29 117")
    elif variant == "not":
        d.poly([(44, 43), (44, 117), (119, 80)], closed=True, fill=d.surface)
    elif variant in {"flip_flop", "register", "mux", "decoder"}:
        d.rect(42, 35, 78, 90, fill=d.surface, radius=3)
        if variant == "mux":
            d.line(80, 125, 80, 149)
        if variant == "flip_flop":
            d.poly([(42, 93), (51, 99), (42, 105)])
    else:
        raise DiagramError("diagram_unknown_variant")
    input_x = 48.2 if variant in {"or", "nor", "xor", "xnor"} else 44 if variant == "not" else 42 if variant in {"flip_flop", "mux"} else 45
    input_y = 80 if variant == "not" else 59
    d.line(10, input_y, input_x, input_y)
    if variant != "not":
        d.line(10, 101, input_x, 101)
    x = 125 if variant in {"and", "nand"} else 132 if variant in {"or", "nor", "xor", "xnor"} else 119 if variant == "not" else 120
    if variant in {"nand", "nor", "xnor", "not"}:
        d.circle(x+5, 80, 5, fill="#fff")
        x += 10
    d.line(x, 80, 150, 80)
    d.anchors.update({"input_a": (10, input_y), "output": (150, 80)})
    if variant != "not":
        d.anchors["input_b"] = (10, 101)
    return d
