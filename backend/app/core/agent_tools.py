"""会话工具组装（从 api/v1/chat.py::_build_tools 提取，plan.md §12.4）。

普通聊天经 ``api.v1.chat._build_tools`` 薄 wrapper 复用本模块（保留既有
patch 点）；课堂问答以服务端构造的 ``restrict_to_file_ids`` 做**可信
来源 override**：检索 overlay 限于当前仍授权的 lesson 来源文件，撤销
来源只经课堂材料区的冻结摘录解释（§12.4.4）。集合由服务端从冻结 spec
导出，LLM 工具 schema 不新增任何身份参数。
"""
from __future__ import annotations

from typing import Iterable

from .session import TutorSession


def build_session_tools(
    session: TutorSession, *, user_message: str = "",
    attachments: list[dict] | None = None,
    restrict_to_file_ids: Iterable[str] | None = None,
) -> list:
    """Wire tools for a session (knowledge_search needs the session's store).

    If the session belongs to a workspace, merge the workspace's SELECTED
    library sources (its exclusive folder + picked folders/files) into the
    session's store so knowledge_search searches both session-level and
    workspace-readable materials — unselected library files stay invisible.
    When the embedding track is configured, knowledge_search additionally
    gets the scoped (session/folder/file) stores for hybrid retrieval;
    otherwise the BM25 overlay alone remains the whole retrieval path.

    统一 Quiz Grounding（plan.md §4.2）：本轮 message/attachments 经
    decide_material_grounding 得出 strict 教材语义，注入共享的
    KnowledgeSearchQuizGroundingProvider —— generate_quiz / fit_quiz 与
    普通问答使用同一个已授权检索空间；教材 scope 由服务端闭包决定，
    LLM 工具 schema 不新增任何身份参数。

    ``restrict_to_file_ids``：仅限服务端内部（课堂）调用——非 None 时
    工作区 overlay 只保留该集合内的文件；课堂撤销来源不进入检索。
    """
    from app.core.llm_async import get_llm
    from app.tools.knowledge_search import KnowledgeSearchTool
    from app.tools.knowledge_read import KnowledgeReadTool
    from app.tools.quiz import GenerateQuizTool
    from app.tools.fit_quiz import FitQuizTool
    from app.tools.recall_history import RecallHistoryTool
    llm = get_llm()
    # Hybrid track: scoped stores + embed client (None when unconfigured).
    from app.core.embedding import get_embedding_client
    embed = get_embedding_client()
    scoped = None
    if embed is not None:
        from app.core.workspace import scoped_knowledge_stores
        scoped = scoped_knowledge_stores(session) or None
    # Merge workspace-readable knowledge if applicable.
    # Prior quiz stems feed generate_quiz's anti-repeat list so successive
    # turns don't re-issue the same canonical question.
    avoid_stems = [
        str(q.get("stem", "")).strip()[:40]
        for qh in (session.quiz_history or [])[-3:]
        for q in ((qh.get("questions") or []) if isinstance(qh, dict) else [])
        if isinstance(q, dict) and str(q.get("stem", "")).strip()
    ]

    def _quiz_tools(search_tool):
        from app.agents.preresearch import decide_material_grounding
        from app.core.quiz_grounding import (
            KnowledgeSearchQuizGroundingProvider)
        decision = decide_material_grounding(session, user_message, attachments)
        quiz_grounding = KnowledgeSearchQuizGroundingProvider(
            search_tool,
            required=decision.required,
            reason=decision.trace_reason,
            file_ids=decision.file_ids,
        )
        from app.core.quiz_illustration_policy import (
            IllustrationPolicyProvider, explicit_illustration_request)
        illustrations = IllustrationPolicyProvider(
            session.student_id, explicit_illustration_request(user_message))
        return (GenerateQuizTool(llm, avoid_stems=avoid_stems,
                                 grounding_provider=quiz_grounding,
                                 illustration_policy_provider=illustrations),
                FitQuizTool(llm, grounding_provider=quiz_grounding,
                            illustration_policy_provider=illustrations))

    restrict = set(restrict_to_file_ids) if restrict_to_file_ids is not None \
        else None

    if session.workspace_id:
        from app.core.workspace import readable_files, readable_stores, workspace_for_session
        ws = workspace_for_session(session)
        if ws:
            ws_stores = readable_stores(ws)
            all_chunks = [c for _s, st in ws_stores for c in st.chunks]
            # chunk 级过滤：folder/workspace 作用域的 store 混装多文件，
            # 受限模式下只保留集合内 file_id 的 chunk（file_id 为权威归属）。
            ws_chunks = ([c for c in all_chunks if c.file_id in restrict]
                         if restrict is not None else all_chunks)
            if ws_chunks:
                # Create a SEPARATE KnowledgeStore copy so workspace chunks are
                # NOT persisted to the session file. Temporary overlay for
                # knowledge_search tool only.
                from app.core.knowledge_store import KnowledgeStore
                overlay = KnowledgeStore()
                merged_files = readable_files(ws)
                if restrict is not None:
                    merged_files = [f for f in merged_files
                                    if f.get("id") in restrict]
                overlay.chunks = list(session.knowledge.chunks) + ws_chunks
                overlay.files = list(session.knowledge.files) + merged_files
                search_tool = KnowledgeSearchTool(
                    overlay, scoped_stores=scoped, embed_client=embed,
                    student_id=getattr(session, "student_id", "") or "")
                gen_quiz, fit_quiz = _quiz_tools(search_tool)
                return [
                    search_tool,
                    KnowledgeReadTool(overlay, scoped_stores=scoped),
                    gen_quiz,
                    fit_quiz,
                    RecallHistoryTool(session.session_id,
                                      getattr(session, "student_id", "") or "",
                                      getattr(session, "workspace_id", "") or ""),
                ]
    search_tool = KnowledgeSearchTool(
        session.knowledge, scoped_stores=scoped, embed_client=embed,
        student_id=getattr(session, "student_id", "") or "")
    gen_quiz, fit_quiz = _quiz_tools(search_tool)
    return [
        search_tool,
        KnowledgeReadTool(session.knowledge, scoped_stores=scoped),
        gen_quiz,
        fit_quiz,
        RecallHistoryTool(session.session_id,
                          getattr(session, "student_id", "") or "",
                          getattr(session, "workspace_id", "") or ""),
    ]
