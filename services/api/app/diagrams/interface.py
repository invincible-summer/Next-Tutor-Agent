"""Material-owned parameter contracts shared by every illustration renderer.

The legacy vocabulary is migrated here once. The scene compiler consumes the
resulting declarations, never asset identities or parameter-name exceptions.
"""
from __future__ import annotations

INTERFACE_VERSION = "1.0.0"
LAYOUT = {"origin": "top_left", "uniform_scale": True,
    "annotations": "measured_outside_placement", "relations": "declared_ports_only"}


def parameter_contract(parameters):
    result = {}
    for key, original in parameters.items():
        spec = dict(original)
        kind = spec["type"]
        role = spec.get("role") or (
            "function" if key == "function" else "range" if key in {"x_range", "y_range", "interval"} else
            "appearance" if not spec.get("condition_bearing", True) or kind == "color" else
            "display" if key.startswith("show_") or key == "scale_labels" else
            "text" if kind in {"string", "enum"} or key == "labels" else
            "state" if kind == "boolean" else "data" if kind in {"array", "list"} else
            "schematic" if spec.get("unit") == "diagram_px" else "quantity")
        fact_types = spec.get("fact_types") or {
            "function": ["function"], "range": ["range", "data"], "text": ["label", "data"]
                if kind in {"array", "list"} else ["label"], "state": ["state"],
            "display": ["state"], "quantity": ["scalar"], "schematic": [],
            "appearance": [], "data": ["data", "label"] if key == "items" else ["data"],
        }[role]
        rule = spec.get("default_rule", "fact")
        canonical = {}
        derived = []
        if "default_rule" not in spec:
            if role == "appearance":
                rule = "canonical"
            elif role == "schematic":
                rule = "schematic"
            elif "default" not in spec and not spec.get("required") and role != "function":
                rule = "optional"
            elif role == "display" and kind == "boolean":
                rule = "off"
                canonical = {"default": False, "suppress_unrequested": key in {"show_labels", "show_poles"}}
                if key in {"show_scale", "scale_labels"}:
                    derived = [["reading", "maximum"]] if "maximum" in parameters else (
                        [["reading"]] if "reading" in parameters else [["capacity"]] if "capacity" in parameters else [])
            elif key == "construction" and "none" in spec.get("choices", []):
                rule = "canonical"
                canonical = {"canonical_value": "none"}
            if {"x_range", "y_range"} <= set(parameters) and key in {"show_ticks", "x_label", "y_label"}:
                rule = "derived"
                derived = [[name for name in ("function", "x_range", "y_range") if name in parameters]]
                canonical = {"canonical_value": {"show_ticks": True, "x_label": "x", "y_label": "y"}[key]}
        if role == "display" and derived and key in {"show_scale", "scale_labels"}:
            rule = "derived"
            canonical = {"default": True, "canonical_value": True}
        result[key] = {**spec, **canonical, "role": role, "fact_types": fact_types,
            "default_rule": rule, "derived_from": spec.get("derived_from", derived),
            "condition_bearing": role != "appearance",
            "non_quantitative_allowed": spec.get("non_quantitative_allowed", role == "schematic")
                and role not in {"function", "data", "range"}}
        if key == "fill" and result[key].get("unit") == "height_fraction":
            result[key]["qualitative_state"] = "liquid_present"
    return result


def material_interface(parameters, *, rotation_allowed=False):
    return {"schema_version": INTERFACE_VERSION, "parameters": parameters,
        "layout": {**LAYOUT, "rotation_allowed": rotation_allowed}}


def compatible_fact(fact, spec, key, *, to_scale=False):
    if fact.display_policy == "hidden":
        return False
    if spec.get("qualitative_state") and fact.type == "state" and fact.predicate == spec["qualitative_state"]:
        return fact.value is True and not fact.unit and not to_scale
    if spec.get("unit") and fact.unit != spec["unit"] or fact.predicate and fact.predicate != key:
        return False
    if fact.type not in spec.get("fact_types", []):
        return False
    kind, value = spec["type"], fact.value
    if kind in {"number", "integer"}:
        return type(value) in {int, float} and (kind != "integer" or type(value) is int) and (
            "minimum" not in spec or value >= spec["minimum"]) and (
            "maximum" not in spec or value <= spec["maximum"])
    if kind == "boolean":
        return type(value) is bool
    if kind in {"array", "list"}:
        return isinstance(value, list)
    return isinstance(value, str)


def default_is_safe(spec, value, bound_keys):
    rule = spec.get("default_rule", "fact")
    if spec.get("role") == "display" and value is False:
        return True
    if rule == "canonical":
        return not spec.get("condition_bearing") or value == spec.get("canonical_value")
    derived = any(set(group) <= bound_keys for group in spec.get("derived_from", []))
    return derived and ("canonical_value" not in spec or value == spec["canonical_value"])
