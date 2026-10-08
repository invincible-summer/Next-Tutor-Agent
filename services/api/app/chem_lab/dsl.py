"""Closed-set DSL static validation for chem-lab rules.

Rules may only combine the ops the engines implement; anything executable,
free-form or referencing unknown species/equipment is rejected at load
time. The checks here are intentionally loud — content authors get the
rule id and the exact offending field.
"""
from __future__ import annotations

from .pack_schema import RuleDef

#: Vessel placeholders a rule may reference.
VESSEL_SCOPES = {"$target"}

_CONDITION_REQUIRED: dict[str, set[str]] = {
    "species_at_least": {"species", "amount_umol"},
    "volume_between": set(),
    "temperature_between": set(),
    "ph_between": set(),
    "mix_at_least": set(),
    "vessel_connected": {"target"},
    "equipment_state": {"target", "expected"},
}

_EFFECT_REQUIRED: dict[str, set[str]] = {
    "consume_min_ratio": {"pairs"},
    "produce": {"species"},
    "transfer_to_solid": {"species"},
    "transfer_to_gas": {"species"},
    "emit_temperature": {"micro_j_per_umol"},
    "mark_observation": {"key"},
    "mark_step": {"key"},
}

#: Effects that change chemistry (vs. purely bookkeeping effects).
_CHEMICAL_EFFECTS = {
    "consume_min_ratio", "produce", "transfer_to_solid",
    "transfer_to_gas", "emit_temperature",
}

EQUIPMENT_STATES = {"idle", "measuring", "done", "connected", "loaded", "clean"}


def _check_pairs(rule: RuleDef, effect, species_ids: set[str], errors: list[str]) -> None:
    if not effect.pairs:
        errors.append(f"{rule.id}: consume_min_ratio 缺少 pairs")
        return
    for pair in effect.pairs:
        if len(pair) != 2 or not isinstance(pair[0], str) or not isinstance(pair[1], int):
            errors.append(f"{rule.id}: pairs 行必须是 [species:str, ratio:int]")
            continue
        if pair[0] not in species_ids:
            errors.append(f"{rule.id}: pairs 引用了未知物质 {pair[0]}")
        if pair[1] <= 0:
            errors.append(f"{rule.id}: pairs 比例必须为正整数")


def validate_rule(rule: RuleDef, *, species_ids: set[str], equipment_ids: set[str],
                  step_ids: set[str], observation_keys: set[str]) -> list[str]:
    """Static-check one rule against the pack's declared universe."""
    errors: list[str] = []
    if rule.priority < 0:
        errors.append(f"{rule.id}: priority 不能为负")
    if not rule.triggers:
        errors.append(f"{rule.id}: 至少需要一个触发器")

    for condition in rule.when:
        if condition.vessel not in VESSEL_SCOPES:
            errors.append(f"{rule.id}: 条件引用了未知容器作用域 {condition.vessel}")
        required = _CONDITION_REQUIRED[condition.op]
        if "species" in required and condition.species not in species_ids:
            errors.append(f"{rule.id}: 条件引用了未知物质 {condition.species or '(空)'}")
        if "target" in required and condition.op == "equipment_state":
            if condition.target not in equipment_ids:
                errors.append(f"{rule.id}: 条件引用了未知器材 {condition.target or '(空)'}")
            if condition.expected not in EQUIPMENT_STATES:
                errors.append(f"{rule.id}: 未知器材状态 {condition.expected or '(空)'}")
        if condition.op in {"volume_between", "temperature_between", "ph_between"}:
            if condition.min_value >= condition.max_value:
                errors.append(f"{rule.id}: {condition.op} 的区间必须 min < max")
        if condition.op == "mix_at_least" and not (0 < condition.min_value <= 1000):
            errors.append(f"{rule.id}: mix_at_least 需要 0 < min_value <= 1000")

    has_chemical = False
    for effect in rule.then:
        if effect.vessel not in VESSEL_SCOPES:
            errors.append(f"{rule.id}: 效果引用了未知容器作用域 {effect.vessel}")
        required = _EFFECT_REQUIRED[effect.op]
        if effect.op in _CHEMICAL_EFFECTS:
            has_chemical = True
        if effect.op == "consume_min_ratio":
            _check_pairs(rule, effect, species_ids, errors)
        elif "species" in required and effect.species not in species_ids:
            errors.append(f"{rule.id}: 效果引用了未知物质 {effect.species or '(空)'}")
        if effect.op == "transfer_to_gas" and effect.amount_umol < 0:
            errors.append(f"{rule.id}: transfer_to_gas 数量不能为负")
        if effect.op == "mark_step" and effect.key not in step_ids:
            errors.append(f"{rule.id}: mark_step 引用了未声明步骤 {effect.key or '(空)'}")
        if effect.op == "mark_observation" and observation_keys and effect.key not in observation_keys:
            errors.append(f"{rule.id}: mark_observation 引用了未声明观察 {effect.key or '(空)'}")

    if not has_chemical and not any(e.op in {"mark_observation", "mark_step"} for e in rule.then):
        errors.append(f"{rule.id}: 规则没有任何效果")
    return errors


def validate_rule_set(rules: list[RuleDef], *, species_ids: set[str], equipment_ids: set[str],
                      step_ids: set[str], observation_keys: set[str]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for rule in rules:
        if rule.id in seen:
            errors.append(f"规则 id 重复: {rule.id}")
        seen.add(rule.id)
        errors.extend(validate_rule(
            rule, species_ids=species_ids, equipment_ids=equipment_ids,
            step_ids=step_ids, observation_keys=observation_keys))
    return errors
