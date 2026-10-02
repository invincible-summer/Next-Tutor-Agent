"""Explicit second-edition registrations and clearly separated preview examples."""

import math

from . import extended_math, extended_physics, extended_chemistry, extended_biology
from . import extended_earth, extended_statistics, extended_systems, extended_humanities
from . import extended_engineering, extended_creative
from . import extended_deferred, extended_experiments
from .extended_inventory import entries
from .registry import Renderer

SUBJECT_RENDERERS = {
    "mathematics": extended_math,
    "physics": extended_physics,
    "chemistry": extended_chemistry,
    "biology": extended_biology,
    "geography": extended_earth,
    "agriculture": extended_earth,
    "environment": extended_earth,
    "statistics": extended_statistics,
    "systems": extended_systems,
    "language": extended_humanities,
    "history": extended_humanities,
    "economics": extended_humanities,
    "engineering": extended_engineering,
    "astronomy": extended_engineering,
    "music": extended_creative,
    "visual_art": extended_creative,
    "sports": extended_creative,
}


def registrations():
    rows = {
        a["id"]: Renderer(
            SUBJECT_RENDERERS[a["category"]].draw,
            SUBJECT_RENDERERS[a["category"]].parameters,
        )
        for a in entries()
    }
    rows.update(
        {
            f"{family}.{variant}": Renderer(
                extended_deferred.draw, extended_deferred.parameters
            )
            for family, variants in extended_deferred.VARIANTS.items()
            for variant in variants
        }
    )
    rows.update(
        {
            f"template.{variant}": Renderer(
                extended_experiments.draw, extended_experiments.parameters
            )
            for variant in extended_experiments.VARIANTS
        }
    )
    return rows


def restored_sample(renderer, variant):
    if variant == "pedigree":
        return {
            "people": [
                ["I-1", 1, 0, 0],
                ["I-2", 0, 0, 0],
                ["II-1", 1, 1, 1],
                ["II-2", 0, 0, 1],
            ],
            "couples": [[0, 1]],
            "children": [[0, 2], [0, 3]],
        }
    if variant == "cashflow":
        return {"items": ["t0", "t1", "t2", "t3"], "values": [-100, 35, 45, 55]}
    if variant == "gantt":
        return {
            "items": ["A", "B", "C", "D"],
            "values": [[0, 3], [2, 6], [4, 8], [8, 10]],
        }
    if variant in {"ecosystem", "hasse", "bipartite", "er", "class", "double_list"}:
        return {
            "items": ["A", "B", "C", "D"],
            "edges": (
                [[0, 2], [0, 3], [1, 2], [1, 3]]
                if variant == "bipartite"
                else [[0, 1], [0, 2], [1, 3], [2, 3]]
            ),
        }
    if variant == "experiment_control":
        return {"items": ["treatment", "control", "same conditions"]}
    if variant == "box_comparison":
        return {"groups": [[2, 4, 5, 6, 9], [4, 6, 7, 9, 12]], "labels": ["A", "B"]}
    if variant == "scatter_fit":
        return {"points": [[1, 2], [2, 4], [3, 4], [4, 6], [5, 7], [6, 9]]}
    return {}


def sample_params(subject, v):
    """Gallery-only examples; never injected into an assessment scene."""
    if subject == "chemistry" and v in extended_chemistry.DATA_VARIANTS:
        points = {
            "titration_curve": [
                [i, 3 + 8 / (1 + math.exp(-(i - 12)))] for i in range(25)
            ],
            "solubility_curve": [[i * 10, 12 + i * i * 1.3] for i in range(11)],
            "gas_burette": [[i, 80 * (1 - math.exp(-i / 5))] for i in range(21)],
            "equilibrium_time": [[i, 2 + 6 * math.exp(-i / 3)] for i in range(21)],
            "mass_spectrum": [[15, 13], [29, 37], [43, 100], [58, 22], [72, 44]],
            "ir_spectrum": [
                [
                    500 + i * 60,
                    85
                    - 55 * math.exp(-(((i - 20) / 2) ** 2))
                    - 35 * math.exp(-(((i - 43) / 5) ** 2)),
                ]
                for i in range(59)
            ],
            "nmr_spectrum": [
                [
                    i / 8,
                    3
                    + 85 * math.exp(-(((i - 15) / 1.2) ** 2))
                    + 55 * math.exp(-(((i - 40) / 1.6) ** 2)),
                ]
                for i in range(65)
            ],
        }
        labels = {
            "titration_curve": ("V", "pH"),
            "solubility_curve": ("T", "solubility"),
            "gas_burette": ("t", "V"),
            "equilibrium_time": ("t", "c"),
            "mass_spectrum": ("m/z", "relative I"),
            "ir_spectrum": ("wavenumber", "T (%)"),
            "nmr_spectrum": ("δ (ppm)", "I"),
        }
        return {"points": points[v], "x_label": labels[v][0], "y_label": labels[v][1]}
    if v == "electrophoresis":
        return {
            "lanes": [
                [8000, 5000, 2500, 1000, 300],
                [6500, 1800, 500],
                [5000, 2500, 700],
                [8000, 1000],
            ]
        }
    if v == "niche_resources":
        return {
            "points": [
                [
                    i / 15,
                    math.exp(-(((i / 15 - 0.37) / 0.19) ** 2)) * 0.8,
                    math.exp(-(((i / 15 - 0.65) / 0.19) ** 2)) * 0.8,
                ]
                for i in range(16)
            ]
        }
    if v == "climate_plot":
        return {
            "temperature": [3, 5, 10, 16, 21, 25, 28, 27, 23, 17, 10, 5],
            "rainfall": [30, 34, 50, 72, 90, 128, 145, 138, 83, 50, 36, 28],
        }
    if v == "travel_time":
        return {"points": [[i * 100, i * 16, i * 28] for i in range(1, 9)]}
    if subject == "statistics":
        if v in {"violin", "beeswarm", "raincloud"}:
            return {
                "groups": [[2, 3, 4, 4, 5, 6, 7, 8, 9], [4, 5, 6, 7, 7, 8, 10, 11, 13]]
            }
        if v == "permutation_distribution":
            return {"groups": [[2, 4, 5, 7], [3, 6, 8, 9]]}
        if v in {"residuals", "agreement", "kde_contours"}:
            return {
                "points": [
                    [1, 2],
                    [2, 4],
                    [3, 3.5],
                    [4, 6],
                    [5, 5.5],
                    [6, 8],
                    [7, 7.5],
                    [8, 10],
                    [9, 11],
                ]
            }
        if v == "prediction_interval":
            return {
                "points": [
                    [
                        i,
                        2 + i * 0.8,
                        1 + i * 0.8 - (i - 4) ** 2 * 0.06,
                        3 + i * 0.8 + (i - 4) ** 2 * 0.06,
                    ]
                    for i in range(9)
                ]
            }
        if v == "slope":
            return {"points": [[3, 6], [7, 5], [9, 11]], "labels": ["A", "B", "C"]}
        if v == "sankey":
            return {"flows": [[8, 3], [4, 7], [5, 2]]}
        if v == "mosaic":
            return {"values": [[8, 3], [4, 7], [5, 2]], "labels": ["A", "B", "C"]}
        if v == "sampling_distribution":
            return {
                "values": [3, 4, 4, 5, 5, 5, 6, 6, 6, 6, 7, 7, 7, 8, 8, 9],
                "population": [1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 11],
            }
        if v in {"lorenz", "pareto", "treemap", "bullet"}:
            return {
                "values": [2, 4, 6, 10],
                **({"labels": ["A", "B", "C", "D"]} if v != "lorenz" else {}),
            }
    if subject == "systems":
        if v in extended_systems.GRAPH_VARIANTS:
            return {
                "items": ["A", "B", "C", "D", "E"],
                "edges": [[0, 1, 2], [0, 2, 5], [1, 3, 3], [2, 3, 1], [3, 4, 2]],
            }
        if v in {"hash_chain", "open_addressing", "b_tree", "red_black"}:
            return {"values": [12, 5, 19, 8, 22, 15, 3]}
        if v == "trie":
            return {"words": ["cat", "car", "dog", "dot"]}
        if v == "round_robin":
            return {"values": [5, 3, 4]}
        if v == "convolution":
            return {
                "input": [[1, 2, 0], [0, 1, 3], [2, 1, 1]],
                "kernel": [[1, 0], [0, -1]],
            }
        if v == "confusion_matrix":
            return {"values": [[28, 4], [3, 25]]}
    if subject == "language" and v in extended_humanities.LANGUAGE_TEXT:
        return {
            **({"edges": [[1, 0], [1, 2]]} if v == "dependency" else {}),
            "items": {
                "english_syllables": ["ed", "u", "ca", "tion"],
                "affixes": ["un", "help", "ful"],
                "dependency": ["Birds", "build", "nests"],
                "agreement": ["she", "writes"],
                "subordinate_clauses": ["main clause", "because", "reason"],
                "paragraph_support": ["claim", "evidence", "reasoning"],
                "general_specific": ["topic", "example A", "example B"],
                "narrative_view": ["narrator", "observer", "character"],
                "dialogue_turns": [
                    "question",
                    "response",
                    "follow-up",
                    "clarification",
                ],
                "rhetorical_relations": ["cause", "effect", "response"],
            }[v],
        }
    if v in {"chronology", "parallel_civilizations"}:
        return {
            "events": [
                [100, "event A", 0],
                [250, "event B", 1],
                [420, "event C", 0],
                [600, "event D", 1],
            ]
        }
    if v == "causality":
        return {"items": ["condition", "trigger", "change", "result"]}
    if v == "evidence_compare":
        return {"items": ["A: context", "B: claim", "shared limits"]}
    if subject == "economics":
        if v in extended_humanities.ECON_DATA:
            points = {
                "utility": [[i, 15 * (1 - math.exp(-i / 4))] for i in range(12)],
                "cost_curves": [
                    [i, 9 + i * 2, i * 0.8 + 20 / max(1, i)] for i in range(1, 12)
                ],
                "profit": [[i, 8 * i, 10 + i * 3 + i * i * 0.25] for i in range(12)],
                "compound_growth": [[i, 100 * 1.08**i, 100 + 8 * i] for i in range(12)],
                "annuity": [[i, 10] for i in range(1, 9)],
                "inventory_model": [[i, 90 - (i % 4) * 22] for i in range(16)],
            }
            return {"points": points[v]}
        if v == "opportunity_cost":
            return {"choices": [[2, 10], [7, 4]]}
        if v == "input_output":
            return {"values": [[12, 8, 6], [4, 18, 9], [7, 5, 14]]}
        if v == "balance_sheet":
            return {"values": [[100, 60, 40], [120, 70, 50]]}
    if v == "hr_diagram":
        return {
            "stars": [
                [30000, 30000],
                [15000, 500],
                [8000, 15],
                [5800, 1],
                [4000, 0.1],
                [3000, 0.01],
                [4000, 1000],
                [3000, 5000],
                [12000, 0.02],
                [20000, 0.05],
            ]
        }
    if v == "melody_contour":
        return {"values": [0, 2, 4, 3, 5, 4, 2, 0]}
    if v == "rhythm_grid":
        return {"values": [1, 0, 1, 1, 0, 1, 0, 1]}
    if v == "heart_rate":
        return {"values": [72, 78, 94, 112, 130, 143, 149, 138, 117, 99, 84]}
    if v == "landing_distribution":
        return {"values": [4, 6, 3, 7, 5, 6, 4, 8]}
    return {}
