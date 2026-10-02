"""Project-authored algorithm diagrams, using supplied structures and traces."""

import heapq
import math

from .extended_common import canvas, text, panel, node, connect, numeric, box, values
from .schema import DiagramError

GRAPH_VARIANTS = {
    "adjacency",
    "dfs",
    "bfs",
    "shortest_path",
    "topological",
    "spanning_tree",
    "union_find",
}


def parameters(v):
    if v in GRAPH_VARIANTS:
        return {
            "items": {"type": "list", "required": True},
            "edges": {"type": "list", "required": True},
            "start": {"type": "integer", "default": 0, "minimum": 0, "maximum": 7},
        }
    if v in {"hash_chain", "open_addressing", "b_tree", "red_black"}:
        return {"values": {"type": "list", "required": True}}
    if v == "trie":
        return {"words": {"type": "list", "required": True}}
    if v == "round_robin":
        return {
            "values": {"type": "list", "required": True},
            **numeric("quantum", 2, 1, 4, True),
        }
    if v == "convolution":
        return {
            "input": {"type": "list", "required": True},
            "kernel": {"type": "list", "required": True},
        }
    if v == "confusion_matrix":
        return {"values": {"type": "list", "required": True}}
    if v == "neural_network":
        return {"layers": {"type": "list", "default": [3, 4, 2]}}
    return {}


def _graph(p):
    labels = p["items"]
    edges = p["edges"]
    n = len(labels)
    if (
        not 2 <= n <= 7
        or any(not isinstance(a, str) or not 1 <= len(a) <= 6 for a in labels)
        or len(edges) > 18
        or p["start"] >= n
    ):
        raise DiagramError("diagram_invalid_graph")
    if any(
        not isinstance(e, list)
        or len(e) not in {2, 3}
        or any(type(k) is not int or not 0 <= k < n for k in e[:2])
        or e[0] == e[1]
        or len(e) == 3
        and (type(e[2]) not in {int, float} or not 0 < e[2] <= 100)
        for e in edges
    ):
        raise DiagramError("diagram_invalid_graph")
    return labels, edges


def _layout_tree(d, root, children, label, col=lambda a: None):
    def leaves(a):
        return sum(leaves(b) for b in children(a)) or 1

    def place(a, left, right, depth):
        x = (left + right) / 2
        y = 48 + depth * 61
        if depth > 3:
            raise DiagramError("diagram_tree_too_deep")
        node(
            d, label(a), x, y, w=min(78, right - left - 4), h=32, fill=col(a) or d.glass
        )
        cs = children(a)
        total = sum(leaves(b) for b in cs)
        cursor = left
        for b in cs:
            end = cursor + (right - left) * leaves(b) / total
            bx = (cursor + end) / 2
            d.line(x, y + 17, bx, y + 44, color=d.muted, width=1.5)
            place(b, cursor, end, depth + 1)
            cursor = end

    place(root, 35, 445, 0)


def _btree(nums):
    root = {"keys": [], "children": []}

    def split(parent, i):
        child = parent["children"][i]
        right = {"keys": child["keys"][2:], "children": child["children"][2:]}
        middle = child["keys"][1]
        child["keys"] = child["keys"][:1]
        child["children"] = child["children"][:2]
        parent["keys"].insert(i, middle)
        parent["children"].insert(i + 1, right)

    def insert(a, value):
        i = len(a["keys"])
        if not a["children"]:
            a["keys"].append(value)
            a["keys"].sort()
            return
        while i and value < a["keys"][i - 1]:
            i -= 1
        if len(a["children"][i]["keys"]) == 3:
            split(a, i)
            if value > a["keys"][i]:
                i += 1
        insert(a["children"][i], value)

    for value in nums:
        if len(root["keys"]) == 3:
            root = {"keys": [], "children": [root]}
            split(root, 0)
        insert(root, value)
    return root


def _red_black(nums):
    # Left-leaning red/black insertion: links, recoloring and rotations are real.
    def red(a):
        return a is not None and a["red"]

    def rotate_left(a):
        b = a["right"]
        a["right"] = b["left"]
        b["left"] = a
        b["red"] = a["red"]
        a["red"] = True
        return b

    def rotate_right(a):
        b = a["left"]
        a["left"] = b["right"]
        b["right"] = a
        b["red"] = a["red"]
        a["red"] = True
        return b

    def insert(a, n):
        if a is None:
            return {"value": n, "red": True, "left": None, "right": None}
        key = "left" if n < a["value"] else "right"
        a[key] = insert(a[key], n)
        if red(a["right"]) and not red(a["left"]):
            a = rotate_left(a)
        if red(a["left"]) and red(a["left"]["left"]):
            a = rotate_right(a)
        if red(a["left"]) and red(a["right"]):
            a["red"] = not a["red"]
            a["left"]["red"] = False
            a["right"]["red"] = False
        return a

    root = None
    for n in nums:
        root = insert(root, n)
        root["red"] = False
    return root


def draw(v, p, mono=False):
    d = canvas(mono)
    if v in GRAPH_VARIANTS:
        labels, edges = _graph(p)
        n = len(labels)
        adj = [[] for _ in labels]
        directed = v in {"adjacency", "topological"}
        for edge in edges:
            a, b = edge[:2]
            w = edge[2] if len(edge) == 3 else 1
            adj[a].append((b, w))
            if not directed:
                adj[b].append((a, w))
        for row in adj:
            row.sort()
        chosen = set()
        order = []
        start = p["start"]
        if v in {"dfs", "bfs"}:
            work = [start]
            seen = {start}
            while work:
                a = work.pop(0 if v == "bfs" else -1)
                order.append(a)
                for b, w in (adj[a] if v == "bfs" else reversed(adj[a])):
                    if b not in seen:
                        seen.add(b)
                        work.append(b)
                        chosen.add(tuple(sorted((a, b))))
        elif v == "shortest_path":
            dist = [math.inf] * n
            dist[start] = 0
            parent = [None] * n
            queue = [(0, start)]
            while queue:
                cost, a = heapq.heappop(queue)
                if cost != dist[a]:
                    continue
                for b, w in adj[a]:
                    if cost + w < dist[b]:
                        dist[b] = cost + w
                        parent[b] = a
                        heapq.heappush(queue, (dist[b], b))
            chosen = {
                tuple(sorted((a, b))) for b, a in enumerate(parent) if a is not None
            }
            d.facts["distances"] = [q if math.isfinite(q) else None for q in dist]
        elif v in {"spanning_tree", "union_find"}:
            parents = list(range(n))

            def find(a):
                while parents[a] != a:
                    parents[a] = parents[parents[a]]
                    a = parents[a]
                return a

            for edge in sorted(edges, key=lambda e: e[2] if len(e) == 3 else 1):
                a, b = edge[:2]
                ra, rb = find(a), find(b)
                if ra != rb:
                    parents[rb] = ra
                    chosen.add(tuple(sorted((a, b))))
            d.facts["component_roots"] = [find(i) for i in range(n)]
        elif v == "topological":
            indegree = [0] * n
            for a, b, *_ in edges:
                indegree[b] += 1
            queue = [i for i in range(n) if indegree[i] == 0]
            while queue:
                a = queue.pop(0)
                order.append(a)
                for b, w in adj[a]:
                    indegree[b] -= 1
                    if indegree[b] == 0:
                        queue.append(b)
            if len(order) != n:
                raise DiagramError("diagram_topological_cycle")
        if v == "adjacency":
            cell = min(32, 190 / n)
            for i, label in enumerate(labels):
                text(d, label, 258 + (i + 0.5) * cell, 67, size=11)
                text(d, label, 242, 90 + (i + 0.5) * cell, size=11)
                for j in range(n):
                    connected = any(b == j for b, w in adj[i])
                    d.rect(
                        258 + j * cell,
                        75 + i * cell,
                        cell,
                        cell,
                        fill=d.blue if connected else d.surface,
                        width=0.7,
                    )
                    text(
                        d,
                        "1" if connected else "0",
                        258 + (j + 0.5) * cell,
                        79 + (i + 0.5) * cell,
                        size=11,
                        color="#fff" if connected else d.ink,
                    )
            positions = [
                (
                    125 + 85 * math.cos(i * 2 * math.pi / n),
                    150 + 85 * math.sin(i * 2 * math.pi / n),
                )
                for i in range(n)
            ]
        else:
            positions = [
                (
                    240 + 145 * math.cos(i * 2 * math.pi / n - math.pi / 2),
                    145 + 99 * math.sin(i * 2 * math.pi / n - math.pi / 2),
                )
                for i in range(n)
            ]
        for edge in edges:
            a, b = edge[:2]
            aa, bb = positions[a], positions[b]
            dx, dy = bb[0] - aa[0], bb[1] - aa[1]
            r = math.hypot(dx, dy)
            aa = (aa[0] + dx / r * 20, aa[1] + dy / r * 20)
            bb = (bb[0] - dx / r * 20, bb[1] - dy / r * 20)
            col = d.blue if tuple(sorted((a, b))) in chosen else d.muted
            if directed:
                d.arrow(*aa, *bb, color=col, width=2)
            else:
                d.line(*aa, *bb, color=col, width=3 if col == d.blue else 1)
            if len(edge) == 3:
                text(
                    d,
                    str(edge[2]),
                    (aa[0] + bb[0]) / 2 + 8,
                    (aa[1] + bb[1]) / 2 - 7,
                    size=11,
                )
        for i, (x, y) in enumerate(positions):
            d.circle(x, y, 17, fill=d.glass)
            text(d, labels[i], x, y + 5, size=12)
        if order:
            text(d, " → ".join(labels[i] for i in order), 240, 288, size=14)
        elif v == "shortest_path":
            text(
                d,
                " / ".join(
                    f"{labels[i]}:{q:g}" if math.isfinite(q) else labels[i] + ":∞"
                    for i, q in enumerate(dist)
                ),
                240,
                288,
                size=12,
            )
        elif v == "union_find":
            text(
                d, "components: " + str(len(set(d.facts["component_roots"]))), 240, 289
            )
        d.facts.update(items=labels, edges=edges, traversal=order)
    elif v in {"hash_chain", "open_addressing", "b_tree", "red_black"}:
        nums = values(p, maximum=7, minimum=2)
        if any(n != int(n) or not 0 <= n <= 99 for n in nums) or len(set(nums)) != len(
            nums
        ):
            raise DiagramError("diagram_invalid_keys")
        nums = [int(n) for n in nums]
        if v == "hash_chain":
            for i in range(7):
                node(d, str(i), 74, 43 + i * 36, w=40, h=28)
                row = [n for n in nums if n % 7 == i]
                for j, n in enumerate(row):
                    x = 143 + j * 44
                    node(d, str(n), x, 43 + i * 36, w=35, h=26)
                    connect(
                        d, (x - 48, 43 + i * 36), (x - 20, 43 + i * 36), color=d.blue
                    )
            text(d, "h(k) = k mod 7", 315, 309, size=12)
        elif v == "open_addressing":
            table = [None] * 8
            paths = []
            for n in nums:
                slot = n % 8
                trace = [slot]
                while table[slot] is not None:
                    slot = (slot + 1) % 8
                    trace.append(slot)
                table[slot] = n
                paths.append(trace)
            for i, n in enumerate(table):
                d.rect(
                    40 + i * 50,
                    137,
                    50,
                    50,
                    fill=d.glass if n is not None else d.surface,
                )
                text(d, str(n) if n is not None else "∅", 65 + i * 50, 168)
                text(d, str(i), 65 + i * 50, 215, size=12)
            for j, trace in enumerate(paths):
                if len(trace) > 1:
                    a, b = trace[0], trace[-1]
                    d.path(
                        f"M {65+a*50} 128 Q {(65+a*50+65+b*50)/2} {85-j*6} {65+b*50} 128",
                        color=d.gold,
                    )
            text(d, "linear probing · h(k)=k mod 8", 240, 285, size=12)
            d.facts["probe_paths"] = paths
        elif v == "b_tree":
            root = _btree(nums)
            _layout_tree(
                d,
                root,
                lambda a: a["children"],
                lambda a: " | ".join(map(str, a["keys"])),
            )
            text(d, "minimum degree t = 2", 240, 297)
        else:
            root = _red_black(nums)
            _layout_tree(
                d,
                root,
                lambda a: [b for b in [a["left"], a["right"]] if b],
                lambda a: str(a["value"]),
                lambda a: d.red if a["red"] else d.muted,
            )
            text(d, "red / black links · balanced insertion", 240, 297, size=12)
        d.facts["inserted_keys"] = nums
    elif v == "trie":
        words = p["words"]
        if not 2 <= len(words) <= 5 or any(
            not isinstance(w, str)
            or not w.isascii()
            or not w.isalpha()
            or not 1 <= len(w) <= 4
            for w in words
        ):
            raise DiagramError("diagram_invalid_trie")
        root = {}
        for word in words:
            part = root
            for char in word:
                part = part.setdefault(char, {})
            part["$"] = {}

        def leaves(a):
            return sum(leaves(b) for key, b in a.items() if key != "$") or 1

        def place(a, x, left, right, depth):
            y = 38 + depth * 49
            d.circle(x, y, 12, fill=d.gold if "$" in a else d.glass)
            text(d, "•" if depth else "∅", x, y + 4, size=10)
            cursor = left
            total = leaves(a)
            for char, b in a.items():
                if char == "$":
                    continue
                end = cursor + (right - left) * leaves(b) / total
                xx = (cursor + end) / 2
                d.line(x, y + 12, xx, y + 37, color=d.muted, width=1)
                text(d, char, (x + xx) / 2 + 10, y + 29, size=11)
                place(b, xx, cursor, end, depth + 1)
                cursor = end

        place(root, 240, 30, 450, 0)
        text(d, "gold = word end", 240, 301, size=12)
        d.facts["words"] = words
    elif v == "cpu_cycle":
        for x, label in [(80, "fetch"), (240, "decode"), (400, "execute")]:
            node(d, label, x, 160, w=100, h=62, fill=d.glass)
        connect(d, (135, 160), (180, 160))
        connect(d, (295, 160), (340, 160))
        d.path("M 400 198 L 400 253 L 80 253 L 80 198", color=d.blue)
        d.arrow(80, 219, 80, 195, color=d.blue)
        node(d, "memory", 80, 55, w=100)
        node(d, "control", 240, 55, w=100)
        node(d, "ALU", 400, 55, w=100)
        for x in [80, 240, 400]:
            d.arrow(x, 80, x, 126, color=d.gold)
    elif v == "memory_hierarchy":
        for i, label in enumerate(
            ["registers", "L1 / L2 cache", "main memory", "SSD / storage"]
        ):
            w = 90 + i * 95
            d.poly(
                [
                    (240 - w / 2, 42 + i * 58),
                    (240 + w / 2, 42 + i * 58),
                    (240 + (w + 60) / 2, 91 + i * 58),
                    (240 - (w + 60) / 2, 91 + i * 58),
                ],
                closed=True,
                fill=[d.red, d.gold, d.blue, d.glass][i],
            )
            text(d, label, 240, 75 + i * 58, size=13)
        d.arrow(40, 260, 40, 38, color=d.red)
        text(d, "speed", 42, 291, size=11)
    elif v == "paging":
        for i in range(4):
            node(d, f"page {i}", 83, 55 + i * 61, w=82, h=44)
            node(d, str([2, 0, 3, 1][i]), 238, 55 + i * 61, w=70, h=44)
            node(d, f"frame {i}", 394, 55 + i * 61, w=90, h=44)
            connect(d, (128, 55 + i * 61), (199, 55 + i * 61))
            connect(
                d, (277, 55 + i * 61), (344, 55 + [2, 0, 3, 1][i] * 61), color=d.blue
            )
        text(d, "virtual → page table → physical", 240, 309, size=12)
    elif v == "round_robin":
        bursts = values(p, minimum=2, maximum=4)
        quantum = p["quantum"]
        if any(n != int(n) or not 1 <= n <= 6 for n in bursts):
            raise DiagramError("diagram_invalid_bursts")
        remaining = [int(n) for n in bursts]
        queue = list(range(len(bursts)))
        runs = []
        time = 0
        while queue:
            i = queue.pop(0)
            duration = min(quantum, remaining[i])
            runs.append([i, time, time + duration])
            remaining[i] -= duration
            time += duration
            if remaining[i]:
                queue.append(i)
        for i, a, b in runs:
            x = 40 + 400 * a / time
            w = 400 * (b - a) / time
            d.rect(x, 150, w, 55, fill=[d.blue, d.green, d.gold, d.red][i], width=1)
            text(d, f"P{i+1}", x + w / 2, 183, size=min(12, w * 0.6))
            text(d, str(a), x, 232, size=10)
        text(d, str(time), 440, 232, size=10)
        text(d, f"quantum = {quantum}", 240, 90)
        d.facts["schedule"] = runs
    elif v == "encapsulation":
        for i, (label, col) in enumerate(
            [
                ("data", d.glass),
                ("TCP header", d.green),
                ("IP header", d.gold),
                ("frame header", d.blue),
            ]
        ):
            y = 45 + i * 62
            start = 205 - i * 44
            d.rect(start, y, 230 + i * 20, 38, fill=d.glass, width=1)
            d.rect(start, y, 44 if i else 230, 38, fill=col, width=1)
            text(d, label, 230, y + 25, size=12)
            if i:
                d.arrow(137, y - 17, 137, y - 2, color=d.muted)
        text(d, "transport → network → link", 240, 307, size=12)
    elif v == "packet_switching":
        positions = [
            (65, 160),
            (195, 70),
            (195, 247),
            (335, 75),
            (335, 240),
            (428, 160),
        ]
        for a, b in [(0, 1), (0, 2), (1, 3), (1, 4), (2, 4), (3, 5), (4, 5)]:
            d.line(*positions[a], *positions[b], color=d.muted, width=1.5)
        for i, (x, y) in enumerate(positions):
            node(d, ["S", "R1", "R2", "R3", "R4", "D"][i], x, y, w=43, h=32)
        for x, y, c in [
            (127, 117, d.blue),
            (269, 73, d.blue),
            (382, 123, d.blue),
            (126, 206, d.red),
            (264, 243, d.red),
        ]:
            d.rect(x - 8, y - 5, 16, 10, fill=c, width=1)
        text(d, "packets may take different routes", 240, 309, size=12)
    elif v == "tcp_sequence":
        for x, label in [(115, "client"), (365, "server")]:
            text(d, label, x, 37)
            d.line(x, 50, x, 290, color=d.muted)
        for y, left, label in [
            (85, True, "SYN"),
            (158, False, "SYN + ACK"),
            (233, True, "ACK"),
        ]:
            d.arrow(
                115 if left else 365,
                y,
                365 if left else 115,
                y + 29,
                color=d.blue if left else d.gold,
            )
            text(d, label, 240, y - 9, size=13)
    elif v == "regex_automaton":
        for x, label in [(160, "q₀"), (355, "q₁")]:
            d.circle(x, 160, 35, fill=d.glass)
            text(d, label, x, 166, size=20)
        d.circle(355, 160, 28)
        d.arrow(47, 160, 124, 160)
        d.arrow(197, 160, 317, 160, color=d.blue)
        text(d, "b", 257, 145)
        d.path("M 135 135 C 72 34 246 34 184 135", color=d.gold)
        d.arrow(195, 116, 184, 135, color=d.gold)
        text(d, "a", 160, 50)
        text(d, "language: a*b", 240, 280)
        d.facts["pattern"] = "a*b"
    elif v == "neural_network":
        layers = p["layers"]
        if not 2 <= len(layers) <= 4 or any(
            type(n) is not int or not 1 <= n <= 5 for n in layers
        ):
            raise DiagramError("diagram_invalid_layers")
        points = [
            [
                (70 + i * 340 / (len(layers) - 1), 160 + (j - (n - 1) / 2) * 45)
                for j in range(n)
            ]
            for i, n in enumerate(layers)
        ]
        for left, right in zip(points, points[1:]):
            for a in left:
                for b in right:
                    d.line(*a, *b, color=d.muted, width=0.6)
        for i, layer in enumerate(points):
            for x, y in layer:
                d.circle(
                    x, y, 13, fill=[d.blue, d.glass, d.green, d.gold][i], width=1.5
                )
        text(d, "input", 70, 295)
        text(d, "output", 410, 295)
        d.facts["layers"] = layers
    elif v == "convolution":
        matrix = p["input"]
        kernel = p["kernel"]
        if (
            len(matrix) != 3
            or len(kernel) != 2
            or any(not isinstance(r, list) or len(r) != 3 for r in matrix)
            or any(not isinstance(r, list) or len(r) != 2 for r in kernel)
            or any(
                type(n) not in {int, float}
                for rows in [matrix, kernel]
                for r in rows
                for n in r
            )
        ):
            raise DiagramError("diagram_invalid_convolution")
        result = [
            [
                sum(
                    matrix[i + a][j + b] * kernel[a][b]
                    for a in range(2)
                    for b in range(2)
                )
                for j in range(2)
            ]
            for i in range(2)
        ]
        for x, y, data, label in [
            (40, 100, matrix, "input"),
            (208, 120, kernel, "kernel"),
            (350, 120, result, "output"),
        ]:
            for i, row in enumerate(data):
                for j, n in enumerate(row):
                    d.rect(x + j * 38, y + i * 38, 38, 38, fill=d.glass, width=1)
                    text(d, f"{n:g}", x + j * 38 + 19, y + i * 38 + 24, size=11)
            text(d, label, x + len(data[0]) * 19, 72)
        text(d, "*", 183, 167, size=25)
        text(d, "=", 320, 167, size=25)
        text(d, "stride 1 · valid · cross-correlation", 240, 292, size=12)
        d.facts["output"] = result
    elif v == "decision_split":
        node(d, "x ≤ threshold?", 240, 66, w=140, h=44)
        node(d, "left subset", 123, 163, w=115)
        node(d, "right subset", 357, 163, w=115)
        d.arrow(213, 91, 140, 140, color=d.blue)
        d.arrow(267, 91, 340, 140, color=d.gold)
        text(d, "yes", 151, 114, size=12)
        text(d, "no", 330, 114, size=12)
        for x, yes in [(66, True), (180, True), (307, False), (417, False)]:
            node(d, "leaf", x, 264, w=62, h=35, fill=d.green if yes else d.red)
            d.line(123 if yes else 357, 184, x, 245, color=d.muted)
    elif v == "confusion_matrix":
        matrix = p["values"]
        if len(matrix) != 2 or any(
            not isinstance(row, list)
            or len(row) != 2
            or any(type(n) is not int or n < 0 for n in row)
            for row in matrix
        ):
            raise DiagramError("diagram_invalid_confusion_matrix")
        for i, row in enumerate(matrix):
            for j, n in enumerate(row):
                d.rect(
                    160 + j * 95,
                    100 + i * 85,
                    95,
                    85,
                    fill=d.green if i == j else d.red,
                )
                text(d, str(n), 207 + j * 95, 151 + i * 85, size=23)
        text(d, "predicted", 255, 36)
        text(d, "−", 207, 80)
        text(d, "+", 302, 80)
        text(d, "−", 130, 151)
        text(d, "+", 130, 236)
        text(d, "actual", 77, 205)
        d.facts["counts"] = matrix
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
