"""Knowledge Intelligence core data structures (M5: knowledge ontology layer).

Where the V2 Supervisor (M1) answers "what task to run", the Student Model
(M2) answers "what does this student know", the Teaching Engine (M3) answers
"how to teach now", and Assessment (M4) answers "did they learn it", this
module answers the layer beneath all of them:

    "what should the system KNOW about the subject matter itself?"

It is the knowledge *ontology*: concepts, the relationships between them, and
their teaching content. The Student Model's SkillGraph is a per-student
projection of (PREREQUISITE edges + mastery) drawn from here; this module owns
the richer multi-edge graph plus descriptions/aliases/content that the
teaching + assessment layers consume to ground explanations in real subject
structure rather than free-form LLM knowledge.

Plain dataclasses with to_dict/from_dict round-trips, mirroring
student_model/state.py and teaching_engine/state.py. No behaviour lives here
beyond serialization + the EdgeType enum; the graph logic is in graph.py.
Keeping data and behaviour separate matches how every other module in this
package is split.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: 学段词表：KnowledgeNode.level 只允许这些值（"" = 跨学段）。
LEVELS = ("小学", "初中", "高中", "本科")

#: 节点来源（KnowledgeNode.origin）：
#:   seed     = 考纲种子包（代码提交，运行期确定性）
#:   material = 用户上传教材提取（M5.7 自定义图谱）
#:   llm      = LLM 直接生成（M5.7 自定义图谱，无教材时）
NODE_ORIGINS = ("seed", "material", "llm")

#: 节点种类（KnowledgeNode.kind）：
#:   concept = 可学习概念（参与检索 / 掌握度 / 学习顺序推理）
#:   chapter = 章节容器（PART_OF 分组骨架，仅供前端分层导航，不检索、不追踪掌握度）
#:   section = 章内二级结构（课/篇目/小节；PART_OF 挂在 chapter 下，参与名称检索
#:             与 RAG 定位标注，但不追踪掌握度——语文篇目、理科小节都归此类）
NODE_KINDS = ("concept", "chapter", "section")


class EdgeType(str, Enum):
    """Kinds of relationships between two knowledge concepts.

    Only PREREQUISITE forms the learning-order DAG that SkillGraph consumes
    (to learn B you first need A). The other edge types carry pedagogically
    useful context (what a concept is applied to, what it is commonly
    confused with) that the Context Builder surfaces as soft directives, but
    they never constrain learning order.
    """
    PREREQUISITE = "prerequisite"     # A must precede B (learning-order DAG)
    RELATED = "related"               # topical kin (tangent <-> slope)
    PART_OF = "part_of"               # belongs to chapter / unit
    APPLICATION = "application"       # real-world use (derivative -> velocity)
    MISCONCEPTION = "misconception"   # common wrong association

    @classmethod
    def from_value(cls, v: Any) -> "EdgeType":
        if isinstance(v, EdgeType):
            return v
        try:
            return cls(str(v)) if v else cls.RELATED
        except ValueError:
            return cls.RELATED


@dataclass
class KnowledgeNode:
    """One concept in the knowledge ontology.

    `id` shares the dotted-path scheme with student_model/skill_graph_seed
    (<subject>.<area>.<skill>), and overlapping ids carry the SAME name and
    PREREQUISITE edges, so this graph is a strict superset of the skill seed.
    `aliases` widen the fuzzy-match surface for the ConceptRetriever; they do
    not change identity. `common_errors` are short, pre-seeded wrong ideas the
    Context Builder surfaces as [知识智能·易错点] hints.
    `origin` records where the node came from (NODE_ORIGINS); seed nodes are
    curated in code, material/llm nodes enter via the M5.7 custom-graph build
    and are persisted by store.py. `kind` separates learnable concepts from
    chapter containers (NODE_KINDS): chapters exist only so PART_OF edges can
    group concepts for hierarchical navigation; they are excluded from
    retrieval and mastery tracking.
    """
    id: str
    name: str
    subject: str = ""
    level: str = ""          # 小学/初中/高中/本科 ("" = cross-level); see LEVELS
    difficulty: int = 3      # 1 (foundational) .. 5 (advanced)
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    common_errors: list[str] = field(default_factory=list)
    origin: str = "seed"     # seed | material | llm (NODE_ORIGINS)
    kind: str = "concept"    # concept | chapter | section (NODE_KINDS)
    metadata: dict[str, Any] = field(default_factory=dict)  # source identity; not display decoration

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "subject": self.subject,
                "level": self.level, "difficulty": self.difficulty,
                "description": self.description, "aliases": list(self.aliases),
                "common_errors": list(self.common_errors),
                "origin": self.origin, "kind": self.kind,
                "metadata": dict(self.metadata)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KnowledgeNode":
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            subject=str(d.get("subject", "")),
            level=str(d.get("level", "")),
            difficulty=int(d.get("difficulty", 3)),
            description=str(d.get("description", "")),
            aliases=[str(a) for a in (d.get("aliases", []) or [])],
            common_errors=[str(e) for e in (d.get("common_errors", []) or [])],
            origin=str(d.get("origin", "seed")) or "seed",
            kind=str(d.get("kind", "concept")) or "concept",
            metadata=dict(d.get("metadata") or {}),
        )

    def search_text(self) -> str:
        """Text the ConceptRetriever indexes: name + aliases + description.

        Subject/level are appended so queries like "初中物理 浮力" can match;
        they sit last so they dilute per-concept term frequencies the least.
        Centralized so retriever.py has one place to expand the match surface.
        """
        parts = [self.name] + [a for a in self.aliases if a]
        if self.description:
            parts.append(self.description)
        for tag in (self.subject, self.level):
            if tag:
                parts.append(tag)
        return " ".join(p for p in parts)


@dataclass
class KnowledgeEdge:
    """A typed, weighted, provenance-tracked relationship between two nodes.

    `source -> target` semantics depend on `type`: for PREREQUISITE it reads
    "to learn `target` you first need `source`" (so source is the ancestor).
    `weight` is the confidence for LEARNED edges (0..1); seed edges default to
    1.0. `provenance` records how the edge entered the graph so the Reasoner
    never overwrites a curated seed fact.
    """
    source: str
    target: str
    type: EdgeType = EdgeType.RELATED
    weight: float = 1.0
    provenance: str = "seed"      # seed | reasoner | material

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target,
                "type": self.type.value, "weight": round(self.weight, 4),
                "provenance": self.provenance}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KnowledgeEdge":
        return cls(
            source=str(d.get("source", "")),
            target=str(d.get("target", "")),
            type=EdgeType.from_value(d.get("type")),
            weight=float(d.get("weight", 1.0)),
            provenance=str(d.get("provenance", "seed")) or "seed",
        )


@dataclass
class KnowledgeContent:
    """Teaching material attached to one concept.

    A node is the abstract concept; this is the concrete teaching content the
    ContentResolver resolves it into (definition / formula / worked example /
    exercise hint). Phase 1 sources this from seed.py for core concepts and
    falls back to uploaded-material BM25 for the rest; LLM generation is a
    documented future source (Phase 3+), never Phase 1.
    """
    concept_id: str = ""
    definition: str = ""
    formula: str = ""
    example: str = ""
    exercise_hint: str = ""
    source: str = "seed"        # seed | material:<file> | llm

    def to_dict(self) -> dict[str, Any]:
        return {"concept_id": self.concept_id, "definition": self.definition,
                "formula": self.formula, "example": self.example,
                "exercise_hint": self.exercise_hint, "source": self.source}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KnowledgeContent":
        d = d or {}
        return cls(
            concept_id=str(d.get("concept_id", "")),
            definition=str(d.get("definition", "")),
            formula=str(d.get("formula", "")),
            example=str(d.get("example", "")),
            exercise_hint=str(d.get("exercise_hint", "")),
            source=str(d.get("source", "seed")) or "seed",
        )

    @property
    def has_any(self) -> bool:
        return any((self.definition, self.formula, self.example, self.exercise_hint))


@dataclass
class KnowledgeContext:
    """The resolved knowledge view one teaching turn consumes.

    Assembled by context_builder.build_knowledge_context from the graph +
    the student's mastery view + content + uploaded materials, then rendered
    into a [知识智能·...] soft-directive block. It is a flat plain-data
    projection so the Context Builder stays import-clean of student_model
    types (mastery arrives as plain {id: p_known} dicts).
    """
    concept: str = ""
    node_id: str = ""
    prerequisite_chain: list[str] = field(default_factory=list)   # ancestor names, root-first
    missing_prereqs: list[str] = field(default_factory=list)      # not yet mastered, weakest-first
    common_errors: list[str] = field(default_factory=list)        # node + MISCONCEPTION edges
    recommended_examples: list[str] = field(default_factory=list) # APPLICATION edges + content
    related_concepts: list[str] = field(default_factory=list)
    available_materials: list[str] = field(default_factory=list)  # uploaded chunks mentioning it
    definition: str = ""
    confidence: float = 0.0       # retriever confidence for the concept match

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept, "node_id": self.node_id,
            "prerequisite_chain": list(self.prerequisite_chain),
            "missing_prereqs": list(self.missing_prereqs),
            "common_errors": list(self.common_errors),
            "recommended_examples": list(self.recommended_examples),
            "related_concepts": list(self.related_concepts),
           "available_materials": list(self.available_materials),
           "definition": self.definition,
           "confidence": round(self.confidence, 3),
       }
 
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KnowledgeContext":
        d = d or {}
        return cls(
            concept=str(d.get("concept", "") or ""),
            node_id=str(d.get("node_id", "") or ""),
            prerequisite_chain=list(d.get("prerequisite_chain", []) or []),
            missing_prereqs=list(d.get("missing_prereqs", []) or []),
            common_errors=list(d.get("common_errors", []) or []),
            recommended_examples=list(d.get("recommended_examples", []) or []),
            related_concepts=list(d.get("related_concepts", []) or []),
            available_materials=list(d.get("available_materials", []) or []),
            definition=str(d.get("definition", "") or ""),
            confidence=float(d.get("confidence", 0.0)),
        )
