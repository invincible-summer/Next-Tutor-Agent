"""Mathematical constructions and data-driven charts; no model-authored code."""
from __future__ import annotations

import ast
import math
import statistics

from .drawing import Drawing, num
from .schema import DiagramError


def geometry(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    if variant in {"point", "segment", "line", "ray", "arrow", "double_arrow", "dimension", "parallel", "perpendicular", "equal_length", "right_angle", "brace", "bracket", "break", "polyline"}:
        if variant == "point":
            d.circle(80, 80, 4, fill=d.ink)
        elif variant in {"arrow", "double_arrow", "ray", "dimension"}:
            d.arrow(20, 80, 140, 80, double=variant in {"double_arrow", "dimension"})
            if variant == "dimension":
                d.line(20, 65, 20, 95)
                d.line(140, 65, 140, 95)
        elif variant in {"parallel", "equal_length"}:
            for y in [55, 105]:
                d.line(20, y, 140, y)
                if variant == "parallel":
                    d.poly([(75, y-5), (84, y), (75, y+5)])
                else:
                    d.line(80, y-6, 80, y+6)
        elif variant in {"perpendicular", "right_angle"}:
            d.line(30, 125, 130, 125)
            d.line(30, 125, 30, 25)
            d.poly([(30, 108), (47, 108), (47, 125)], width=1)
        elif variant == "brace":
            d.path("M 35 20 Q 60 20 60 45 L 60 65 Q 60 80 80 80 Q 60 80 60 95 L 60 115 Q 60 140 35 140")
        elif variant == "bracket":
            d.poly([(80, 20), (45, 20), (45, 140), (80, 140)])
        elif variant in {"polyline", "break"}:
            d.poly([(15, 85), (59, 85), (69, 72), (79, 97), (89, 72), (99, 85), (145, 85)])
        else:
            d.line(20, 80, 140, 80)
            if variant == "segment":
                d.circle(20, 80, 3, fill=d.ink)
                d.circle(140, 80, 3, fill=d.ink)
    elif variant in {"triangle", "right_triangle", "isosceles_triangle", "equilateral_triangle", "rectangle", "square", "parallelogram", "rhombus", "trapezoid", "kite", "polygon", "regular_polygon"}:
        shapes = {"triangle": [(25, 130), (133, 130), (66, 23)],
                  "right_triangle": [(25, 130), (133, 130), (25, 25)],
                  "isosceles_triangle": [(25, 130), (135, 130), (80, 25)],
                  "equilateral_triangle": [(20, 132), (140, 132), (80, 132-60*math.sqrt(3))],
                  "rectangle": [(18, 45), (142, 45), (142, 120), (18, 120)],
                  "square": [(29, 29), (131, 29), (131, 131), (29, 131)],
                  "parallelogram": [(45, 35), (140, 35), (115, 125), (20, 125)],
                  "rhombus": [(80, 15), (141, 80), (80, 145), (19, 80)],
                  "trapezoid": [(48, 36), (112, 36), (143, 128), (17, 128)],
                  "kite": [(80, 15), (125, 67), (80, 145), (35, 67)],
                  "polygon": [(25, 35), (86, 20), (135, 62), (106, 91), (126, 135), (25, 127)]}
        count = p.get("sides", 6)
        points = shapes.get(variant) or [(80+62*math.cos(-math.pi/2+i*2*math.pi/count), 80+62*math.sin(-math.pi/2+i*2*math.pi/count)) for i in range(count)]
        d.poly(points, closed=True, fill=d.surface)
        for i, point in enumerate(points):
            d.anchors[f"vertex_{i}"] = point
        if variant == "right_triangle":
            d.poly([(25, 114), (41, 114), (41, 130)], width=1)
        if p.get("construction") in {"altitude", "median", "bisector"}:
            if len(points) != 3:
                raise DiagramError("diagram_triangle_construction_required")
            a, b, c = points
            ratio = .5
            if p["construction"] == "altitude":
                ratio = ((c[0]-a[0])*(b[0]-a[0])+(c[1]-a[1])*(b[1]-a[1]))/math.dist(a, b)**2
            elif p["construction"] == "bisector":
                ratio = math.dist(a, c)/(math.dist(a, c)+math.dist(b, c))
            foot = (a[0]+ratio*(b[0]-a[0]), a[1]+ratio*(b[1]-a[1]))
            d.line(*c, *foot, dashed=True)
            d.facts["construction_endpoint"] = foot
    elif variant in {"circle", "ellipse", "arc", "sector", "segment_area", "annulus", "chord", "tangent", "secant", "central_angle", "inscribed_angle", "incircle", "circumcircle", "intersecting_circles"}:
        radius = p.get("radius", 54)
        angle = math.radians(p.get("angle", 60))
        if variant == "ellipse":
            d.ellipse(80, 80, 63, 39)
        elif variant in {"sector", "arc", "segment_area"}:
            x, y = 80+radius*math.cos(angle), 80-radius*math.sin(angle)
            data = f"M {80+radius} 80 A {radius} {radius} 0 {int(angle>math.pi)} 0 {num(x)} {num(y)}"
            if variant == "sector":
                data = f"M 80 80 L {80+radius} 80 A {radius} {radius} 0 {int(angle>math.pi)} 0 {num(x)} {num(y)} Z"
            elif variant == "segment_area":
                data += " Z"
            d.path(data, fill=d.glass if variant != "arc" else None)
        elif variant == "intersecting_circles":
            d.circle(57, 80, 43)
            d.circle(103, 80, 43)
        else:
            d.circle(80, 80, radius)
            if variant == "annulus":
                d.circle(80, 80, radius*.58)
            elif variant in {"chord", "secant", "tangent"}:
                yy = 80-radius if variant == "tangent" else 80+radius*.5
                half = math.sqrt(max(0, radius*radius-(yy-80)**2))
                d.line(80-half if variant == "chord" else 9, yy, 80+half if variant == "chord" else 151, yy)
            elif variant in {"central_angle", "inscribed_angle"}:
                origin = (80, 80) if variant == "central_angle" else (80-radius, 80)
                d.line(*origin, 80+radius, 80)
                d.line(*origin, 80+radius*math.cos(angle), 80-radius*math.sin(angle))
            elif variant in {"incircle", "circumcircle"}:
                r = radius
                pts = [(80+r*math.cos(t), 80+r*math.sin(t)) for t in [-math.pi/2, math.pi/6, 5*math.pi/6]]
                if variant == "incircle":
                    d.parts.clear()
                    d.circle(80, 80, radius/2)
                d.poly(pts, closed=True)
        d.anchors["center"] = (80, 80)
    elif variant in {"cube", "cuboid", "prism", "pyramid", "frustum", "cylinder", "cone", "cone_frustum", "sphere", "hemisphere", "hollow_cylinder", "section", "net", "three_views"}:
        if variant == "three_views":
            for x, y, w, h in [(18, 22, 55, 40), (18, 95, 55, 48), (103, 95, 40, 48)]:
                d.rect(x, y, w, h, fill=d.surface)
            d.line(18, 62, 18, 95, color=d.muted, dashed=True, width=1)
            d.line(73, 62, 73, 95, color=d.muted, dashed=True, width=1)
            d.line(73, 95, 103, 95, color=d.muted, dashed=True, width=1)
            d.line(73, 143, 103, 143, color=d.muted, dashed=True, width=1)
        elif variant == "prism":
            front = [(23, 126), (99, 126), (54, 48)]
            back = [(x+32, y-26) for x, y in front]
            d.poly(back, closed=True, color=d.muted)
            d.poly([front[1], back[1], back[2], front[2]], closed=True, fill=d.glass)
            d.poly(front, closed=True, fill=d.surface)
            d.line(*front[0], *back[0], dashed=True)
        elif variant == "cuboid":
            # Three different edge lengths distinguish a rectangular prism.
            d.poly([(18, 62), (122, 62), (122, 120), (18, 120)], closed=True, fill=d.surface)
            d.poly([(18, 62), (42, 40), (146, 40), (122, 62)], closed=True, fill=d.glass)
            d.poly([(122, 62), (146, 40), (146, 98), (122, 120)], closed=True, fill=d.surface)
            d.line(18, 120, 42, 98, dashed=True)
            d.line(42, 40, 42, 98, dashed=True)
            d.line(42, 98, 146, 98, dashed=True)
        elif variant in {"cube", "section"}:
            d.poly([(29, 52), (106, 52), (106, 132), (29, 132)], closed=True, fill=d.surface)
            d.poly([(29, 52), (56, 27), (133, 27), (106, 52)], closed=True, fill=d.glass)
            d.poly([(106, 52), (133, 27), (133, 108), (106, 132)], closed=True, fill=d.surface)
            d.line(29, 132, 56, 108, dashed=True)
            d.line(56, 27, 56, 108, dashed=True)
            d.line(56, 108, 133, 108, dashed=True)
            if variant == "section":
                d.poly([(29, 95), (106, 95), (133, 70), (56, 70)], closed=True, fill=d.glass, color=d.blue)
        elif variant in {"pyramid", "frustum"}:
            d.poly([(20, 112), (80, 145), (140, 110), (86, 87)], closed=True)
            if variant == "pyramid":
                for x, y in [(20, 112), (80, 145), (140, 110), (86, 87)]:
                    d.line(80, 15, x, y, dashed=(x == 86))
            else:
                d.poly([(47, 52), (80, 69), (113, 50), (84, 36)], closed=True, fill=d.surface)
                for a, b in zip([(20, 112), (80, 145), (140, 110)], [(47, 52), (80, 69), (113, 50)]):
                    d.line(*a, *b)
        elif variant in {"sphere", "hemisphere"}:
            d.circle(80, 80, 55)
            d.ellipse(80, 80, 55, 15, color=d.muted)
            d.ellipse(80, 80, 18, 55, color=d.muted)
            if variant == "hemisphere":
                d.rect(23, 80, 114, 59, fill="#fff", color="#fff")
                d.ellipse(80, 80, 55, 15)
        elif variant == "net":
            for x, y in [(28, 59), (62, 59), (96, 59), (130, 59), (62, 25), (62, 93)]:
                d.rect(x-17, y, 34, 34, fill=d.surface)
        else:
            d.ellipse(80, 128, 49, 14)
            if variant == "cone":
                d.line(31, 128, 80, 18)
                d.line(129, 128, 80, 18)
            else:
                radius = 49 if variant in {"cylinder", "hollow_cylinder"} else 27
                d.ellipse(80, 32, radius, 10, fill=d.surface)
                d.line(80-radius, 32, 31, 128)
                d.line(80+radius, 32, 129, 128)
                if variant == "hollow_cylinder":
                    d.ellipse(80, 32, 32, 6)
    elif variant in {"grid", "hatching", "number_line", "fraction_bar", "fraction_circle", "percent_grid", "dot_array", "counting_rods", "base_ten", "abacus", "clock", "dice", "coin", "spinner", "matrix"}:
        count = p.get("count", 4)
        if variant == "number_line":
            d.arrow(10, 80, 150, 80)
            for i in range(7):
                d.line(20+i*20, 74, 20+i*20, 86)
                if p.get("show_values", False):
                    d.text(i-3, 20+i*20, 108, size=14)
        elif variant == "fraction_circle":
            d.circle(80, 80, 55)
            for i in range(count):
                a = i*2*math.pi/count
                d.line(80, 80, 80+55*math.cos(a), 80+55*math.sin(a))
        elif variant in {"fraction_bar", "counting_rods"}:
            for i in range(count):
                d.rect(18+i*124/count, 54, 124/count, 48, fill=d.glass if i < p.get("filled", 1) else "#fff")
        elif variant in {"grid", "percent_grid", "matrix", "base_ten", "dot_array"}:
            n = 10 if variant == "percent_grid" else count
            for i in range(n):
                for j in range(n):
                    x, y = 20+i*120/n, 20+j*120/n
                    if variant == "dot_array":
                        d.circle(x+60/n, y+60/n, 3, fill=d.blue)
                    else:
                        d.rect(x, y, 120/n, 120/n, fill=d.glass if variant == "base_ten" else None, width=.8)
        elif variant == "abacus":
            d.rect(17, 26, 126, 109, fill=d.surface, radius=3)
            d.line(17, 63, 143, 63, width=4)
            for x in range(32, 140, 21):
                d.line(x, 28, x, 132, color=d.muted)
                for y in [45, 80, 92, 104, 116]:
                    d.ellipse(x, y, 8, 4, fill=d.gold)
        elif variant in {"clock", "spinner"}:
            d.circle(80, 80, 58, fill=d.surface)
            for i in range(12):
                a = i*math.pi/6
                d.line(80+50*math.sin(a), 80-50*math.cos(a), 80+55*math.sin(a), 80-55*math.cos(a))
            d.arrow(80, 80, 112, 49)
            if variant == "clock":
                d.line(80, 80, 80, 49, width=3)
        elif variant == "dice":
            d.rect(34, 34, 92, 92, fill=d.surface, radius=14)
            corners = [(55, 55), (105, 55), (55, 105), (105, 105)]
            faces = {1: [(80, 80)], 2: [corners[0], corners[3]], 3: [corners[0], (80, 80), corners[3]],
                     4: corners, 5: corners+[(80, 80)], 6: corners+[(55, 80), (105, 80)]}
            for x, y in faces[p.get("face", 5)]:
                d.circle(x, y, 5, fill=d.ink)
        elif variant == "coin":
            d.circle(80, 80, 51, fill=d.gold)
            d.circle(80, 80, 43, width=1)
        elif variant == "hatching":
            d.rect(25, 35, 110, 90)
            d.hatch(25, 35, 110, 90)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d


_FUNCTIONS = {"sin": math.sin, "cos": math.cos, "tan": math.tan,
              "exp": math.exp, "log": math.log, "sqrt": math.sqrt, "abs": abs}


def expression(source: str):
    """Interpret a tiny arithmetic tree. Never eval/compile user or model text."""
    if not isinstance(source, str) or len(source) > 160:
        raise DiagramError("diagram_invalid_function")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError):
        raise DiagramError("diagram_invalid_function") from None
    if len(list(ast.walk(tree))) > 80:
        raise DiagramError("diagram_invalid_function")

    def visit(node, x):
        if isinstance(node, ast.Expression):
            return visit(node.body, x)
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            if not math.isfinite(node.value) or abs(node.value) > 10000:
                raise DiagramError("diagram_invalid_function")
            return node.value
        if isinstance(node, ast.Name) and node.id in {"x", "pi", "e"}:
            return {"x": x, "pi": math.pi, "e": math.e}[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = visit(node.operand, x)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            a, b = visit(node.left, x), visit(node.right, x)
            if isinstance(node.op, ast.Pow):
                if abs(b) > 12:
                    raise DiagramError("diagram_invalid_function")
                return a ** b
            if isinstance(node.op, ast.Add):
                return a+b
            if isinstance(node.op, ast.Sub):
                return a-b
            if isinstance(node.op, ast.Mult):
                return a*b
            return a/b
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS and len(node.args) == 1 and not node.keywords:
            return _FUNCTIONS[node.func.id](visit(node.args[0], x))
        raise DiagramError("diagram_invalid_function")

    allowed = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.UnaryOp, ast.UAdd,
               ast.USub, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Call)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise DiagramError("diagram_invalid_function")
        if isinstance(node, ast.Name) and node.id not in {"x", "pi", "e", *_FUNCTIONS}:
            raise DiagramError("diagram_invalid_function")
    try:
        visit(tree, .731)
    except DiagramError:
        raise
    except (ZeroDivisionError, OverflowError, ValueError, TypeError):
        # Domain errors at this probe are fine; every sample is bounded below.
        pass
    return lambda x: visit(tree, x)


def function_plot(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(480, 300, mono)
    xmin, xmax = p.get("x_range", [-4, 4])
    ymin, ymax = p.get("y_range", [-3, 5])
    if xmin >= xmax or ymin >= ymax:
        raise DiagramError("diagram_invalid_domain")
    def xy(x, y):
        return 50+(x-xmin)*395/(xmax-xmin), 250-(y-ymin)*220/(ymax-ymin)
    for i in range(9):
        x = 50+i*395/8
        d.line(x, 30, x, 250, color=d.surface, width=1)
    for i in range(6):
        y = 30+i*44
        d.line(50, y, 445, y, color=d.surface, width=1)
    ox, oy = xy(max(xmin, min(xmax, 0)), max(ymin, min(ymax, 0)))
    d.arrow(50, oy, 451, oy)
    d.arrow(ox, 250, ox, 22)
    if p.get("show_ticks", True):
        for i in range(5):
            x = xmin+i*(xmax-xmin)/4
            px, _ = xy(x, 0)
            d.line(px, oy-4, px, oy+4)
            d.text(f"{x:g}", px-12 if abs(x) < 1e-9 else px, min(282, oy+23), size=14)
        for i in range(5):
            y = ymin+i*(ymax-ymin)/4
            _, py = xy(0, y)
            d.line(ox-4, py, ox+4, py)
            if abs(y) > 1e-9 and abs((py+5)-min(282, oy+23)) >= 14:
                d.text(f"{y:g}", max(28, ox-9), py+5, size=14, anchor="end")
    d.text("Re" if variant == "complex" else p.get("x_label", "x"), 460, min(278, oy+21), size=18)
    d.text("Im" if variant == "complex" else p.get("y_label", "y"), min(432, ox+16), 22, size=18)
    defaults = {"linear": "x", "quadratic": "x**2", "polynomial": "x**3/4", "reciprocal": "1/x",
                "absolute": "abs(x)", "sqrt": "sqrt(x)", "exponential": "exp(x)",
                "logarithm": "log(x)", "sine": "sin(x)", "cosine": "cos(x)",
                "tangent": "tan(x)", "tangent_line": "x*x/2", "integral": "x*x/3", "riemann": "x*x/3"}
    if variant in {"axes", "polar", "complex", "vector", "vector_sum", "projection", "basis", "linear_transform"}:
        if variant == "linear_transform":
            matrix = p.get("matrix", [[1, .5], [.2, 1]])
            if len(matrix) != 2 or any(len(row) != 2 or any(type(v) not in {int, float} for v in row) for row in matrix):
                raise DiagramError("diagram_invalid_matrix")
            def transformed(x, y):
                return matrix[0][0]*x+matrix[0][1]*y, matrix[1][0]*x+matrix[1][1]*y
            for k in range(-2, 3):
                for endpoints in [[(-2, k), (2, k)], [(k, -2), (k, 2)]]:
                    points = [transformed(*point) for point in endpoints]
                    if any(not xmin <= x <= xmax or not ymin <= y <= ymax for x, y in points):
                        raise DiagramError("diagram_transform_range_missing")
                    d.line(*xy(*points[0]), *xy(*points[1]), color=d.blue, width=1)
            d.arrow(*xy(0, 0), *xy(*transformed(1, 0)), color=d.red, width=3)
            d.arrow(*xy(0, 0), *xy(*transformed(0, 1)), color=d.green, width=3)
            d.facts["matrix"] = matrix
        if variant == "polar":
            for r in [35, 70, 105]:
                d.circle(250, 140, r, color=d.muted, width=1)
            for i in range(6):
                a = i*math.pi/6
                d.line(250-105*math.cos(a), 140-105*math.sin(a), 250+105*math.cos(a), 140+105*math.sin(a), color=d.muted, width=1)
        if variant in {"vector", "vector_sum", "projection", "basis"}:
            a, b = p.get("vector", [2, 2])
            required = [(0, 0), (a, b)]
            if variant in {"vector_sum", "basis"}:
                required.append((-1, 2))
            if variant == "vector_sum":
                required.append((a-1, b+2))
            if any(not xmin <= x <= xmax or not ymin <= y <= ymax for x,y in required):
                raise DiagramError("diagram_vector_range_missing")
            d.arrow(*xy(0, 0), *xy(a, b), color=d.blue, width=3)
            if variant in {"vector_sum", "basis"}:
                d.arrow(*xy(0, 0), *xy(-1, 2), color=d.red)
            if variant == "vector_sum":
                d.arrow(*xy(a, b), *xy(a-1, b+2), color=d.red)
                d.arrow(*xy(0, 0), *xy(a-1, b+2), color=d.green, width=3)
            if variant == "projection":
                d.line(*xy(a, b), *xy(a, 0), dashed=True)
    else:
        fn = expression(p.get("function", defaults.get(variant, "x")))
        points = []
        samples = []
        for i in range(241):
            x = xmin+i*(xmax-xmin)/240
            try:
                y = fn(x)
                good = isinstance(y, (int, float)) and math.isfinite(y) and ymin <= y <= ymax
            except (ZeroDivisionError, OverflowError, ValueError, TypeError):
                good = False
                y = 0
            if not good or (points and abs(xy(x, y)[1]-points[-1][1]) > 80):
                if len(points) > 1:
                    d.poly(points, color=d.blue, width=2.3)
                points = []
            if good:
                points.append(xy(x, y))
                samples.append((x, y))
        if len(points) > 1:
            d.poly(points, color=d.blue, width=2.3)
        if variant == "tangent_line":
            at = p.get("interval", [0, 2])[1]
            value = fn(at)
            if not xmin <= at <= xmax or not math.isfinite(value) or not ymin <= value <= ymax:
                raise DiagramError("diagram_tangent_range_missing")
            step = max(1e-5, abs(at)*1e-5)
            slope = (fn(at+step)-fn(at-step))/(2*step)
            line_points = [(x, value+slope*(x-at)) for x in [xmin, xmax]]
            if slope:
                line_points += [(at+(y-value)/slope, y) for y in [ymin, ymax]]
            visible = [(x, y) for x, y in line_points if xmin-1e-8 <= x <= xmax+1e-8 and ymin-1e-8 <= y <= ymax+1e-8]
            if len(visible) >= 2:
                d.line(*xy(*visible[0]), *xy(*visible[1]), color=d.red)
                d.circle(*xy(at, value), 4, fill=d.red, color=d.red)
                d.facts["tangent"] = {"x": at, "y": value, "slope": slope}
        if variant in {"integral", "riemann"}:
            a, b = p.get("interval", [0, 2])
            if not xmin <= a < b <= xmax or not ymin <= 0 <= ymax:
                raise DiagramError("diagram_invalid_interval")
            if variant == "integral":
                samples = [(a+i*(b-a)/80, fn(a+i*(b-a)/80)) for i in range(81)]
                if any(not math.isfinite(y) or not ymin <= y <= ymax for _, y in samples):
                    raise DiagramError("diagram_invalid_interval")
                d.poly([xy(a, 0), *[xy(x, y) for x,y in samples], xy(b, 0)], closed=True, fill=d.glass, color=d.blue, width=1)
            for i in range(8 if variant == "riemann" else 0):
                left = a+i*(b-a)/8
                right = left+(b-a)/8
                y = fn((left+right)/2)
                if not ymin <= y <= ymax:
                    raise DiagramError("diagram_invalid_interval")
                x1, y1 = xy(left, y)
                x2, y0 = xy(right, 0)
                d.rect(x1, min(y0, y1), x2-x1, abs(y0-y1), fill=d.glass, color=d.blue, width=.8)
        d.facts["function"] = p.get("function", defaults.get(variant, "x"))
    d.anchors.update({"origin": (ox, oy)})
    return d


def chart(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(480, 320, mono)
    values = p.get("values")
    if not isinstance(values, list) or not values:
        raise DiagramError("diagram_chart_data_required")
    labels = p.get("labels", [])
    colors = [d.blue, d.gold, d.green, d.red, d.muted, "#977ca9" if not mono else "#555"]
    show = p.get("show_values", False)
    if variant in {"pie", "donut", "radar", "funnel", "pictograph"}:
        if any(v < 0 for v in values) or sum(values) <= 0:
            raise DiagramError("diagram_invalid_chart_data")
        if variant in {"pie", "donut"}:
            angle = -math.pi/2
            angles = []
            for i, value in enumerate(values):
                sweep = value/sum(values)*2*math.pi
                angles.append(math.degrees(sweep))
                if value:
                    if sweep >= 2*math.pi-1e-9:
                        d.circle(190, 151, 105, fill=colors[i % 6], color="#fff", width=1.5)
                    else:
                        x1, y1 = 190+105*math.cos(angle), 151+105*math.sin(angle)
                        x2, y2 = 190+105*math.cos(angle+sweep), 151+105*math.sin(angle+sweep)
                        d.path(f"M 190 151 L {num(x1)} {num(y1)} A 105 105 0 {int(sweep>math.pi)} 1 {num(x2)} {num(y2)} Z", fill=colors[i % 6], color="#fff", width=1.5)
                    if show:
                        mid = angle+sweep/2
                        d.text(f"{value/sum(values)*100:.1f}%", 190+73*math.cos(mid), 155+73*math.sin(mid), size=15)
                angle += sweep
            if variant == "donut":
                d.circle(190, 151, 54, fill="#fff", color="#fff")
            d.facts["sector_angles"] = angles
            for i, label in enumerate(labels):
                d.rect(327, 72+i*29, 14, 14, fill=colors[i % 6], color=colors[i % 6])
                d.text(label, 350, 84+i*29, anchor="start", size=17)
        elif variant == "radar":
            n, maximum = len(values), max(values)
            if n < 3:
                raise DiagramError("diagram_invalid_chart_data")
            for r in [35, 70, 105]:
                d.poly([(230+r*math.cos(-math.pi/2+i*2*math.pi/n), 155+r*math.sin(-math.pi/2+i*2*math.pi/n)) for i in range(n)], closed=True, color=d.muted, width=1)
            for i in range(n):
                a = -math.pi/2+i*2*math.pi/n
                d.line(230, 155, 230+105*math.cos(a), 155+105*math.sin(a), color=d.muted, width=1)
                d.text(labels[i] if i < len(labels) else str(i+1), 230+126*math.cos(a), 160+126*math.sin(a), size=15)
            d.poly([(230+105*v/maximum*math.cos(-math.pi/2+i*2*math.pi/n), 155+105*v/maximum*math.sin(-math.pi/2+i*2*math.pi/n)) for i, v in enumerate(values)], closed=True, color=d.blue)
        elif variant == "funnel":
            if any(b > a for a, b in zip(values, values[1:])):
                raise DiagramError("diagram_funnel_not_descending")
            for i, v in enumerate(values):
                w = 350*v/max(values)
                d.poly([(240-w/2, 30+i*240/len(values)), (240+w/2, 30+i*240/len(values)),
                        (240+w*.43, 30+(i+1)*240/len(values)-5), (240-w*.43, 30+(i+1)*240/len(values)-5)], closed=True, fill=colors[i % 6])
        else:
            if any(type(v) is not int or not 0 <= v <= 16 for v in values):
                raise DiagramError("diagram_pictograph_count_invalid")
            for i, v in enumerate(values):
                d.text(labels[i] if i < len(labels) else str(i+1), 48, 50+i*32, size=15, anchor="end")
                for j in range(v):
                    d.circle(68+j*22, 45+i*32, 6, fill=colors[i % 6])
            d.text("● = 1", 390, 297, size=15)
    else:
        d.line(52, 28, 52, 267)
        d.line(52, 267, 453, 267)
        matrix = values if isinstance(values[0], list) else [values]
        flat = [v for series in matrix for v in series]
        if variant in {"scatter", "bubble", "errorbar", "confidence"} and p.get("points"):
            flat = [pt[1] for pt in p["points"]]
        maximum = max(max(flat), 1)
        minimum = min(min(flat), 0)
        if variant in {"ecdf", "ogive"}:
            maximum, minimum = (1, 0) if variant == "ecdf" else (sum(values), 0)
        if variant == "waterfall":
            if len(matrix) != 1:
                raise DiagramError("diagram_invalid_chart_data")
            totals = [0]
            for value in values:
                totals.append(totals[-1]+value)
            maximum, minimum = max(max(totals), 1), min(min(totals), 0)
        if variant in {"errorbar", "confidence"}:
            errors = p.get("errors")
            points = p.get("points") or [[i+1, v] for i, v in enumerate(values)]
            if errors is None or len(errors) != len(points) or any(e < 0 for e in errors):
                raise DiagramError("diagram_error_values_required")
            maximum = max(maximum, max(pt[1]+e for pt, e in zip(points, errors)))
            minimum = min(minimum, min(pt[1]-e for pt, e in zip(points, errors)))
        if variant in {"stacked", "percent_stacked", "stacked_area"}:
            if any(v < 0 for v in flat):
                raise DiagramError("diagram_invalid_chart_data")
            maximum = max(sum(series[i] for series in matrix) for i in range(len(matrix[0]))) or 1
            if variant == "percent_stacked":
                maximum = 100
        def yy(v):
            return 267-(v-minimum)*225/(maximum-minimum)
        if variant in {"horizontal_bar", "population"}:
            if any(v < 0 for v in flat) or (variant == "population" and len(matrix) != 2):
                raise DiagramError("diagram_invalid_chart_data")
            d.parts.clear()
            mid = 250 if variant == "population" else 85
            span = 165 if variant == "population" else 350
            d.line(mid, 30, mid, 267, color=d.muted)
            d.line(85, 267, 435, 267, color=d.muted)
            for i in range(len(matrix[0])):
                y = 36+i*214/len(matrix[0])
                h = 214/len(matrix[0])*.68
                for j, series in enumerate(matrix):
                    w = span*series[i]/maximum
                    x = mid-w if variant == "population" and j == 0 else mid
                    d.rect(x, y, w, h, fill=colors[j % 6], color=colors[j % 6], width=1)
                d.text(labels[i] if i < len(labels) else str(i+1), 76, y+h/2+5, size=15, anchor="end")
            for i in range(5):
                v = maximum*i/4
                d.text(f"{v:.4g}", mid+span*i/4, 289, size=14)
                if variant == "population" and i:
                    d.text(f"{v:.4g}", mid-span*i/4, 289, size=14)
            d.facts["data"] = values
            return d
        if variant in {"bar", "grouped", "stacked", "percent_stacked", "horizontal_bar", "lollipop", "waterfall", "population"}:
            n = len(matrix[0])
            slot = 390/n
            running = 0
            for i in range(n):
                base = running if variant == "waterfall" else 0
                for j, series in enumerate(matrix):
                    v = series[i]
                    if variant == "percent_stacked":
                        total = sum(s[i] for s in matrix)
                        v = v/total*100 if total else 0
                    x = 58+i*slot+slot*.16
                    w = slot*.68
                    if variant == "grouped":
                        w /= len(matrix)
                        x += j*w
                    if variant in {"horizontal_bar", "population"}:
                        h = 210/n
                        d.rect(54, 40+i*h, 370*v/maximum, h*.65, fill=colors[j % 6], color=colors[j % 6])
                    elif variant == "lollipop":
                        d.line(x+w/2, yy(0), x+w/2, yy(v), color=d.blue)
                        d.circle(x+w/2, yy(v), 5, fill=d.blue)
                    else:
                        top = base+v if variant in {"stacked", "percent_stacked", "waterfall"} else v
                        bottom = base if variant in {"stacked", "percent_stacked", "waterfall"} else 0
                        d.rect(x, min(yy(top), yy(bottom)), w, abs(yy(top)-yy(bottom)), fill=colors[j % 6], color=colors[j % 6], width=1)
                        if show:
                            d.text(f"{v:g}", x+w/2, min(yy(top), yy(bottom))-8, size=15)
                        base += v
                if variant == "waterfall":
                    running = base
                    if i < n-1:
                        d.line(58+i*slot+slot*.84, yy(running), 58+(i+1)*slot+slot*.16, yy(running), color=d.muted, dashed=True, width=1)
                if i < len(labels):
                    d.text(labels[i], 58+(i+.5)*slot, 292, size=16)
        elif variant == "histogram":
            edges = p.get("bin_edges")
            if not isinstance(edges, list) or len(edges) != len(values)+1 or any(b <= a for a, b in zip(edges, edges[1:])):
                raise DiagramError("diagram_invalid_bins")
            if any(v < 0 for v in values):
                raise DiagramError("diagram_invalid_chart_data")
            heights = [v/(edges[i+1]-edges[i]) for i, v in enumerate(values)] if p.get("density", False) else values
            if not p.get("density", False) and len({round(b-a, 9) for a, b in zip(edges, edges[1:])}) > 1:
                raise DiagramError("diagram_unequal_bins_need_density")
            for i, h in enumerate(heights):
                x = 53+(edges[i]-edges[0])*390/(edges[-1]-edges[0])
                w = (edges[i+1]-edges[i])*390/(edges[-1]-edges[0])
                y = 267-h*225/(max(heights) or 1)
                d.rect(x, y, w, 267-y, fill=d.glass, color=d.blue, width=1.3)
            d.facts["bar_heights"] = heights
            maximum = max(heights) or 1
            for i, edge in enumerate(edges):
                d.text(f"{edge:g}", 53+(edge-edges[0])*390/(edges[-1]-edges[0]), 289, size=13)
        elif variant in {"box", "dot", "stem_leaf", "ecdf", "density", "qq", "normal", "t_distribution"}:
            data = sorted(values)
            lo, hi = min(data), max(data)
            def xx(v):
                return 65+(v-lo)*370/(hi-lo or 1)
            if variant == "box":
                d.parts.pop(0)
                quartiles = statistics.quantiles(data, n=4, method="inclusive") if len(data) > 1 else [data[0]]*3
                q1, median, q3 = quartiles
                d.line(xx(lo), 150, xx(hi), 150)
                for v in [lo, hi]:
                    d.line(xx(v), 126, xx(v), 174)
                d.rect(xx(q1), 113, xx(q3)-xx(q1), 74, fill=d.glass)
                d.line(xx(median), 113, xx(median), 187, width=3)
                d.facts["five_number_summary"] = [lo, q1, median, q3, hi]
                for value in [lo, q1, median, q3, hi]:
                    d.text(f"{value:.4g}", xx(value), 222, size=14)
            elif variant == "dot":
                frequencies = {v: data.count(v) for v in set(data)}
                spacing = min(20, 220/max(frequencies.values()))
                counts = {}
                for v in data:
                    counts[v] = counts.get(v, 0)+1
                    d.circle(xx(v), 267-counts[v]*spacing, 4, fill=d.blue)
                for count in range(0, max(frequencies.values())+1, max(1, math.ceil(max(frequencies.values())/5))):
                    d.text(count, 43, 272-count*spacing, size=14, anchor="end")
                for i in range(5):
                    value = lo+(hi-lo)*i/4
                    d.text(f"{value:.4g}", xx(value), 289, size=14)
            elif variant == "stem_leaf":
                stems = {}
                for v in data:
                    stem, leaf = divmod(int(v), 10)
                    stems.setdefault(stem, []).append(leaf)
                for i, (stem, leaves) in enumerate(stems.items()):
                    d.text(stem, 92, 55+i*24, size=17)
                    d.text(" ".join(map(str, leaves)), 129, 55+i*24, size=17, anchor="start")
                d.line(111, 31, 111, min(261, 60+len(stems)*24))
            elif variant == "ecdf":
                unique = sorted(set(data))
                d.line(52, 267, xx(unique[0]), 267, color=d.blue)
                for i, v in enumerate(unique):
                    y = yy(sum(item <= v for item in data)/len(data))
                    nextx = xx(unique[i+1]) if i+1 < len(unique) else 445
                    d.line(xx(v), y, nextx, y, color=d.blue)
                    d.circle(xx(v), y, 2, fill=d.blue)
                for i in range(5):
                    v = lo+(hi-lo)*i/4
                    d.text(f"{v:.4g}", xx(v), 289, size=14)
            elif variant == "qq":
                normal = statistics.NormalDist()
                for i, v in enumerate(data):
                    z = normal.inv_cdf((i+.5)/len(data))
                    d.circle(245+z*65, 267-(v-lo)*220/(hi-lo or 1), 3, fill=d.blue)
                for i in range(5):
                    d.text(i-2, 245+(i-2)*65, 289, size=14)
                    d.text(f"{lo+(hi-lo)*i/4:.4g}", 43, 272-i*220/4, size=14, anchor="end")
                d.text("Q", 456, 289, size=14)
            else:
                sd = statistics.stdev(data) if len(data) > 1 else 1
                bandwidth = max(sd, .01)*len(data)**(-.2)*1.06
                points = []
                for i in range(161):
                    x = -4+i*.05 if variant in {"normal", "t_distribution"} else lo-3*bandwidth+i*(hi-lo+6*bandwidth)/160
                    if variant == "normal":
                        y = math.exp(-x*x/2)/math.sqrt(2*math.pi)
                    elif variant == "t_distribution":
                        df = p.get("df", 5)
                        y = math.gamma((df+1)/2)/(math.sqrt(df*math.pi)*math.gamma(df/2))*(1+x*x/df)**(-(df+1)/2)
                    else:
                        y = sum(math.exp(-.5*((x-v)/bandwidth)**2) for v in data)/(len(data)*bandwidth*math.sqrt(2*math.pi))
                    points.append((55+i*390/160, y))
                ymax = max(y for _, y in points) or 1
                d.poly([(x, 267-y*220/ymax) for x, y in points], color=d.blue)
                xlo, xhi = (-4, 4) if variant in {"normal", "t_distribution"} else (lo-3*bandwidth, hi+3*bandwidth)
                for i in range(5):
                    d.text(f"{xlo+(xhi-xlo)*i/4:.3g}", 55+i*390/4, 289, size=13)
                    d.text(f"{ymax*i/4:.3g}", 43, 272-i*220/4, size=13, anchor="end")
        elif variant == "heatmap":
            for i, row in enumerate(matrix):
                for j, v in enumerate(row):
                    shade = int(240-140*(v-minimum)/(maximum-minimum))
                    color = f"#{shade:02x}{min(255,shade+8):02x}{min(255,shade+15):02x}"
                    d.rect(55+j*390/len(row), 35+i*225/len(matrix), 390/len(row), 225/len(matrix), fill=color, color="#fff", width=1)
        elif variant in {"scatter", "bubble", "errorbar", "confidence"}:
            points = p.get("points")
            if not points:
                points = [[i+1, v] for i, v in enumerate(values)]
            xmax = max(x for x, *_ in points) or 1
            xmin = min(0, min(x for x, *_ in points))
            xmax = max(xmax, xmin+1)
            for point in points:
                x, v = point[:2]
                px = 55+(x-xmin)*385/(xmax-xmin)
                r = math.sqrt(point[2])*3 if variant == "bubble" and len(point) > 2 else 4
                d.circle(px, yy(v), min(30, r), fill=d.blue)
                if variant in {"errorbar", "confidence"}:
                    errors = p.get("errors")
                    if errors is None or len(errors) != len(points):
                        raise DiagramError("diagram_error_values_required")
                    error = errors[points.index(point)]
                    if error < 0:
                        raise DiagramError("diagram_invalid_chart_data")
                    a, b = yy(v-error), yy(v+error)
                    if a > 267 or b < 28:
                        raise DiagramError("diagram_chart_range_missing")
                    d.line(px, a, px, b, color=d.blue)
                    d.line(px-5, a, px+5, a)
                    d.line(px-5, b, px+5, b)
            for i in range(5):
                d.text(f"{xmin+(xmax-xmin)*i/4:.3g}", 55+i*385/4, 289, size=13)
        else:
            previous = [0]*len(matrix[0])
            for j, series in enumerate(matrix):
                if variant == "ogive":
                    series = [sum(series[:i+1]) for i in range(len(series))]
                if variant == "stacked_area":
                    series = [v+previous[i] for i, v in enumerate(series)]
                pts = [(58+i*385/max(1, len(series)-1), yy(v)) for i, v in enumerate(series)]
                if variant in {"area", "stacked_area"}:
                    lower = [(58+i*385/max(1, len(series)-1), yy(v)) for i, v in enumerate(previous)]
                    d.poly(pts+list(reversed(lower)), closed=True, fill=colors[j % 6] if variant == "stacked_area" else d.glass, color=colors[j % 6])
                d.poly(pts, color=colors[j % 6], width=2.3)
                for x, y in pts:
                    d.circle(x, y, 3, fill=colors[j % 6])
                if variant == "stacked_area":
                    previous = series
            for i in range(len(matrix[0])):
                d.text(labels[i] if i < len(labels) else str(i+1), 58+i*385/max(1,len(matrix[0])-1), 289, size=13)
        if variant not in {"box", "dot", "stem_leaf", "density", "qq", "normal", "t_distribution", "heatmap"}:
            for i in range(5):
                v = minimum+i*(maximum-minimum)/4
                d.text(f"{v:.4g}", 43, yy(v)+5, size=14, anchor="end")
    d.facts["data"] = values
    return d
