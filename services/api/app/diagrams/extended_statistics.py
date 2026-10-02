"""Data-driven statistical graphics, authored without external chart artwork."""

import itertools
import math
import statistics

from .extended_common import canvas, text, axes, curve, panel, numeric, values, labels
from .schema import DiagramError


def parameters(v):
    if v == "binomial":
        return numeric("n", 10, 2, 20, True) | numeric("probability", 0.4, 0.05, 0.95)
    if v == "poisson":
        return numeric("rate", 4, 0.5, 10)
    if v == "chi_square":
        return numeric("df", 4, 1, 20)
    if v == "f_distribution":
        return numeric("df1", 5, 2, 20) | numeric("df2", 8, 2, 30)
    key = (
        "groups"
        if v in {"violin", "beeswarm", "raincloud", "permutation_distribution"}
        else (
            "points"
            if v
            in {
                "residuals",
                "prediction_interval",
                "agreement",
                "slope",
                "kde_contours",
            }
            else "flows" if v == "sankey" else "values"
        )
    )
    schema = {key: {"type": "list", "required": True}}
    if v in {"pareto", "treemap", "slope", "bullet", "mosaic"}:
        schema["labels"] = {"type": "list", "default": []}
    if v == "bullet":
        schema["target"] = numeric("target", 8, 0.1, 10000)["target"]
    if v == "sampling_distribution":
        schema["population"] = {"type": "list", "required": True}
    return schema


def _groups(p):
    gs = p["groups"]
    if not 1 <= len(gs) <= 3 or any(not isinstance(row, list) for row in gs):
        raise DiagramError("diagram_invalid_groups")
    return [values({"values": row}, minimum=3, maximum=30) for row in gs]


def _points(p, width=2, minimum=3):
    rows = p["points"]
    if not minimum <= len(rows) <= 45 or any(
        not isinstance(a, list)
        or len(a) != width
        or any(type(x) not in {int, float} or not math.isfinite(x) for x in a)
        for a in rows
    ):
        raise DiagramError("diagram_invalid_plot_data")
    return rows


def _scale(nums):
    lo, hi = min(nums), max(nums)
    if lo == hi:
        lo -= 0.5
        hi += 0.5
    pad = (hi - lo) * 0.1
    return lo - pad, hi + pad


def _hist(d, nums):
    lo, hi = _scale(nums)
    bins = 12
    counts = [0] * bins
    for n in nums:
        counts[min(bins - 1, int((n - lo) / (hi - lo) * bins))] += 1
    xy = axes(d, x_label="value", y_label="frequency")
    peak = max(counts)
    for i, count in enumerate(counts):
        x, y = xy(i / bins, count / peak * 0.9)
        d.rect(x, y, 370 / bins - 2, 260 - y, fill=d.blue, width=1)
    text(d, f"{lo:.2g}", 56, 292, size=11)
    text(d, f"{hi:.2g}", 423, 292, size=11)
    return counts


def draw(v, p, mono=False):
    d = canvas(mono)
    d.facts["data_source"] = "task_parameters"
    if v in {"violin", "beeswarm", "raincloud"}:
        groups = _groups(p)
        nums = list(itertools.chain.from_iterable(groups))
        lo, hi = _scale(nums)
        d.line(65, 267, 437, 267)
        d.line(65, 267, 65, 38)
        text(d, "value", 38, 40, size=12)
        for i, group in enumerate(groups):
            x = 140 + i * 260 / max(1, len(groups) - 1)
            coord = lambda n: 257 - (n - lo) / (hi - lo) * 205
            sd = statistics.stdev(group)
            band = max((hi - lo) * 0.025, 1.06 * sd * len(group) ** (-0.2))
            grid = [lo + (hi - lo) * j / 40 for j in range(41)]
            density = [
                sum(math.exp(-0.5 * ((n - a) / band) ** 2) for a in group) / len(group)
                for n in grid
            ]
            peak = max(density)
            if v in {"violin", "raincloud"}:
                right = [(x + 42 * q / peak, coord(n)) for n, q in zip(grid, density)]
                left = [
                    (x - 42 * q / peak if v == "violin" else x, coord(n))
                    for n, q in zip(grid, density)
                ]
                d.poly(
                    right + list(reversed(left)),
                    closed=True,
                    fill=d.glass,
                    color=d.blue,
                    width=1.5,
                )
            if v in {"beeswarm", "raincloud"}:
                placed = []
                for n in sorted(group):
                    y = coord(n)
                    candidates = [0] + [
                        sign * j * 7 for j in range(1, 8) for sign in [-1, 1]
                    ]
                    dx = next(
                        (
                            q
                            for q in candidates
                            if all(math.hypot(q - px, y - py) >= 6 for px, py in placed)
                        ),
                        candidates[-1],
                    )
                    placed.append((dx, y))
                    d.circle(
                        x + dx - (24 if v == "raincloud" else 0),
                        y,
                        2.5,
                        fill=d.blue,
                        width=0.5,
                    )
            if v != "beeswarm":
                q1, _, q3 = statistics.quantiles(group, n=4, method="inclusive")
                med = statistics.median(group)
                d.rect(
                    x - 6, coord(q3), 12, coord(q1) - coord(q3), fill=d.surface, width=1
                )
                d.line(x - 9, coord(med), x + 9, coord(med), color=d.red, width=2)
            text(d, chr(65 + i), x, 292)
        d.facts["groups"] = groups
    elif v == "mosaic":
        matrix = p["values"]
        if not 2 <= len(matrix) <= 4 or any(
            not isinstance(r, list)
            or not 2 <= len(r) <= 4
            or len(r) != len(matrix[0])
            or any(type(n) not in {int, float} or n <= 0 for n in r)
            for r in matrix
        ):
            raise DiagramError("diagram_invalid_contingency")
        total = sum(map(sum, matrix))
        x = 60
        names = labels(p, len(matrix))
        colors = [d.blue, d.green, d.gold, d.red]
        for i, row in enumerate(matrix):
            w = sum(row) / total * 360
            y = 45
            for j, n in enumerate(row):
                h = n / sum(row) * 215
                d.rect(x, y, w - 3, h - 3, fill=colors[j], width=0.7)
                y += h
            text(d, names[i], x + w / 2, 287, size=12)
            x += w
        d.facts["cell_areas_proportional_to_counts"] = True
    elif v in {"residuals", "agreement"}:
        rows = _points(p)
        if v == "residuals":
            xs = [a[0] for a in rows]
            ys = [a[1] for a in rows]
            mx = statistics.mean(xs)
            my = statistics.mean(ys)
            ss = sum((x - mx) ** 2 for x in xs)
            if ss == 0:
                raise DiagramError("diagram_degenerate_regression")
            slope = sum((x - mx) * (y - my) for x, y in rows) / ss
            data = [(x, y - my - slope * (x - mx)) for x, y in rows]
            reference = 0
            d.facts.update(slope=slope, intercept=my - slope * mx)
        else:
            data = [((a + b) / 2, a - b) for a, b in rows]
            reference = statistics.mean(y for x, y in data)
            sd = statistics.stdev(y for x, y in data)
            d.facts.update(
                mean_difference=reference,
                agreement_limits=[reference - 1.96 * sd, reference + 1.96 * sd],
            )
        xmin, xmax = _scale([x for x, y in data])
        ymin, ymax = _scale([y for x, y in data] + [reference])
        if v == "agreement":
            ymin = min(ymin, reference - 2.2 * sd)
            ymax = max(ymax, reference + 2.2 * sd)
        xy = axes(
            d,
            x_label="x" if v == "residuals" else "mean",
            y_label="residual" if v == "residuals" else "difference",
        )
        coord = lambda x, y: xy((x - xmin) / (xmax - xmin), (y - ymin) / (ymax - ymin))
        d.line(
            *coord(xmin, reference), *coord(xmax, reference), color=d.red, dashed=True
        )
        if v == "agreement":
            for q in [reference - 1.96 * sd, reference + 1.96 * sd]:
                d.line(
                    *coord(xmin, q),
                    *coord(xmax, q),
                    color=d.gold,
                    dashed=True,
                    width=1.5,
                )
        for x, y in data:
            d.circle(*coord(x, y), 4, fill=d.blue, width=1)
    elif v == "prediction_interval":
        rows = _points(p, width=4)
        if any(a[2] > a[1] or a[1] > a[3] for a in rows) or any(
            rows[i][0] >= rows[i + 1][0] for i in range(len(rows) - 1)
        ):
            raise DiagramError("diagram_invalid_interval")
        xmin, xmax = _scale([a[0] for a in rows])
        ymin, ymax = _scale([q for a in rows for q in a[1:]])
        xy = axes(d, x_label="x", y_label="prediction")
        coord = lambda x, y: xy((x - xmin) / (xmax - xmin), (y - ymin) / (ymax - ymin))
        d.poly(
            [coord(a[0], a[2]) for a in rows]
            + [coord(a[0], a[3]) for a in reversed(rows)],
            closed=True,
            fill=d.glass,
            color=d.blue,
            width=1,
        )
        d.poly([coord(a[0], a[1]) for a in rows], color=d.blue, width=3)
        d.facts["intervals_source"] = "provided_lower_and_upper_bounds"
    elif v in {"lorenz", "pareto", "treemap", "bullet"}:
        nums = values(p, maximum=12, positive=True)
        names = labels(p, len(nums)) if v != "lorenz" else []
        if v == "lorenz":
            xy = axes(d, x_label="population share", y_label="value share")
            total = sum(nums)
            cumulative = 0
            pts = [xy(0, 0)]
            for i, n in enumerate(sorted(nums)):
                cumulative += n
                pts.append(xy((i + 1) / len(nums), cumulative / total))
            d.line(*xy(0, 0), *xy(1, 1), color=d.muted, dashed=True)
            d.poly(pts, color=d.blue, width=3)
            cumulative = 0
            area = 0
            for n in sorted(nums):
                previous = cumulative
                cumulative += n / total
                area += (previous + cumulative) / 2 / len(nums)
            d.facts["gini"] = 1 - 2 * area
        elif v == "pareto":
            pairs = sorted(zip(nums, names), reverse=True)
            xy = axes(d, x_label="category", y_label="count")
            peak = max(nums)
            total = sum(nums)
            cum = 0
            pts = []
            for i, (n, label) in enumerate(pairs):
                w = 340 / len(nums)
                x = 70 + i * w
                h = n / peak * 185
                d.rect(x, 260 - h, w - 7, h, fill=d.blue, width=1)
                cum += n
                pts.append((x + w / 2, 260 - cum / total * 195))
                text(d, label, x + w / 2, 285, size=10)
            d.poly(pts, color=d.red)
            text(d, "cumulative %", 367, 32, size=12, color=d.red)
        elif v == "treemap":
            # Alternating strip partition, exact areas, deterministic order.
            remaining = sum(nums)
            x, y, w, h = 55, 45, 370, 220
            colors = [d.blue, d.green, d.gold, d.red, d.glass, d.surface]
            for i, (n, label) in enumerate(zip(nums, names)):
                ratio = n / remaining
                if w >= h:
                    ww = w * ratio
                    d.rect(x, y, ww, h, fill=colors[i % 6], width=1)
                    text(
                        d, label, x + ww / 2, y + h / 2, size=max(8, min(13, ww * 0.6))
                    )
                    x += ww
                    w -= ww
                else:
                    hh = h * ratio
                    d.rect(x, y, w, hh, fill=colors[i % 6], width=1)
                    text(
                        d, label, x + w / 2, y + hh / 2, size=max(8, min(13, hh * 0.6))
                    )
                    y += hh
                    h -= hh
                remaining -= n
        else:
            target = p["target"]
            mx = max(target * 1.2, max(nums))
            spacing = 205 / len(nums)
            for i, (n, label) in enumerate(zip(nums, names)):
                y = 55 + i * spacing
                text(d, label, 42, y + 16, size=10)
                for start, end, c in [
                    (0, 0.6, d.surface),
                    (0.6, 0.85, d.glass),
                    (0.85, 1, d.muted),
                ]:
                    d.rect(
                        83 + start * 340, y, (end - start) * 340, 25, fill=c, width=0.5
                    )
                d.rect(83, y + 8, n / mx * 340, 9, fill=d.blue, width=0.5)
                x = 83 + target / mx * 340
                d.line(x, y - 3, x, y + 28, color=d.red, width=2)
            d.facts["target"] = target
    elif v == "sankey":
        flows = p["flows"]
        if not 2 <= len(flows) <= 4 or any(
            not isinstance(r, list)
            or not 2 <= len(r) <= 3
            or len(r) != len(flows[0])
            or any(type(n) not in {int, float} or n < 0 for n in r)
            for r in flows
        ):
            raise DiagramError("diagram_invalid_flows")
        total = sum(map(sum, flows))
        if total <= 0 or any(sum(r) <= 0 for r in flows):
            raise DiagramError("diagram_invalid_flows")
        scale = 175 / total
        left = []
        y = 50
        for r in flows:
            left.append(y)
            y += sum(r) * scale + 14
        right = []
        y = 50
        for j in range(len(flows[0])):
            right.append(y)
            y += sum(r[j] for r in flows) * scale + 14
        offsets = right[:]
        colors = [d.blue, d.green, d.gold, d.red]
        for i, row in enumerate(flows):
            a = left[i]
            for j, n in enumerate(row):
                if not n:
                    continue
                b = offsets[j]
                h = n * scale
                d.path(
                    f"M 104 {a} C 230 {a} 250 {b} 376 {b} L 376 {b+h} C 250 {b+h} 230 {a+h} 104 {a+h} Z",
                    fill=colors[i],
                    color=colors[i],
                    width=0.5,
                )
                a += h
                offsets[j] += h
            d.rect(90, left[i], 14, sum(row) * scale, fill=d.ink, width=0.5)
            text(d, chr(65 + i), 65, left[i] + sum(row) * scale / 2 + 4)
        for j, start in enumerate(right):
            h = sum(r[j] for r in flows) * scale
            d.rect(376, start, 14, h, fill=d.ink, width=0.5)
            text(d, str(j + 1), 417, start + h / 2 + 4)
        d.facts.update(total_flow=total, flow_matrix=flows)
    elif v == "slope":
        rows = _points(p)
        names = labels(p, len(rows))
        lo, hi = _scale(list(itertools.chain.from_iterable(rows)))
        for x in [135, 345]:
            d.line(x, 45, x, 260, color=d.muted, width=1)
        for i, (a, b) in enumerate(rows):
            ya = 260 - (a - lo) / (hi - lo) * 215
            yb = 260 - (b - lo) / (hi - lo) * 215
            c = [d.blue, d.red, d.green, d.gold][i % 4]
            d.line(135, ya, 345, yb, color=c)
            d.circle(135, ya, 4, fill=c)
            d.circle(345, yb, 4, fill=c)
            text(d, names[i], 104, ya + 4, size=10)
        text(d, "before", 135, 292)
        text(d, "after", 345, 292)
    elif v == "kde_contours":
        rows = _points(p)
        xmin, xmax = _scale([a for a, b in rows])
        ymin, ymax = _scale([b for a, b in rows])
        points = [
            ((a - xmin) / (xmax - xmin), (b - ymin) / (ymax - ymin)) for a, b in rows
        ]
        xy = axes(d)
        # Marching squares with linear edge interpolation, rather than fake nested ellipses.
        n = 25
        band = 0.13
        grid = [
            [
                sum(
                    math.exp(-((x / n - a) ** 2 + (y / n - b) ** 2) / (2 * band**2))
                    for a, b in points
                )
                for y in range(n + 1)
            ]
            for x in range(n + 1)
        ]
        peak = max(map(max, grid))
        for level in [0.2, 0.4, 0.65, 0.85]:
            threshold = peak * level
            for x in range(n):
                for y in range(n):
                    corners = [(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)]
                    crossings = []
                    for a, b in zip(corners, corners[1:] + corners[:1]):
                        aa, bb = grid[a[0]][a[1]], grid[b[0]][b[1]]
                        if (aa < threshold) != (bb < threshold):
                            t = (threshold - aa) / (bb - aa)
                            crossings.append(
                                xy(
                                    (a[0] + (b[0] - a[0]) * t) / n,
                                    (a[1] + (b[1] - a[1]) * t) / n,
                                )
                            )
                    for i in range(0, len(crossings) - 1, 2):
                        d.line(
                            *crossings[i], *crossings[i + 1], color=d.blue, width=1.2
                        )
        for a, b in points:
            d.circle(*xy(a, b), 2, fill=d.red, width=0.5)
        d.facts.update(kernel="gaussian", bandwidth_normalized=band)
    elif v in {"binomial", "poisson", "chi_square", "f_distribution"}:
        xy = axes(
            d,
            x_label="k" if v in {"binomial", "poisson"} else "x",
            y_label="probability" if v in {"binomial", "poisson"} else "density",
        )
        if v in {"binomial", "poisson"}:
            n = (
                p["n"]
                if v == "binomial"
                else min(25, math.ceil(p["rate"] + 4 * math.sqrt(p["rate"])))
            )
            prob = p.get("probability", 0)
            probs = [
                (
                    math.comb(n, k) * prob**k * (1 - prob) ** (n - k)
                    if v == "binomial"
                    else math.exp(-p["rate"]) * p["rate"] ** k / math.factorial(k)
                )
                for k in range(n + 1)
            ]
            peak = max(probs)
            for k, q in enumerate(probs):
                a = xy(k / (n + 1) + 0.02, 0)
                b = xy(k / (n + 1) + 0.02, q / peak * 0.85)
                d.line(*a, *b, color=d.blue, width=3)
                d.circle(*b, 3, fill=d.blue, width=0.5)
            d.facts.update(probabilities=probs, distribution=v)
        else:
            if v == "chi_square":
                df = p["df"]
                limit = df + 5 * math.sqrt(2 * df)
                fn = lambda x: math.exp(
                    (df / 2 - 1) * math.log(max(x, 1e-5))
                    - x / 2
                    - df / 2 * math.log(2)
                    - math.lgamma(df / 2)
                )
            else:
                a, b = p["df1"], p["df2"]
                limit = 6
                fn = lambda x: math.exp(
                    a / 2 * math.log(a / b)
                    + (a / 2 - 1) * math.log(max(x, 1e-5))
                    - (a + b) / 2 * math.log1p(a * x / b)
                    - math.lgamma(a / 2)
                    - math.lgamma(b / 2)
                    + math.lgamma((a + b) / 2)
                )
            peak = max(fn(limit * i / 200) for i in range(1, 201))
            curve(d, lambda t: fn(t * limit) / peak * 0.9, xy, start=0.005)
            d.facts.update(distribution=v, domain_max=limit, parameters=p)
    elif v == "sampling_distribution":
        nums = values(p, minimum=6, maximum=80)
        population = values({"values": p["population"]}, minimum=6, maximum=80)
        lo, hi = _scale(population + nums)
        histograms = []
        for y0, data, title, color in [
            (130, population, "population", d.gold),
            (265, nums, "sample statistics", d.blue),
        ]:
            counts = [0] * 12
            for value in data:
                counts[min(11, int((value - lo) / (hi - lo) * 12))] += 1
            d.line(55, y0, 425, y0)
            d.line(55, y0, 55, y0 - 88)
            for i, count in enumerate(counts):
                h = 80 * count / max(counts)
                d.rect(
                    56 + i * 370 / 12, y0 - h, 370 / 12 - 2, h, fill=color, width=0.5
                )
            text(d, title, 240, y0 - 94, size=13)
            histograms.append(counts)
        text(d, f"{lo:.2g}", 55, 288, size=11)
        text(d, f"{hi:.2g}", 425, 288, size=11)
        text(d, "shared value scale · frequency within each panel", 240, 310, size=11)
        d.facts.update(
            bin_counts=histograms,
            provided_sample_statistics=nums,
            population=population,
            shared_domain=[lo, hi],
        )
    elif v == "permutation_distribution":
        groups = _groups(p)
        if len(groups) != 2 or sum(map(len, groups)) > 12:
            raise DiagramError("diagram_permutation_limit")
        nums = groups[0] + groups[1]
        n = len(groups[0])
        total = sum(nums)
        stats = []
        for indices in itertools.combinations(range(len(nums)), n):
            s = sum(nums[i] for i in indices)
            stats.append(s / n - (total - s) / (len(nums) - n))
        d.facts["bin_counts"] = _hist(d, stats)
        d.facts.update(
            permutations=len(stats),
            observed_difference=statistics.mean(groups[0]) - statistics.mean(groups[1]),
        )
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
