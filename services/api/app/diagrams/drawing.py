"""Small XML-only drawing vocabulary, with no CSS, references or executable content."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)


def num(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("non-finite coordinate")
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


@dataclass
class Drawing:
    width: float = 160
    height: float = 160
    monochrome: bool = False
    parts: list[ET.Element] = field(default_factory=list)
    anchors: dict[str, tuple[float, float]] = field(default_factory=dict)
    facts: dict = field(default_factory=dict)

    def __post_init__(self):
        self.ink = "#26364a"
        self.muted = "#8a99a9"
        self.surface = "#eef2f6"
        self.glass = "#e2f0f5"
        self.blue = "#5a8da7"
        self.red = "#b76e61"
        self.green = "#719785"
        self.gold = "#bc9460"
        if self.monochrome:
            self.ink, self.muted = "#222", "#777"
            self.surface, self.glass = "#f5f5f5", "#eee"
            self.blue, self.red, self.green, self.gold = "#888", "#aaa", "#ccc", "#666"
        self.anchors.update({"center": (self.width / 2, self.height / 2),
                             "top": (self.width / 2, 0),
                             "bottom": (self.width / 2, self.height),
                             "left": (0, self.height / 2),
                             "right": (self.width, self.height / 2)})

    def element(self, tag: str, **attrs) -> ET.Element:
        element = ET.Element(f"{{{NS}}}{tag}")
        for key, value in attrs.items():
            key = key.replace("_", "-")
            if key == "stroke-width":
                value = max(.25, min(8, value))
            element.set(key, num(value) if isinstance(value, (int, float)) else str(value))
        self.parts.append(element)
        return element

    def line(self, x1, y1, x2, y2, *, color=None, width=2, dashed=False):
        attrs = dict(x1=x1, y1=y1, x2=x2, y2=y2, stroke=color or self.ink,
                     stroke_width=width, stroke_linecap="round", fill="none")
        if dashed:
            attrs["stroke_dasharray"] = "5 4"
        return self.element("line", **attrs)

    def rect(self, x, y, w, h, *, fill=None, color=None, radius=0, width=2):
        return self.element("rect", x=x, y=y, width=w, height=h, rx=radius,
                            fill=fill or "none", stroke=color or self.ink, stroke_width=width)

    def circle(self, x, y, r, *, fill=None, color=None, width=2):
        return self.element("circle", cx=x, cy=y, r=r, fill=fill or "none",
                            stroke=color or self.ink, stroke_width=width)

    def ellipse(self, x, y, rx, ry, *, fill=None, color=None, width=2):
        return self.element("ellipse", cx=x, cy=y, rx=rx, ry=ry, fill=fill or "none",
                            stroke=color or self.ink, stroke_width=width)

    def path(self, data, *, fill=None, color=None, width=2, dashed=False):
        attrs = dict(d=data, fill=fill or "none", stroke=color or self.ink,
                     stroke_width=width, stroke_linecap="round", stroke_linejoin="round")
        if dashed:
            attrs["stroke_dasharray"] = "5 4"
        return self.element("path", **attrs)

    def poly(self, points, *, fill=None, color=None, closed=False, width=2):
        if not closed and len(points) > 100:
            for start in range(0, len(points) - 1, 99):
                self.poly(points[start:start+100], fill=fill, color=color, width=width)
            return self.parts[-1]
        return self.element("polygon" if closed else "polyline",
                            points=" ".join(f"{num(x)},{num(y)}" for x, y in points),
                            fill=fill or "none", stroke=color or self.ink,
                            stroke_width=width, stroke_linejoin="round")

    def text(self, text, x, y, *, size=18, anchor="middle", color=None):
        element = self.element("text", x=x, y=y, fill=color or self.ink,
                               stroke="none", font_size=size, font_family="sans-serif",
                               text_anchor=anchor)
        element.text = str(text)
        return element

    def arrow(self, x1, y1, x2, y2, *, color=None, width=2, double=False):
        self.line(x1, y1, x2, y2, color=color, width=width)
        angle = math.atan2(y2 - y1, x2 - x1)
        for x, y, a in [(x2, y2, angle)] + ([(x1, y1, angle + math.pi)] if double else []):
            self.poly([(x - 8 * math.cos(a - .45), y - 8 * math.sin(a - .45)),
                       (x, y), (x - 8 * math.cos(a + .45), y - 8 * math.sin(a + .45))],
                      color=color, width=width)

    def hatch(self, x, y, w, h, *, spacing=12):
        for offset in range(0, int(w + h), spacing):
            x1, y1 = x + max(0, offset - h), y + min(h, offset)
            x2, y2 = x + min(w, offset), y + max(0, offset - w)
            self.line(x1, y1, x2, y2, color=self.muted, width=1)

    def add(self, other: "Drawing", x=0, y=0, scale=1, rotate=0):
        group = self.element("g", transform=f"translate({num(x)} {num(y)}) rotate({num(rotate)}) scale({num(scale)})")
        group.extend(other.parts)
        return group

    def svg(self) -> str:
        root = ET.Element(f"{{{NS}}}svg", {"viewBox": f"0 0 {num(self.width)} {num(self.height)}"})
        root.extend(self.parts)
        return ET.tostring(root, encoding="unicode")
