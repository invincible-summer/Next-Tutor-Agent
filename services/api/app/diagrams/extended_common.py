"""Project-authored composition primitives, shared by the second inventory."""

from __future__ import annotations

import math

from .drawing import Drawing, num
from .schema import DiagramError


def canvas(mono=False, width=480, height=320):
    return Drawing(width, height, mono)


def text(d, value, x, y, size=14, color=None, anchor="middle"):
    d.text(value, x, y, size=size, color=color, anchor=anchor)


def text_units(value: object) -> float:
    """Approximate rendered width in font-size units for mixed CJK/Latin text."""
    return sum(1.0 if ord(char) > 255 else 0.58 for char in str(value))


def node_size(label, *, size=14, min_width=66, max_width=190, height=36):
    width = max(min_width, min(max_width, text_units(label) * size + 22))
    return width, height


def panel(d, x, y, w, h, fill=None):
    d.rect(x, y, w, h, fill=fill or d.surface, radius=7, width=1.5)


def ball(d, x, y, r, color=None):
    color = color or d.blue
    d.circle(x, y, r, fill=color)
    d.path(
        f"M {num(x-r*.63)} {num(y+r*.18)} Q {num(x-r*.86)} {num(y-r*.5)} {num(x-r*.18)} {num(y-r*.72)}",
        color="#fff",
        width=max(1, r * 0.08),
    )


def box(d, x, y, w, h, depth=16):
    d.rect(x, y, w, h, fill=d.surface)
    d.poly(
        [
            (x, y),
            (x + depth, y - depth * 0.7),
            (x + w + depth, y - depth * 0.7),
            (x + w, y),
        ],
        closed=True,
        fill=d.glass,
    )
    d.poly(
        [
            (x + w, y),
            (x + w + depth, y - depth * 0.7),
            (x + w + depth, y + h - depth * 0.7),
            (x + w, y + h),
        ],
        closed=True,
        fill=d.muted,
    )


def vessel(d, x, y, w=75, h=100, fill=0.4, color=None):
    d.path(
        f"M {x} {y} Q {x+w/2} {y-8} {x+w} {y} L {x+w-5} {y+h-8} L {x+w-15} {y+h} L {x+12} {y+h} L {x+5} {y+h-8} Z",
        fill=d.glass,
    )
    if fill:
        level = y + h * (1 - fill)
        d.poly(
            [
                (x + 4, level),
                (x + w - 4, level),
                (x + w - 5, y + h - 8),
                (x + w - 15, y + h),
                (x + 12, y + h),
                (x + 5, y + h - 8),
            ],
            closed=True,
            fill=color or d.blue,
            color=color or d.blue,
            width=1,
        )
        d.line(x + 4, level, x + w - 4, level, color=d.ink, width=1)
    d.line(x + 13, y + 15, x + 15, y + h - 18, color="#fff", width=3)
    d.path(f"M {x-4} {y-1} Q {x+w/2} {y-9} {x+w} {y} l 8 -5")


def stand(d, x, y, h=190):
    d.rect(x - 38, y + h, 78, 9, fill=d.muted, radius=3)
    d.line(x, y, x, y + h, width=4)
    d.line(x, y + 40, x + 48, y + 40, width=3)
    d.circle(x, y + 40, 5, fill=d.gold)


def burner(d, x, y, lit=True):
    d.path(
        f"M {x-20} {y+10} Q {x-27} {y+46} {x} {y+47} Q {x+27} {y+46} {x+20} {y+10} Z",
        fill=d.glass,
    )
    d.ellipse(x, y + 10, 20, 5, fill=d.surface)
    d.rect(x - 4, y - 3, 8, 12, fill=d.surface)
    if lit:
        d.path(
            f"M {x} {y-36} C {x-4} {y-22} {x-19} {y-15} {x} {y-4} C {x+18} {y-16} {x+6} {y-29} {x} {y-36} Z",
            fill=d.gold,
            color=d.gold,
        )
        d.path(
            f"M {x} {y-18} Q {x-8} {y-7} {x} {y-4} Q {x+7} {y-7} {x} {y-18} Z",
            fill=d.blue,
            color=d.blue,
        )


def pipe(d, points):
    d.poly(points, color=d.blue, width=6)
    d.poly(points, color=d.glass, width=2)


def leaf(d, x, y, w=45, h=22, rotate=0):
    part = Drawing(100, 80, d.monochrome)
    part.path(
        f"M 0 0 Q {w*.4} {-h} {w} 0 Q {w*.4} {h} 0 0 Z", fill=d.green, color=d.green
    )
    part.line(0, 0, w * 0.85, 0, color=d.ink, width=1)
    d.add(part, x, y, 1, rotate)


def plant(d, x, y, height=90):
    d.path(f"M {x} {y} Q {x-8} {y-height*.5} {x+6} {y-height}", color=d.green, width=3)
    leaf(d, x, y - height * 0.45, rotate=-25)
    leaf(d, x, y - height * 0.65, rotate=200)


def axes(d, x=55, y=260, w=370, h=215, *, x_label="x", y_label="y", ticks=True):
    d.arrow(x, y, x + w, y)
    d.arrow(x, y, x, y - h)
    # Endpoint numbers occupy the first row below the axis. Keep its name
    # on a separate baseline for spectra, climate and statistical charts.
    text(d, x_label, x + w, min(d.height - 8, y + 50), anchor="end")
    text(d, y_label, x - 25, y - h - 9, anchor="start")
    if ticks:
        for i in range(1, 5):
            d.line(x + i * w / 5, y - 3, x + i * w / 5, y + 3, width=1)
            d.line(x - 3, y - i * h / 5, x + 3, y - i * h / 5, width=1)
    return lambda a, b: (x + a * w, y - b * h)


def curve(d, fn, coord, *, color=None, n=65, start=0, end=1, width=2):
    runs = []
    run = []
    for i in range(n):
        x = start + (end - start) * i / (n - 1)
        try:
            y = fn(x)
            if not math.isfinite(y) or not -0.02 <= y <= 1.02:
                raise ValueError()
            run.append(coord(x, y))
        except (ValueError, ZeroDivisionError, OverflowError):
            if len(run) > 1:
                runs.append(run)
            run = []
    if len(run) > 1:
        runs.append(run)
    for points in runs:
        d.poly(points, color=color or d.blue, width=width)


def node(d, label, x, y, w=66, h=36, fill=None):
    # A caller supplied width remains a lower bound; labels determine the
    # actual box width so Chinese and long prompts never protrude.
    w = max(w, node_size(label, height=h)[0])
    panel(d, x - w / 2, y - h / 2, w, h, fill)
    text(d, label, x, y + 5)
    return w, h


def connect(d, a, b, *, color=None, dashed=False):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1:
        return
    if dashed:
        d.line(*a, *b, color=color, dashed=True)
    else:
        d.arrow(*a, *b, color=color)


def sequence(d, labels, y=130, *, symbols=None):
    n = len(labels)
    spacing = 390 / max(1, n - 1)
    centers = [(45 + i * spacing, y) for i in range(n)]
    widths = [node_size(label, max_width=max(66, spacing - 16))[0] for label in labels]
    for i, ((x, cy), label) in enumerate(zip(centers, labels)):
        if symbols:
            symbols(i, x, cy)
        else:
            node(d, label, x, cy, w=widths[i])
        if symbols:
            text(d, label, x, cy + 74)
        if i:
            connect(d, (centers[i - 1][0] + widths[i - 1] / 2, cy),
                    (x - widths[i] / 2, cy))
    return centers


def values(p, *, key="values", minimum=2, maximum=32, positive=False):
    rows = p.get(key)
    if not isinstance(rows, list) or not minimum <= len(rows) <= maximum:
        raise DiagramError("diagram_data_required")
    if any(
        isinstance(v, bool)
        or not isinstance(v, (float, int))
        or not math.isfinite(v)
        or abs(v) > 10000
        or positive
        and v <= 0
        for v in rows
    ):
        raise DiagramError("diagram_invalid_data")
    return [float(v) for v in rows]


def labels(p, n):
    rows = p.get("labels") or [chr(65 + i) for i in range(n)]
    if len(rows) != n or any(not isinstance(s, str) or len(s) > 10 for s in rows):
        raise DiagramError("diagram_invalid_labels")
    return rows


def numeric(name, default, low, high, integer=False):
    return {
        name: {
            "type": "integer" if integer else "number",
            "default": default,
            "minimum": low,
            "maximum": high,
        }
    }


def dataset(sample, *, minimum=2, maximum=32):
    return {"values": {"type": "list", "required": True}}
