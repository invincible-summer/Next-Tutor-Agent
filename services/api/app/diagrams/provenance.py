"""Traceable drawing implementations, not a claim of worldwide uniqueness."""

import hashlib
import inspect
import json
from functools import lru_cache
from pathlib import Path

from .taxonomy import LEVELS

ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = ROOT / "assets" / "diagram_library" / "review.json"
FACT_REFERENCES = {
    "chemistry.water": (
        "https://openstax.org/books/chemistry/pages/8-2-hybrid-atomic-orbitals",
        "水分子104.5°弯曲键角；仅核对事实，未使用外部图形",
    ),
    "biology.chloroplast": (
        "https://openstax.org/books/biology-2e/pages/8-1-overview-of-photosynthesis",
        "双层包膜、基粒与基质；仅核对事实，未使用外部图形",
    ),
    "chemistry_extended.hydration_shell": (
        "https://openstax.org/books/biology-2e/pages/2-2-water",
        "氧端朝向阳离子的水合取向；仅核对事实，未使用外部图形",
    ),
    "geography_extended.artesian_aquifer": (
        "https://www.usgs.gov/water-science-school/science/artesian-water-and-artesian-wells",
        "承压水头与自流井条件；仅核对事实，未使用外部图形",
    ),
    "geography_extended.coastal_upwelling": (
        "https://oceanservice.noaa.gov/facts/upwelling.html",
        "离岸表层输运与深层补偿；仅核对事实，未使用外部图形",
    ),
    "geography_extended.inversion_layer": (
        "https://www.weather.gov/lmk/inversion",
        "逆温层气温随高度升高；仅核对事实，未使用外部图形",
    ),
    "physics_extended.closed_pipe_modes": (
        "https://openstax.org/books/college-physics/pages/17-5-sound-interference-and-resonance-standing-waves-in-air-columns",
        "闭端为位移节点、开端为位移腹点；仅核对事实，未使用外部图形",
    ),
    "chemistry_extended.tetrahedral": (
        "https://openstax.org/books/chemistry-2e/pages/7-6-molecular-structure-and-polarity",
        "分子构型及键角；未使用该书图形",
    ),
    "chemistry_extended.trigonal_pyramidal": (
        "https://openstax.org/books/chemistry-2e/pages/7-6-molecular-structure-and-polarity",
        "孤电子对和三角锥构型；未使用该书图形",
    ),
    "chemistry_extended.trigonal_planar": (
        "https://openstax.org/books/chemistry-2e/pages/7-6-molecular-structure-and-polarity",
        "平面三角构型；未使用该书图形",
    ),
    "biology_extended.pcr": (
        "https://openstax.org/books/biology-2e/pages/17-1-biotechnology",
        "变性、退火、延伸的顺序；未使用该书图形",
    ),
    "biology_extended.electrophoresis": (
        "https://openstax.org/books/biology-2e/pages/17-1-biotechnology",
        "电泳分离方向及片段大小关系；未使用该书图形",
    ),
    "physics_extended.polarizers": (
        "https://openstax.org/books/university-physics-volume-3/pages/1-7-polarization",
        "偏振片方向与透射关系；未使用该书图形",
    ),
    "economics_extended.ppf": (
        "https://openstax.org/books/principles-economics-2e/pages/2-2-the-production-possibilities-frontier-and-social-choices",
        "生产边界及机会成本关系；未使用该书图形",
    ),
}


@lru_cache(maxsize=1)
def review_records():
    return json.loads(REVIEW_PATH.read_text()) if REVIEW_PATH.exists() else {}


@lru_cache(maxsize=32)
def source_hash(module):
    directory = Path(__file__).parent
    dependencies = [
        Path(module),
        directory / "drawing.py",
        ROOT / "scripts/build_diagram_catalog.py",
    ]
    if Path(module).name.startswith("extended"):
        dependencies.extend(
            [
                directory / "extended_common.py",
                directory / "instruments.py",
                directory / "extended.py",
                directory / "extended_deferred.py",
            ]
        )
    if Path(module).name == "templates.py":
        dependencies.extend(
            directory / name
            for name in [
                "instruments.py",
                "mathematics.py",
                "physics.py",
                "life_earth.py",
                "systems.py",
            ]
        )
    digest = hashlib.sha256()
    for path in sorted(set(dependencies)):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def source_record(asset_id, renderer, variant):
    from .registry import extension, renderers

    item = extension(asset_id)
    fn = item.draw if item else renderers()[renderer]
    module = Path(inspect.getfile(fn))
    # Include direct composition dependencies; modifications invalidate review.
    derived = []
    if renderer == "template" and not item:
        from .templates import RECIPES

        derived = list(dict.fromkeys(row[0] for row in RECIPES.get(variant, [])))
    if module.name == "extended_experiments.py" and variant in {
        "gas_water",
        "gas_up",
        "gas_down",
        "gas_preparation",
        "gas_wash",
        "gas_dry",
        "extraction",
        "respiration",
    }:
        derived = ["vessel.conical_flask"]
    if module.name == "extended_experiments.py" and variant == "distillation":
        derived = ["vessel.distilling_flask"]
    if module.name == "extended_chemistry.py":
        derived = {
            "reflux": ["vessel.round_flask"],
            "fractional_distillation": ["vessel.round_flask", "vessel.conical_flask"],
            "rotary_evaporator": ["vessel.round_flask"],
            "leak_check": ["vessel.conical_flask"],
            "gas_syringe": ["vessel.conical_flask"],
            "pipette_transfer": ["vessel.volumetric_flask"],
            "standard_solution": ["vessel.volumetric_flask"],
        }.get(variant, [])
    refs = []
    if asset_id in FACT_REFERENCES:
        url, purpose = FACT_REFERENCES[asset_id]
        refs = [
            {
                "url": url,
                "purpose": purpose,
                "checked_at": "2026-10-03" if asset_id in {
                    "chemistry.water", "biology.chloroplast", "chemistry_extended.hydration_shell",
                    "geography_extended.artesian_aquifer", "geography_extended.coastal_upwelling",
                    "geography_extended.inversion_layer", "physics_extended.closed_pipe_modes",
                } else "2026-10-02",
                "type": "fact_only",
            }
        ]
    return {
        "origin": "project_procedural",
        "creation_method": "ai_assisted_vector_code",
        "source_module": str(module.relative_to(ROOT)),
        "source_symbol": fn.__name__ + ":" + variant,
        "source_hash": source_hash(str(module)),
        "external_graphics": [],
        "derived_from": derived,
        "knowledge_references": refs,
        "statement": "项目独立编写矢量路径；通用符号与知识事实不代表独占造型。",
    }


def enrich(asset):
    subject = asset["category"]
    renderer = asset["renderer"]
    variant = asset["variant"]
    subjects = [subject]
    if renderer in {"vessel", "apparatus", "measurement"}:
        subjects = list(dict.fromkeys([subject, "physics", "chemistry", "biology"]))
    if variant in {
        "seasons",
        "moon_phase",
        "eclipse",
        "lunar_eclipse",
        "orbit",
        "sun",
        "moon",
        "earth",
        "globe",
    }:
        subjects = list(dict.fromkeys(subjects + ["astronomy"]))
    if variant in {"water_cycle", "food_web", "ecosystem", "runoff", "groundwater"}:
        subjects = list(
            dict.fromkeys(subjects + ["environment", "biology", "geography"])
        )
    if variant in {"supply_demand", "cashflow", "payoff"}:
        subjects = list(dict.fromkeys(subjects + ["economics"]))
    if renderer == "objects":
        subjects = list(dict.fromkeys(subjects + ["engineering"]))
    levels = ["junior", "senior", "undergraduate"]
    if subject in {
        "language",
        "history",
        "music",
        "visual_art",
        "sports",
        "agriculture",
        "environment",
    }:
        levels = list(LEVELS)
    if (
        renderer == "geometry"
        or subject == "mathematics"
        and variant
        in {
            "tangram",
            "area_dissection",
            "unit_cubes",
            "grouped_array",
            "equivalent_fractions",
            "equation_balance",
        }
    ):
        levels = list(LEVELS)
    if subject in {"economics", "statistics"} or variant in {
        "nine_point_circle",
        "plane_normal",
        "chirality",
        "hybrid_orbitals",
        "nmr_spectrum",
        "kde_contours",
        "hr_diagram",
    }:
        levels = ["senior", "undergraduate"]
    kind = "scene" if "extended" in renderer or renderer == "template" else "object"
    if renderer in {"geometry", "circuit", "logic"}:
        kind = "symbol"
    if renderer in {"chart", "function"} or subject == "statistics":
        kind = "chart"
    if any(
        word in variant
        for word in [
            "sequence",
            "cycle",
            "process",
            "stages",
            "construction",
            "preparation",
            "transfer",
            "replication",
            "transcription",
            "translation",
            "dfs",
            "bfs",
            "topological",
        ]
    ):
        kind = "sequence"
    asset.update(
        subjects=subjects,
        education_levels=levels,
        asset_kind=kind,
        topics=[subject, variant],
    )
    asset["provenance"] = source_record(asset["id"], renderer, variant)
    records = review_records()
    review = records.get(asset["id"], {})
    asset["review"] = (
        review
        if review.get("source_hash") == asset["provenance"]["source_hash"]
        else {
            "status": "pending",
            "automatic": "pending",
            "structure": "pending",
            "visual": "pending",
            "reviewer_type": "agent",
        }
    )
    return asset
