"""Explicit renderer registration; no dynamic imports from model/user strings."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from .schema import DiagramError


@dataclass(frozen=True)
class Renderer:
    draw: Callable
    parameters: Callable
    rotation_allowed: bool = False
    version: int = 1


@lru_cache(maxsize=1)
def renderers():
    from .instruments import apparatus, mechanics, vessel
    from .life_earth import biology, earth
    from .mathematics import chart, function_plot, geometry
    from .physics import circuit, measurement, optics, waves
    from .systems import chemistry, graph, logic, objects
    from .templates import template

    rows = {
        "apparatus": apparatus,
        "mechanics": mechanics,
        "vessel": vessel,
        "biology": biology,
        "earth": earth,
        "chart": chart,
        "function": function_plot,
        "geometry": geometry,
        "circuit": circuit,
        "measurement": measurement,
        "optics": optics,
        "waves": waves,
        "chemistry": chemistry,
        "graph": graph,
        "logic": logic,
        "objects": objects,
        "template": template,
    }
    return rows


@lru_cache(maxsize=1)
def extensions():
    from .extended import registrations

    from . import terrestrial_models
    rows = registrations()
    rows.update({aid: Renderer(terrestrial_models.draw, terrestrial_models.parameters, version=2)
        for aid in ("earth.water_cycle", "template.water_cycle", "geography_extended.glacier")})
    return rows


def extension(asset_id: str):
    return extensions().get(asset_id)


def render(renderer: str, variant: str, params: dict, monochrome: bool):
    item = extension(f"{renderer}.{variant}")
    if item:
        return item.draw(variant, params, monochrome)
    try:
        return renderers()[renderer](variant, params, monochrome)
    except KeyError as exc:
        raise DiagramError("diagram_renderer_missing") from exc
