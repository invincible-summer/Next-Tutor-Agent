"""评价核心的只读端口协议（plan §6.2 依赖边界）。

评价包不得 import `api/v1/*`、M3/M9 manager/store；跨模块读取一律走
这些 Protocol，由 `core/learner_runtime.py` 组装具体适配器。M2 facade、
M1/M3/M5/M7/M9、memory 页面通过 `EvaluationReader` 读评价（§6.2）。
"""
from __future__ import annotations

from typing import Any, Protocol

from . import schema as S


class WorkspaceReader(Protocol):
    """工作区读取（core/workspace 适配）。"""

    def load(self, workspace_id: str) -> dict[str, Any] | None:
        """工作区 dict（含 student_id/selected_file_ids）；None=不存在。"""
        ...

    def stamp(self, student_id: str) -> tuple:
        """本用户工作区文件的失效戳（mtime 元组即可，只做缓存失效）。"""
        ...


class TextbookReader(Protocol):
    """教材注册记录读取（core/textbook 适配）。"""

    def resolve_textbook_file(
            self, student_id: str,
            file_id: str) -> tuple[dict[str, Any], str] | None:
        """(record, owner_namespace)；非注册教材文件返回 None。"""
        ...


class GraphReader(Protocol):
    """M5 自有图谱读取（knowledge.store 适配）。"""

    def load(self, owner_namespace: str,
             topic_key: str) -> dict[str, Any] | None: ...

    def stamp(self, owner_namespace: str) -> tuple: ...


class ContextReader(Protocol):
    """ContextPack 组装的只读数据源（§8）。"""

    def session_context(self, student_id: str,
                        source_session_ref: str) -> dict[str, Any]: ...

    def workspace_context(self, student_id: str,
                          workspace_id: str) -> dict[str, Any]: ...

    def learner_preferences(self, student_id: str) -> dict[str, Any]: ...


class EvaluationReader(Protocol):
    """统一评价读取口：M1/M2/M3/M5/M6/M7/M9/memory 的唯一入口（§6.2）。"""

    def workspace_summary(self, student_id: str,
                          workspace_id: str) -> S.WorkspaceEvaluationSummary | None:
        ...

    def concept_views(self, student_id: str, workspace_id: str,
                      *, scope_revision: str = "") -> list[S.ConceptEvaluationView]:
        ...

    def concept_view(self, student_id: str, workspace_id: str,
                     concept_key: str) -> S.ConceptEvaluationView | None: ...

    def prior_claims(self, student_id: str, workspace_id: str,
                     concept_key: str) -> dict[str, Any]:
        """当前有效主张 + 近期支持/反例（ContextPack prior_same_concept）。"""
        ...

    def evidence_timeline(self, student_id: str, workspace_id: str,
                          *, concept_key: str = "", source_kind: str = "",
                          source_session_ref: str = "",
                          offset: int = 0, limit: int = 20,
                          ) -> tuple[list[S.SourceTimelineItem], int]:
        ...
