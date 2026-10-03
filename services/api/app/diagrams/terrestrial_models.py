"""Original qualitative sections with explicit scientific geometry."""
import math

from .drawing import Drawing
from .schema import DiagramError


def parameters(_variant):
    return {}


def draw(variant, _params, mono=False):
    d = Drawing(480, 320, mono)
    if variant == "water_cycle":
        # Ocean and land are continuous, rather than detached pictograms.
        d.path("M 25 282 L 25 235 L 106 155 L 170 215 L 267 258 L 340 258 L 340 282 Z",
            fill=d.surface, color=d.gold)
        d.rect(335, 240, 120, 42, fill=d.glass, color=d.blue)
        for y in (251, 263, 275):
            d.path(f"M 342 {y} Q 354 {y-4} 366 {y} T 390 {y} T 414 {y} T 446 {y}", color=d.blue, width=1)
        d.circle(67, 51, 17, fill=d.gold, color=d.gold)
        for i in range(10):
            a = i * math.tau/10
            d.line(67+22*math.cos(a), 51+22*math.sin(a), 67+30*math.cos(a),
                51+30*math.sin(a), color=d.gold, width=1.5)
        # One atmospheric reservoir: evaporation reaches the cloud at right,
        # precipitation returns to land at left; runoff closes the loop.
        d.path("M 208 112 C 180 110 180 76 207 72 C 208 39 251 28 270 56 "
            "C 290 29 333 38 338 68 C 380 53 408 97 378 114 Z", fill=d.surface, color=d.muted)
        d.arrow(394, 235, 368, 123, color=d.blue, width=2.5)
        d.arrow(221, 122, 144, 184, color=d.blue, width=2.5)
        for x, y in ((235, 135), (252, 148), (216, 158)):
            d.line(x, y, x-7, y+12, color=d.blue, width=1.5)
        d.arrow(196, 235, 329, 263, color=d.blue, width=2.5)
        # Solar input is visually distinct from the arrows transporting water.
        d.line(89, 71, 137, 117, color=d.gold, width=1.5, dashed=True)
    elif variant == "glacier":
        d.facts["supported_views"] = ["front_orthographic", "section"]
        # Cross-section: broad floor and steep sides are an actual U-shaped
        # erosional trough, not a slanted glacier drawn over a mountain icon.
        d.path("M 25 115 L 83 68 L 120 146 L 135 210 Q 142 248 183 252 "
            "H 297 Q 338 248 345 210 L 360 146 L 397 68 L 455 115 V 292 H 25 Z",
            fill=d.gold)
        d.path("M 120 146 Q 240 128 360 146 L 345 210 Q 338 248 297 252 "
            "H 183 Q 142 248 135 210 Z", fill=d.glass, color=d.blue)
        for y in (171, 196, 219):
            d.path(f"M {130+(y-170)*.2:g} {y} Q 240 {y+12} {350-(y-170)*.2:g} {y}",
                color=d.blue, width=1)
        for x, y in ((172, 233), (242, 244), (312, 233)):
            d.poly([(x-4, y), (x, y-6), (x+6, y+2)], closed=True, fill=d.muted, width=1)
    else:
        raise DiagramError("diagram_unknown_asset")
    return d
