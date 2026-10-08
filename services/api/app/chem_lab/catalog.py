"""Experiment-pack catalog: discovery, validation, hashing, projections.

The content directory is source code (project-authored JSON), never runtime
data. Loading runs the full gate: schema validation, manifest hash check,
closed-set DSL validation and cross-reference checks. A catalog that fails
any check raises ``ChemLabError("pack_invalid")`` — the API never serves a
half-valid pack.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import pack_schema as ps
from .dsl import validate_rule_set
from .engine import model as m
from .errors import ChemLabError

CONTENT_DIR = Path(__file__).resolve().parent / "content"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChemLabError("pack_invalid", f"无法读取 {path.name}: {exc}") from exc


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _index_by_id(items: list[Any], what: str) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for item in items:
        if item.id in indexed:
            raise ChemLabError("pack_invalid", f"{what} id 重复: {item.id}")
        indexed[item.id] = item
    return indexed


class Catalog:
    """Validated, hash-pinned view over the content directory."""

    def __init__(self, content_dir: Path | None = None):
        self.content_dir = Path(content_dir) if content_dir else CONTENT_DIR
        self._load()

    # -- loading -----------------------------------------------------------

    def _load(self) -> None:
        root = self.content_dir
        if not root.is_dir():
            raise ChemLabError("pack_invalid", f"内容目录不存在: {root}")
        manifest_raw = _load_json(root / "manifest.json")
        try:
            self.manifest = ps.ContentManifest.model_validate(manifest_raw)
        except ValueError as exc:
            raise ChemLabError("pack_invalid", f"manifest.json 校验失败: {exc}") from exc

        for relpath, expect_hash in sorted(self.manifest.files.items()):
            path = root / relpath
            if not path.is_file():
                raise ChemLabError("pack_invalid", f"manifest 引用了缺失文件 {relpath}")
            actual = _sha256_file(path)
            if actual != expect_hash:
                raise ChemLabError(
                    "pack_invalid",
                    f"{relpath} 内容与 manifest 不符（修改内容后须重跑 scripts/chem_lab/validate_pack.py 刷新 manifest）")

        self.species = _index_by_id(
            [ps.SpeciesDef.model_validate(_load_json(p)) for p in sorted((root / "species").glob("*.json"))],
            "species")
        self.rules = _index_by_id(
            [ps.RuleDef.model_validate(_load_json(p)) for p in sorted((root / "rules").glob("*.json"))],
            "rule")
        self.equipment = _index_by_id(
            [ps.EquipmentDef.model_validate(_load_json(p)) for p in sorted((root / "equipment").glob("*.json"))],
            "equipment")
        self.concepts = _index_by_id(
            [ps.ConceptDef.model_validate(_load_json(p)) for p in sorted((root / "concepts").glob("*.json"))],
            "concept")
        experiments: dict[str, ps.ExperimentPack] = {}
        for path in sorted((root / "experiments").glob("*.json")):
            pack = ps.ExperimentPack.model_validate(_load_json(path))
            key = f"{pack.id}@{pack.pack_version}"
            if key in experiments:
                raise ChemLabError("pack_invalid", f"实验包重复: {key}")
            experiments[key] = pack
        if not experiments:
            raise ChemLabError("pack_invalid", "没有可用实验包")
        self.experiments = experiments
        self.vectors_dir = root / "vectors"

        for pack in self.experiments.values():
            self._validate_experiment(pack)
        self._pack_hashes: dict[str, str] = {
            key: self._compute_pack_hash(pack) for key, pack in self.experiments.items()
        }

    def _validate_experiment(self, pack: ps.ExperimentPack) -> None:
        prefix = f"{pack.id}@{pack.pack_version}"

        def fail(message: str) -> None:
            raise ChemLabError("pack_invalid", f"{prefix}: {message}")

        for equipment_id in pack.equipment:
            if equipment_id not in self.equipment:
                fail(f"引用了未知器材 {equipment_id}")
        for species_id in pack.species:
            if species_id not in self.species:
                fail(f"引用了未知物质 {species_id}")
        for rule_id in pack.rule_ids:
            if rule_id not in self.rules:
                fail(f"引用了未知规则 {rule_id}")
        for concept_id in pack.concept_ids:
            if concept_id not in self.concepts:
                fail(f"引用了未知概念 {concept_id}")
        declared_species = set(pack.species)

        slot_ids = [slot.id for slot in pack.starting_state.slots]
        if len(slot_ids) != len(set(slot_ids)):
            fail("starting_state.slots id 重复")
        slot_set = set(slot_ids)
        vessel_ids: set[str] = set()
        for vessel in pack.starting_state.vessels:
            vessel_ids.add(vessel.id)
            if vessel.kind not in self.equipment:
                fail(f"容器 {vessel.id} 使用了未知器材种类 {vessel.kind}")
            if vessel.slot not in slot_set:
                fail(f"容器 {vessel.id} 引用了未知槽位 {vessel.slot}")
            for table, name in ((vessel.contents, "contents"), (vessel.solids, "solids"),
                                (vessel.gases, "gases")):
                for species_id in table:
                    if species_id not in declared_species:
                        fail(f"容器 {vessel.id} 的 {name} 引用了未声明物质 {species_id}")
        for equipment in pack.starting_state.equipment:
            if equipment.kind not in self.equipment:
                fail(f"器材 {equipment.id} 使用了未知种类 {equipment.kind}")
            if equipment.slot not in slot_set:
                fail(f"器材 {equipment.id} 引用了未知槽位 {equipment.slot}")
        for reagent in pack.reagents:
            if reagent.vessel_id not in vessel_ids:
                fail(f"试剂 {reagent.id} 指向未知容器 {reagent.vessel_id}")
            for species_id in reagent.species:
                if species_id not in declared_species:
                    fail(f"试剂 {reagent.id} 引用了未声明物质 {species_id}")

        step_ids = {step.id for step in pack.procedure}
        observation_keys: set[str] = set()
        for step in pack.procedure:
            observation_keys.update(step.expected_observations)
            for concept_id in step.concept_ids:
                if concept_id not in self.concepts:
                    fail(f"步骤 {step.id} 引用了未知概念 {concept_id}")
        for goal in pack.goals:
            observation_keys.update(goal.requires_observations)
        for prediction in pack.predictions:
            if prediction.step_id not in step_ids:
                fail(f"预测 {prediction.id} 指向未知步骤 {prediction.step_id}")
            correct = [option for option in prediction.options if option.correct]
            if len(correct) != 1:
                fail(f"预测 {prediction.id} 必须恰好一个正确选项")

        rule_errors = validate_rule_set(
            [self.rules[rule_id] for rule_id in pack.rule_ids],
            species_ids=declared_species,
            equipment_ids={e.id for e in pack.starting_state.equipment},
            step_ids=step_ids,
            observation_keys=observation_keys)
        if rule_errors:
            fail("; ".join(rule_errors))

    # -- hashing ------------------------------------------------------------

    def _compute_pack_hash(self, pack: ps.ExperimentPack) -> str:
        """sha256 over the canonical engine-relevant projection. The client
        pins this hash; the TS engine never recomputes it."""
        payload = {
            "experiment": pack.model_dump(mode="json"),
            "rules": [self.rules[rule_id].model_dump(mode="json") for rule_id in pack.rule_ids],
            "species_defs": {sid: self.species[sid].model_dump(mode="json") for sid in pack.species},
            "equipment_defs": {eid: self.equipment[eid].model_dump(mode="json") for eid in pack.equipment},
        }
        blob = m.canonical(payload)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # -- queries --------------------------------------------------------------

    def experiment_keys(self) -> list[str]:
        return sorted(self.experiments)

    def get(self, experiment_id: str, pack_version: str | None = None) -> ps.ExperimentPack:
        matches = [pack for key, pack in self.experiments.items()
                   if pack.id == experiment_id
                   and (pack_version is None or pack.pack_version == pack_version)]
        if not matches:
            raise ChemLabError("experiment_missing", f"未找到实验 {experiment_id}", status=404)
        return sorted(matches, key=lambda p: p.pack_version)[-1]

    def pack_hash(self, pack: ps.ExperimentPack) -> str:
        return self._pack_hashes[f"{pack.id}@{pack.pack_version}"]

    def list_summaries(self) -> list[dict[str, Any]]:
        summaries = []
        for key in sorted(self.experiments):
            pack = self.experiments[key]
            summaries.append({
                "id": pack.id,
                "pack_version": pack.pack_version,
                "pack_hash": self.pack_hash(pack),
                "title": pack.title.model_dump(),
                "summary": pack.summary.model_dump(),
                "audience": list(pack.audience),
                "modes": list(pack.modes),
                "model_fidelity": pack.model_fidelity,
                "model_scope": pack.model_scope.model_dump(),
            })
        return summaries

    def build_pack(self, experiment_id: str, pack_version: str | None = None) -> dict[str, Any]:
        """Assemble the runtime pack the engines consume (private fields under
        underscore keys, stripped by canonical/state_hash automatically)."""
        pack = self.get(experiment_id, pack_version)
        runtime = pack.model_dump(mode="json")
        runtime["pack_hash"] = self.pack_hash(pack)
        runtime["_rules"] = [self.rules[rule_id].model_dump(mode="json") for rule_id in pack.rule_ids]
        runtime["_species_defs"] = {sid: self.species[sid].model_dump(mode="json") for sid in pack.species}
        runtime["_equipment_defs"] = {eid: self.equipment[eid].model_dump(mode="json")
                                      for eid in pack.equipment}
        runtime["_concepts"] = {cid: self.concepts[cid].model_dump(mode="json")
                                for cid in pack.concept_ids}
        return runtime

    def public_projection(self, experiment_id: str, pack_version: str | None = None) -> dict[str, Any]:
        """Student-facing experiment detail: display info, procedure titles,
        reagents, safety — never rule internals, never prediction answers."""
        pack = self.get(experiment_id, pack_version)
        data = pack.model_dump(mode="json")
        for prediction in data.get("predictions", []):
            for option in prediction.get("options", []):
                option.pop("correct", None)
        data["pack_hash"] = self.pack_hash(pack)
        data["equipment_defs"] = {eid: self.equipment[eid].model_dump(mode="json")
                                  for eid in pack.equipment}
        data["species_defs"] = {sid: _public_species(self.species[sid]) for sid in pack.species}
        data["concept_defs"] = {cid: self.concepts[cid].model_dump(mode="json")
                                for cid in pack.concept_ids}
        return data

    def vector_files(self, experiment_id: str) -> list[Path]:
        directory = self.vectors_dir / experiment_id
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.json"))


def _public_species(species: ps.SpeciesDef) -> dict[str, Any]:
    data = species.model_dump(mode="json")
    return data


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    """Process-wide catalog; tests reload via ``get_catalog.cache_clear()``."""
    return Catalog()
