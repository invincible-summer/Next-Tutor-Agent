"""AI extension points for the chem lab: protocols only, models off by default.

The bench is deterministic. These protocols are the only sanctioned places
where a future AI capability may plug in, and every default implementation
here is model-free:

- ``ScenarioProvider`` resolves an experiment id + version to the validated,
  hash-pinned runtime pack both engines consume.
- ``GuidanceProvider`` turns a sanitized snapshot plus observation facts into
  a guidance card.
- ``ScenarioDraftProvider`` would draft new experiment packs. It has no
  default implementation: drafting stays disabled until the review chain
  (schema validation, rule static checks, multi-path replay, TS/Python hash
  alignment, content review, human approval) exists.

Hard rules for any future AI implementation: never write LabState or execute
commands, never emit client-executable code or DSL, never read other users'
sessions or raw chain-of-thought, and never bypass validators, safety rules,
action preview, user confirmation or idempotency.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .catalog import get_catalog
from .engine.guidance import build_guidance

# Packs, snapshots, observations and guidance cards are validated JSON dicts;
# the engines and serializers own their exact fields.
PublicExperimentPack = dict[str, Any]
SafeLabSnapshot = dict[str, Any]  # session document projection: state + language
ObservationFact = dict[str, Any]
Guidance = dict[str, Any]
ScenarioRequest = dict[str, Any]
ScenarioDraft = dict[str, Any]


@runtime_checkable
class ScenarioProvider(Protocol):
    """Resolves published experiment packs (read-only)."""

    def get_pack(self, experiment_id: str, version: str) -> PublicExperimentPack: ...


@runtime_checkable
class GuidanceProvider(Protocol):
    """Explains the current state from sanitized evidence only."""

    def explain(self, snapshot: SafeLabSnapshot,
                evidence: list[ObservationFact]) -> Guidance: ...


@runtime_checkable
class ScenarioDraftProvider(Protocol):
    """Drafts a new experiment pack for the human review chain."""

    def draft(self, request: ScenarioRequest) -> ScenarioDraft: ...


class CatalogScenarioProvider:
    """Default ScenarioProvider over the validated, hash-pinned catalog."""

    def get_pack(self, experiment_id: str, version: str) -> PublicExperimentPack:
        return get_catalog().build_pack(experiment_id, version or None)


class DeterministicGuidanceProvider:
    """Default GuidanceProvider: rule-based guidance over the engine state.

    ``snapshot`` carries the session document projection (``state`` and
    ``language`` keys); ``evidence`` carries the latest public events.
    """

    def __init__(self, pack: PublicExperimentPack) -> None:
        self._pack = pack

    def explain(self, snapshot: SafeLabSnapshot,
                evidence: list[ObservationFact]) -> Guidance:
        return build_guidance(self._pack, snapshot["state"], last_events=evidence,
                              language=snapshot.get("language", "zh"))
