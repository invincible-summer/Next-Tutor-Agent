"""M-Notes 笔记智能体：来源组装 + 生成管线 + 对话 ReAct-lite 循环。

独立于 M1 导师链路（不进 Supervisor），只服务 /api/v1/notes 的两个 SSE
端点。复用的共享设施：core/llm_async（LLM 客户端）、core/tool_base +
tool_protocol（工具协议）、prompts/registry（提示词版本化）。

降级契约（对齐全项目"智能层可关"的约定）：NOTES_AGENT_MODE=off 时
is_enabled() 为 False，路由返回带指引的错误 SSE；CRUD/导出不受影响。

来源组装的 token 纪律：每类来源有独立字符预算，超限截断并标注；教材
outline 检索失败时降级为仅标题行，绝不因图谱缺失阻塞生成。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from typing import Any, AsyncGenerator

from ..core import notes as notes_store
from ..core.llm_async import get_llm
from ..core.message_protocol import build_openai_tool_messages
from ..core.notes_templates import get_template
from ..core.tool_base import Tool
from ..core.tool_protocol import ErrorCode, err, ok
from ..prompts.registry import get as get_prompt

_SESSION_CHAR_BUDGET = 12_000
_TEXTBOOK_CHAR_BUDGET = 8_000
_WORKSPACE_CHAR_BUDGET = 4_000
_ERROR_NOTEBOOK_LIMIT = 30
_CHAT_MAX_STEPS = 4
_CHAT_HISTORY_MESSAGES = 12
_RETRIEVAL_CHAR_BUDGET = 10_000
_RETRIEVAL_QUERIES = 6
_MAX_FALLBACK_TEXTBOOK_FILES = 12
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif")
_MAX_CONTEXT_IMAGES = 3

# 三模式（2026-09 重构）：ask 只问答；plan 只产计划卡（不写入）；authorize
# 直接修改**当前笔记**（不能新建、不能改其他笔记）。旧四模式值映射：
# suggest/collab→plan、cowrite/auto→authorize，保证老客户端平滑过渡。
_MODES = notes_store.AGENT_MODES
_LEGACY_MODES = dict(notes_store._LEGACY_AGENT_MODES)


def normalize_mode(mode: str) -> str:
    return notes_store.normalize_agent_mode(mode)


def is_enabled() -> bool:
    """NOTES_AGENT_MODE 开关（默认开）。"""
    return os.getenv("NOTES_AGENT_MODE", "1") not in ("0", "false", "False", "off")


def _clip(text: str, budget: int) -> str:
    text = text or ""
    if len(text) <= budget:
        return text
    return text[:budget] + f"\n……（材料过长，已截断，原文约 {len(text)} 字符）"


def _default_student_id() -> str:
    from ..agents.student_model.store import DEFAULT_STUDENT_ID
    return DEFAULT_STUDENT_ID


# --- 来源组装 -----------------------------------------------------------------


def collect_session_block(student_id: str, session_id: str,
                          *, include_quizzes: bool = True) -> str | None:
    """一段会话 → 定界材料块。优先压缩摘要，否则取尾部消息。"""
    from ..core.session import load_session
    session = load_session(session_id)
    if session is None:
        return None
    if (getattr(session, "student_id", "") or _default_student_id()) != student_id:
        return None
    lines = [f"<material_excerpt type=\"session\" id=\"{session.session_id}\" "
             f"title=\"{session.title or session.session_id}\">"]
    compaction = getattr(session, "compaction", None)
    if compaction and getattr(compaction, "summary", ""):
        lines.append("【会话摘要】" + str(compaction.summary))
    else:
        messages = list(getattr(session, "messages", []) or [])
        for msg in messages[-30:]:
            role = "学生" if msg.get("role") == "user" else "教师"
            content = str(msg.get("content") or "").strip()
            if content:
                lines.append(f"{role}：{content}")
    if include_quizzes:
        quiz_lines = []
        for qh in (getattr(session, "quiz_history", None) or []):
            if not isinstance(qh, dict):
                continue
            for q in (qh.get("questions") or [])[:8]:
                result = q.get("result") or {}
                verdict = result.get("verdict") or ""
                if not verdict:
                    continue
                quiz_lines.append(
                    f"- [{verdict}] {str(q.get('knowledge_point') or '')}："
                    f"{str(q.get('stem') or '')[:120]}（作答："
                    f"{str(result.get('student_answer') or '')[:60]}）")
        if quiz_lines:
            lines.append("【本会话作答记录】\n" + "\n".join(quiz_lines[:20]))
    lines.append("</material_excerpt>")
    return _clip("\n".join(lines), _SESSION_CHAR_BUDGET)


def collect_textbook_block(student_id: str, textbook_id: str) -> str | None:
    from ..core.textbook import find_textbook_scoped
    got = find_textbook_scoped(student_id, textbook_id)
    if got is None:
        return None
    tb, owner_sid = got
    lines = [f"<material_excerpt type=\"textbook\" id=\"{tb.get('id')}\" "
             f"title=\"{tb.get('title') or tb.get('group_name') or textbook_id}\" "
             f"subject=\"{tb.get('subject') or ''}\" level=\"{tb.get('level') or ''}\">"]
    try:
        from ..agents.knowledge.textbook_builder import textbook_outline
        outline = textbook_outline(owner_sid, str(tb.get("id")))
    except Exception:
        outline = None
    if outline:
        for ch in outline:
            concepts = "、".join((ch.get("concepts") or [])[:12])
            lines.append(f"- {ch.get('chapter', '')}"
                         + (f"：{concepts}" if concepts else ""))
    else:
        lines.append(f"-（章节大纲暂不可用；教材共 "
                     f"{tb.get('chapter_count') or '?'} 章、"
                     f"{tb.get('concept_count') or '?'} 个概念）")
    lines.append("</material_excerpt>")
    return _clip("\n".join(lines), _TEXTBOOK_CHAR_BUDGET)


def collect_workspace_block(student_id: str, workspace_id: str) -> str | None:
    from ..core.workspace import _owner_of, load_workspace
    ws = load_workspace(workspace_id)
    if ws is None or _owner_of(ws) != student_id:
        return None
    lines = [f"<workspace_memory id=\"{ws.workspace_id}\" name=\"{ws.name}\">"]
    if ws.public_memory:
        lines.append(_clip(str(ws.public_memory), _WORKSPACE_CHAR_BUDGET))
    titles: list[str] = []
    try:
        from ..core.session import list_sessions
        owned = {m.get("session_id"): m.get("title")
                 for m in list_sessions()
                 if (m.get("student_id") or _default_student_id()) == student_id}
        titles = [str(owned.get(sid) or sid) for sid in ws.session_ids
                  if sid in owned]
    except Exception:
        pass
    if titles:
        lines.append("【工作区会话】" + "；".join(titles[:20]))
    tb_titles: list[str] = []
    try:
        from ..core.textbook import textbook_for_file
        for fid in ws.selected_file_ids:
            tb = textbook_for_file(student_id, fid)
            if tb is not None:
                name = str(tb.get("title") or tb.get("group_name") or "")
                if name and name not in tb_titles:
                    tb_titles.append(name)
    except Exception:
        pass
    if tb_titles:
        lines.append("【工作区教材】" + "；".join(tb_titles))
    lines.append("</workspace_memory>")
    return "\n".join(lines)


def collect_error_notebook_block(student_id: str) -> str:
    from ..core.error_notebook import collect_error_notebook
    items = collect_error_notebook(student_id, limit=_ERROR_NOTEBOOK_LIMIT)
    if not items:
        return ""
    lines = ["<material_excerpt type=\"error_notebook\">"]
    for it in items:
        lines.append(
            f"- [{it.get('verdict') or '错'}] {it.get('knowledge_point') or ''}："
            f"{str(it.get('stem') or '')[:150]}"
            f"（学生作答：{str(it.get('student_answer') or '')[:60]}；"
            f"正确答案：{str(it.get('correct_answer') or '')[:80]}）")
    lines.append("</material_excerpt>")
    return _clip("\n".join(lines), _SESSION_CHAR_BUDGET)


def assemble_sources(student_id: str, *,
                     session_ids: list[str] | None = None,
                     textbook_ids: list[str] | None = None,
                     workspace_id: str = "",
                     use_error_notebook: bool = False,
                     include_quizzes: bool = True) -> dict[str, Any]:
    blocks: list[str] = []
    summary: dict[str, Any] = {"sessions": 0, "textbooks": 0,
                               "workspace": False, "error_items": 0}
    for sid in (session_ids or [])[:8]:
        block = collect_session_block(student_id, sid,
                                      include_quizzes=include_quizzes)
        if block:
            blocks.append(block)
            summary["sessions"] += 1
    for tid in (textbook_ids or [])[:5]:
        block = collect_textbook_block(student_id, tid)
        if block:
            blocks.append(block)
            summary["textbooks"] += 1
    if workspace_id:
        block = collect_workspace_block(student_id, workspace_id)
        if block:
            blocks.append(block)
            summary["workspace"] = True
    if use_error_notebook:
        block = collect_error_notebook_block(student_id)
        if block:
            blocks.append(block)
            summary["error_items"] = len(
                block.split("<material_excerpt") ) - 1 or _ERROR_NOTEBOOK_LIMIT
    return {"blocks": blocks, "summary": summary}


# --- 检索语料（三形态来源 → RAG） ------------------------------------------------


def _resolve_source_mode(sources: dict[str, Any]) -> str:
    """来源三形态；缺省时按旧字段推断（老客户端兼容）。"""
    mode = str((sources or {}).get("source_mode") or "").strip().lower()
    if mode in ("sessions", "workspace", "textbooks"):
        return mode
    if (sources or {}).get("workspace_id"):
        return "workspace"
    if (sources or {}).get("textbook_ids"):
        return "textbooks"
    return "sessions"


def _build_retrieval_corpus(
    student_id: str, *,
    source_mode: str = "sessions",
    session_ids: list[str] | None = None,
    textbook_ids: list[str] | None = None,
    workspace_id: str = "",
    include_uploads: bool = False,
    fallback_textbooks: bool = False,
) -> dict[str, Any] | None:
    """三形态来源 → 检索语料 {overlay, scoped, sessions, hints, file_ids}。

    overlay 是 BM25 全量去重 chunks 的合成店（按文本指纹去重，会话引用
    教材的会话内副本与库原件天然合一）；scoped 是向量轨道的
    [(scope, store)]，scope 命名对齐 core/vector_store（session:<id> /
    folder:<id> / file:<fid> / notes:<sid>）。无任何可用语料时返回 None。
    fallback_textbooks：语料为空时回退到学生可选教材（封顶
    _MAX_FALLBACK_TEXTBOOK_FILES 个文件，防大库拖慢请求），仅供笔记助手
    这类无明确来源的场景。
    """
    from ..core.knowledge_store import KnowledgeStore
    from ..core.library import load_library
    from ..core.session import load_session

    overlay = KnowledgeStore()
    scoped: list[tuple[str, Any]] = []
    sessions: list[Any] = []
    hints: list[str] = []
    file_ids: set[str] = set()
    seen_text: set[str] = set()
    libs: dict[str, Any] = {}

    def _lib(owner_sid: str):
        if owner_sid not in libs:
            libs[owner_sid] = load_library(owner_sid)
        return libs[owner_sid]

    def _absorb(store: Any, scope: str) -> None:
        if store is None:
            return
        scoped.append((scope, store))
        for f in getattr(store, "files", None) or []:
            fid = str(f.get("id", ""))
            if fid:
                file_ids.add(fid)
                name = str(f.get("filename", ""))
                if name:
                    hints.append(name)
            overlay.files.append(f)
        for c in getattr(store, "chunks", None) or []:
            text = str(getattr(c, "text", "") or "")
            if not text:
                continue
            fp = hashlib.sha1(text[:400].encode("utf-8")).digest()
            if fp in seen_text:
                continue
            seen_text.add(fp)
            overlay.chunks.append(c)

    def _add_library_file(owner_sid: str, fid: str) -> None:
        try:
            lib = _lib(owner_sid)
            meta = lib.find_file(fid)
            chunks = lib.chunks_for(fid)
            if meta is None or not chunks:
                return
            sub = KnowledgeStore()
            sub.files = [meta]
            sub.chunks = list(chunks)
            _absorb(sub, f"file:{fid}")
        except Exception:
            pass

    def _load_owned_session(sid: str):
        session = load_session(str(sid))
        if session is None:
            return None
        if (getattr(session, "student_id", "") or _default_student_id()) != student_id:
            return None
        return session

    def _add_session(session: Any) -> None:
        sessions.append(session)
        _absorb(getattr(session, "knowledge", None),
                f"session:{getattr(session, 'session_id', '')}")
        try:
            from ..core.workspace import material_sources, resolve_textbook_file
            for f in material_sources(session):
                if f.get("source_scope") != "library":
                    continue
                lib_fid = str(f.get("library_file_id") or "")
                got = resolve_textbook_file(student_id, lib_fid) if lib_fid else None
                if got and got[0] is not None:
                    _add_library_file(got[1], lib_fid)
        except Exception:
            pass

    if source_mode == "sessions":
        for sid in (session_ids or [])[:8]:
            session = _load_owned_session(sid)
            if session is not None:
                _add_session(session)
    elif source_mode == "workspace" and workspace_id:
        from ..core.workspace import _owner_of, load_workspace, readable_stores
        ws = load_workspace(workspace_id)
        if ws is not None and _owner_of(ws) == student_id:
            picked = [str(s) for s in (session_ids or [])][:8]
            if not picked:
                picked = [str(s) for s in (ws.session_ids or [])][:8]
            for scope, store in readable_stores(ws):
                _absorb(store, scope)
            for sid in picked:
                session = _load_owned_session(sid)
                if session is not None:
                    _add_session(session)
    elif source_mode == "textbooks":
        from ..core.textbook import find_textbook_scoped
        for tid in (textbook_ids or [])[:5]:
            got = find_textbook_scoped(student_id, str(tid))
            if got is None:
                continue
            tb, owner_sid = got
            title = str(tb.get("title") or tb.get("group_name") or "")
            if title:
                hints.append(title)
            fids = ((tb.get("file_ids") or [])
                    if tb.get("kind") == "group"
                    else ([tb["file_id"]] if tb.get("file_id") else []))
            for fid in fids:
                _add_library_file(owner_sid, str(fid))

    if include_uploads:
        try:
            _absorb(notes_store.load_uploads_store(student_id),
                    notes_store.uploads_vector_scope(student_id))
        except Exception:
            pass

    if fallback_textbooks and not overlay.chunks:
        from ..core.textbook import PUBLIC_STUDENT_ID, load_textbooks
        budget = _MAX_FALLBACK_TEXTBOOK_FILES
        for sid in (student_id, PUBLIC_STUDENT_ID):
            if budget <= 0:
                break
            try:
                lib = _lib(sid)
                for tb in load_textbooks(sid):
                    if budget <= 0:
                        break
                    if tb.get("status") in {"building", "ocr_waiting"}:
                        continue
                    fids = ((tb.get("file_ids") or [])
                            if tb.get("kind") == "group"
                            else ([tb["file_id"]] if tb.get("file_id") else []))
                    for fid in fids:
                        if budget <= 0:
                            break
                        if lib.find_file(str(fid)) is not None:
                            _add_library_file(sid, str(fid))
                            budget -= 1
            except Exception:
                continue

    if not overlay.chunks:
        return None
    return {"overlay": overlay, "scoped": scoped or None, "sessions": sessions,
            "hints": [h for h in dict.fromkeys(hints) if h][:16],
            "file_ids": sorted(file_ids)}


async def _generate_retrieval_queries(template: Any, instructions: str,
                                      hints: list[str]) -> list[str]:
    """一次小调用生成 3-6 个检索查询；失败降级为确定性查询。"""
    payload = {
        "template": getattr(template, "name", "") or "",
        "template_desc": str(getattr(template, "description", "") or "")[:200],
        "instructions": str(instructions or "").strip()[:500],
        "materials": [h for h in hints if h][:12],
    }
    try:
        text, _usage = await get_llm().complete(
            messages=[{"role": "system",
                       "content": get_prompt("notes_retrieval_queries").text},
                      {"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False)}],
            temperature=0.2, max_tokens=300, disable_thinking=True)
        m = re.search(r"\[.*?\]", str(text or ""), re.DOTALL)
        if m:
            arr = json.loads(m.group(0))
            queries = [str(q).strip() for q in arr
                       if isinstance(q, (str, int, float)) and str(q).strip()]
            if queries:
                return queries[:_RETRIEVAL_QUERIES]
    except Exception:
        pass
    fallback = [q for q in (payload["template"], payload["instructions"],
                            *payload["materials"]) if q]
    return fallback[:_RETRIEVAL_QUERIES] or ["知识点总结"]


async def _collect_retrieval_blocks(student_id: str, queries: list[str],
                                    corpus: dict[str, Any]
                                    ) -> tuple[list[str], list[dict[str, Any]]]:
    """跑 KnowledgeSearchTool（与对话同一条 RAG 路径：混合检索 + 证据门）。

    返回 (块文本列表, 命中结果列表)；单查询未命中时静默跳过（生成侧
    允许部分命中，不同于对话侧的强证据话术）。
    """
    from ..core.embedding import get_embedding_client
    from ..tools.knowledge_search import KnowledgeSearchTool

    tool = KnowledgeSearchTool(corpus["overlay"],
                               scoped_stores=corpus["scoped"],
                               embed_client=get_embedding_client(),
                               student_id=student_id)
    blocks: list[str] = []
    hits: list[dict[str, Any]] = []
    for q in queries:
        got = await tool.run(query=q, top_k=4)
        if getattr(got, "status", "") != "success":
            continue
        text = str(getattr(got, "text", "") or "")
        if text:
            blocks.append(text)
        results = (getattr(got, "data", None) or {}).get("results") or []
        hits.extend(r for r in results if isinstance(r, dict))
    return blocks, hits


def _session_image_dataurls(sessions: list[Any], limit: int = 3) -> list[str]:
    """会话里的图片附件原件 → data URL（≤limit，多会话按顺序取）。"""
    from ..core.multimodal_context import _data_url

    out: list[str] = []
    for session in sessions:
        store = getattr(session, "knowledge", None)
        if store is None:
            continue
        for meta in getattr(store, "files", None) or []:
            if len(out) >= limit:
                return out
            if not str(meta.get("orig_ext", "")).startswith("."):
                continue
            fname = str(meta.get("filename", "")).lower()
            if not any(fname.endswith(e) for e in _IMAGE_EXTS):
                continue
            try:
                url = _data_url(
                    (store.upload_dir
                     / f"{meta['id']}.orig{meta['orig_ext']}").read_bytes())
            except OSError:
                url = None
            if url:
                out.append(url)
    return out


# --- 生成管线 -------------------------------------------------------------------


def _derive_title(draft: str, template: Any) -> str:
    for line in draft.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:60]
    name = template.name if template else "笔记"
    return f"{name} {time.strftime('%m-%d')}"


async def generate_note(student_id: str, *, template_id: str,
                        sources: dict[str, Any] | None = None,
                        target: dict[str, Any] | None = None,
                        instructions: str = "") -> AsyncGenerator[dict, None]:
    """按模板 + 来源生成一篇笔记（SSE 事件流）。

    事件：step{stage: collecting/retrieving/drafting} / sources_summary /
    answer{is_delta} / note_created{note} / done{note_id, revision} /
    error{message}。来源三形态（source_mode）：sessions 教材/文件按对话
    引用自动推导；workspace 可限定子集或整区；textbooks 直接对教材。
    """
    sources = sources or {}
    target = target or {}
    vault = notes_store.load_vault(student_id)
    template = get_template(template_id)
    custom = None
    if template is None and template_id.startswith("ct_"):
        custom = next((t for t in vault.custom_templates
                       if t.get("id") == template_id), None)
        if custom is None:
            yield {"type": "error", "message": "模板不存在"}
            return
    skeleton = template.skeleton if template else str(custom.get("content", ""))

    source_mode = _resolve_source_mode(sources)
    yield {"type": "step", "stage": "collecting"}
    # 工作区模式下 session_ids 为"限定子集"，空 = 整个工作区
    effective_sessions = [str(s) for s in (sources.get("session_ids") or [])]
    if source_mode == "workspace" and not effective_sessions:
        from ..core.workspace import _owner_of, load_workspace
        ws = load_workspace(str(sources.get("workspace_id") or ""))
        if ws is not None and _owner_of(ws) == student_id:
            effective_sessions = [str(s) for s in (ws.session_ids or [])][:8]
    assembled = assemble_sources(
        student_id,
        session_ids=effective_sessions,
        textbook_ids=sources.get("textbook_ids")
        if source_mode == "textbooks" else None,
        workspace_id=str(sources.get("workspace_id") or "")
        if source_mode == "workspace" else "",
        use_error_notebook=bool(sources.get("use_error_notebook")),
        include_quizzes=(template_id != "mistake_correction"))
    if not assembled["blocks"]:
        yield {"type": "error",
               "message": "没有可用的来源材料：请选择会话、教材、工作区或错题本"}
        return

    # 真实 RAG：按来源形态聚合语料 → 生成检索查询 → 检索相关片段
    corpus: dict[str, Any] | None = None
    retrieval_blocks: list[str] = []
    hits: list[dict[str, Any]] = []
    try:
        corpus = _build_retrieval_corpus(
            student_id, source_mode=source_mode,
            session_ids=effective_sessions,
            textbook_ids=sources.get("textbook_ids"),
            workspace_id=str(sources.get("workspace_id") or ""))
    except Exception:
        corpus = None
    if corpus is not None:
        queries = await _generate_retrieval_queries(
            template, instructions, corpus["hints"])
        if queries:
            yield {"type": "step", "stage": "retrieving"}
            retrieval_blocks, hits = await _collect_retrieval_blocks(
                student_id, queries, corpus)
    assembled["summary"]["retrieved"] = len(hits)
    yield {"type": "sources_summary", **assembled["summary"]}

    material_blocks = list(assembled["blocks"])
    if retrieval_blocks:
        material_blocks.append(
            "[检索片段（对所选来源自动 RAG 检索的相关段落，可信度同来源材料）]\n"
            + _clip("\n\n".join(retrieval_blocks), _RETRIEVAL_CHAR_BUDGET))

    user_parts = [f"[模板骨架]\n{skeleton}"]
    user_parts.append("[来源材料]\n" + "\n\n".join(material_blocks))
    outline = vault.vault_outline()
    if outline:
        user_parts.append("[仓库概览（已有笔记，可 [[链接]]）]\n"
                          + json.dumps(outline, ensure_ascii=False))
    if instructions.strip():
        user_parts.append(f"[用户要求]\n{instructions.strip()[:1000]}")

    # 多模态：RAG 图表证据页快照 + 所选会话的图片附件（≤3 张），
    # MULTIMODAL 未配置时静默降级纯文本
    from ..core.multimodal_context import (evidence_snapshot_images,
                                           get_multimodal_llm,
                                           with_context_images)
    images: list[str] = []
    try:
        if hits:
            images = evidence_snapshot_images(hits, student_id)
        if corpus and corpus["sessions"]:
            images += _session_image_dataurls(
                corpus["sessions"], limit=max(0, _MAX_CONTEXT_IMAGES - len(images)))
        images = images[:_MAX_CONTEXT_IMAGES]
    except Exception:
        images = []

    yield {"type": "step", "stage": "drafting"}
    mm_llm = get_multimodal_llm() if images else None
    llm = mm_llm or get_llm()
    draft_messages: list[dict[str, Any]] = [
        {"role": "system",
         "content": get_prompt("notes_generator_system").text},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    if mm_llm is not None:
        draft_messages = with_context_images(draft_messages, images)
    draft = ""
    try:
        async for ev in llm.stream(
                messages=draft_messages,
                temperature=0.3):
            if ev.get("kind") == "answer":
                draft += ev.get("delta", "")
                yield {"type": "answer", "content": ev.get("delta", ""),
                       "is_delta": True}
            elif ev.get("kind") == "retry":
                yield {"type": "retry", "attempt": ev.get("attempt"),
                       "reason": ev.get("reason")}
    except Exception as exc:
        yield {"type": "error", "message": f"生成失败：{exc}"}
        return

    draft = draft.strip()
    if not draft:
        yield {"type": "error", "message": "模型未返回内容，请重试"}
        return
    # 剥掉偶发的开场白，再剥代码块包裹（顺序不能反：开场白常在围栏之前）
    for lead in ("好的，以下是笔记", "以下是笔记", "以下是生成的笔记"):
        if draft.startswith(lead):
            draft = draft.split("\n", 1)[-1].strip()
            break
    if draft.startswith("```"):
        draft = draft.strip("`").strip()
        draft = re.sub(r"^markdown\r?\n", "", draft).strip()
    if hits:
        draft = _with_knowledge_cards(draft, _knowledge_cards({"data": {"results": hits}}))
    title = str(target.get("title") or "").strip() or _derive_title(draft, template)

    review_enabled = bool(template and template.review_enabled)
    folder_id = str(target.get("folder_id") or "")
    if not folder_id and template and template.folder_hint:
        folder_id = vault.ensure_folder(template.folder_hint)["id"]
    meta = vault.create_note(
        title=title, content=draft, folder_id=folder_id,
        template_id=template_id,
        tags=list(template.suggested_tags) if template else [],
        source={
            "source_mode": source_mode,
            "workspace_id": str(sources.get("workspace_id") or "")
            if source_mode == "workspace" else "",
            "session_ids": effective_sessions,
            "textbook_ids": list(sources.get("textbook_ids") or [])
            if source_mode == "textbooks" else [],
            "material_file_ids": (corpus or {}).get("file_ids") or [],
            "use_error_notebook": bool(sources.get("use_error_notebook")),
        },
        review_enabled=review_enabled, status="draft", author="agent")
    if review_enabled:
        try:
            from app.agents.learning_orchestration import manager as m9
            card = m9.get_orchestration_service().upsert_review_card(
                student_id, concept_id=f"note:{meta['id']}", concept_name=title)
            if card:
                meta["review"] = {
                    "enabled": True,
                    "next_review_at": float(card.get("next_review") or 0.0),
                    "easiness": float(card.get("easiness") or 2.5),
                    "interval": int(card.get("interval") or 0),
                    "repetitions": int(card.get("repetitions") or 0),
                }
        except Exception:
            pass
    notes_store.save_vault(vault)
    yield {"type": "note_created", "note": vault.note_summary(meta),
           "content": draft}
    yield {"type": "done", "note_id": meta["id"], "revision": meta["revision"]}


# --- 对话工具 --------------------------------------------------------------------


class _VaultTool(Tool):
    """每请求实例化；工具内即时加载仓库，避免跨请求共享可变状态。"""

    def __init__(self, student_id: str) -> None:
        self.student_id = student_id


class NotesSearchTool(_VaultTool):
    name = "notes_search"
    description = ("在学生的笔记仓库中检索笔记。按标题、标签、正文匹配，返回"
                   "笔记列表（id/标题/文件夹/标签/更新时间）。修改或引用笔记前"
                   "先用它定位目标。")
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索词（标题/标签/正文）"},
        },
        "required": ["query"],
    }

    async def run(self, query: str = "") -> Any:
        vault = notes_store.load_vault(self.student_id)
        results = vault.search(query, limit=10)
        return ok(self.name, data={"results": results},
                  text=f"检索到 {len(results)} 篇相关笔记")


class NotesReadTool(_VaultTool):
    name = "notes_read"
    description = "读取一篇笔记的完整正文与元数据（需要 note_id，可从 notes_search 获得）。"
    parameters = {
        "type": "object",
        "properties": {
            "note_id": {"type": "string", "description": "笔记 id"},
        },
        "required": ["note_id"],
    }

    async def run(self, note_id: str = "") -> Any:
        vault = notes_store.load_vault(self.student_id)
        meta = vault.find_note(note_id)
        if meta is None:
            return err(self.name, ErrorCode.NOT_FOUND, "笔记不存在")
        content = vault.read_note(note_id)
        return ok(self.name,
                  data={"note": vault.note_summary(meta),
                        "content": content[:20_000]},
                  text=f"已读取《{meta.get('title')}》")


class NotesWriteTool(_VaultTool):
    """授权模式的写入工具：只允许修改绑定的那一篇笔记。

    安全约束（2026-09 每笔记专属智能体重构）：
      - note_id 必须等于绑定的 allowed_note_id，改其他笔记直接拒绝；
      - 携带轮首快照的 base_revision 做乐观并发，学生在智能体运行期间
        保存的编辑不会被静默覆盖——冲突返回可重试错误，模型重读后重试；
      - 写入成功后推进期望版本号，同一轮内的连续写入链式生效。
    """

    name = "notes_write"
    description = ("修改当前笔记的正文（仅授权模式可用，且只能修改当前打开"
                   "的这一篇笔记）。必须给出完整的新正文，不是增量片段。")
    parameters = {
        "type": "object",
        "properties": {
            "note_id": {"type": "string", "description": "目标笔记 id（必须是当前笔记）"},
            "content": {"type": "string", "description": "完整的新正文"},
            "summary": {"type": "string", "description": "本次修改说明"},
        },
        "required": ["note_id", "content", "summary"],
    }

    def __init__(self, student_id: str, allowed_note_id: str,
                 expected_revision: int | None = None) -> None:
        super().__init__(student_id)
        self.allowed_note_id = str(allowed_note_id or "")
        self.expected_revision = expected_revision

    async def run(self, note_id: str = "", content: str = "",
                  summary: str = "") -> Any:
        vault = notes_store.load_vault(self.student_id)
        meta = vault.find_note(note_id)
        if meta is None:
            return err(self.name, ErrorCode.NOT_FOUND, "笔记不存在")
        if note_id != self.allowed_note_id:
            bound = vault.find_note(self.allowed_note_id)
            return err(self.name, ErrorCode.NO_TOOL,
                       "授权范围仅限当前笔记"
                       f"《{(bound or {}).get('title') or self.allowed_note_id}》；"
                       "不能修改其他笔记，也不能新建笔记。"
                       "如需改动其他笔记，请先与学生讨论，由学生自行操作。")
        try:
            meta = vault.write_note(note_id, content, author="agent",
                                    base_revision=self.expected_revision,
                                    summary=summary or "助手修改")
        except notes_store.StaleRevisionError:
            self.expected_revision = int(meta.get("revision") or 1)  # 对齐最新版本
            return err(self.name, ErrorCode.TOOL_ERROR,
                       "笔记刚被学生编辑过，本次写入未生效。请先 notes_read 重读"
                       "最新内容，在其基础上合并你的修改后重试一次。")
        self.expected_revision = int(meta.get("revision") or 1)
        notes_store.save_vault(vault)
        return ok(self.name,
                  data={"updated_note": vault.note_summary(meta),
                        "content": content[:20_000]},
                  text=f"已修改《{meta.get('title')}》")


# --- 对话循环 ----------------------------------------------------------------------

_MODE_DIRECTIVES = {
    "ask": ("ask（问答模式）：只回答与当前笔记、仓库和学习相关的问题；可读取"
            "当前笔记、其他笔记（notes_search / notes_read）与教材资料"
            "（knowledge_search）；不修改任何笔记，也不主动提出修改建议。"),
    "plan": ("plan（计划模式）：只讨论并产出针对**当前笔记**的结构化修改计划；"
             "可读取其他笔记与对话历史作参考，但绝不修改任何笔记（不调用任何"
             "写入工具），也不建议新建笔记。计划确定后按系统要求的 JSON 计划卡"
             "格式输出，等待学生批复。"),
    "authorize": ("authorize（授权模式）：学生已授权你直接修改**当前笔记**"
                  "（notes_write，且只能改这一篇）；不能新建笔记，不能修改其他"
                  "笔记。每次写入后在回复里说明改了什么、为什么。"),
}

# 计划卡 JSON 契约：plan 模式的回复在计划敲定时必须以一个 ```json 围栏块
# 结尾，内容为 {"title": str, "steps": [{"title": str, "detail": str}, ...]}。
_PLAN_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_MAX_PLAN_STEPS = 12
_MAX_PLAN_TITLE = 120
_MAX_PLAN_STEP_TEXT = 400


def _parse_plan_card(text: str) -> dict[str, Any] | None:
    """从 plan 模式回复里解析计划卡；解析失败视为普通问答（返回 None）。

    只认**最后一个** json 围栏块（多轮讨论中可能出现过示例块）；steps 必须
    非空且每步有 title。宽容清洗：去首尾空白、截断超长字段、丢弃非 dict 步骤。
    """
    blocks = _PLAN_JSON_RE.findall(str(text or ""))
    if not blocks:
        return None
    try:
        obj = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    title = str(obj.get("title") or "").strip()[:_MAX_PLAN_TITLE]
    steps: list[dict[str, str]] = []
    for raw in obj.get("steps") or []:
        if not isinstance(raw, dict):
            continue
        step_title = str(raw.get("title") or "").strip()[:_MAX_PLAN_TITLE]
        detail = str(raw.get("detail") or "").strip()[:_MAX_PLAN_STEP_TEXT]
        if step_title:
            steps.append({"title": step_title, "detail": detail})
        if len(steps) >= _MAX_PLAN_STEPS:
            break
    if not steps:
        return None
    return {"title": title or "笔记修改计划", "steps": steps}


def _build_knowledge_search_tool(student_id: str,
                                 context: dict[str, Any]) -> Tool | None:
    """笔记助手的知识检索工具（与对话模块同一条 RAG 路径）。

    语料 = 当前笔记的来源材料（生成时记录的 source）+ 笔记页上传的附件；
    两者皆空时回退到学生可选教材（封顶 _MAX_FALLBACK_TEXTBOOK_FILES 个
    文件，防大库拖慢请求）。无任何语料时返回 None（工具不装配即可，
    不影响四模式边界——knowledge_search 只读）。
    """
    try:
        vault = notes_store.load_vault(student_id)
        note_id = str((context or {}).get("note_id") or "")
        meta = vault.find_note(note_id) if note_id else None
        src = (meta or {}).get("source") or {}
        corpus = _build_retrieval_corpus(
            student_id,
            source_mode=_resolve_source_mode(src),
            session_ids=src.get("session_ids"),
            textbook_ids=src.get("textbook_ids"),
            workspace_id=str(src.get("workspace_id") or ""),
            include_uploads=True,
            fallback_textbooks=True)
        if corpus is None:
            return None
        from ..core.embedding import get_embedding_client
        from ..tools.knowledge_search import KnowledgeSearchTool
        return KnowledgeSearchTool(
            corpus["overlay"], scoped_stores=corpus["scoped"],
            embed_client=get_embedding_client(), student_id=student_id)
    except Exception:
        return None


def _context_system_tail(student_id: str, context: dict[str, Any],
                         mode: str, *, executing_plan: str = "") -> str:
    vault = notes_store.load_vault(student_id)
    # 计划批复后的执行期：按授权模式执行，且严格圈定在已批复的计划内
    directive_mode = "authorize" if executing_plan else mode
    parts = [f"\n\n[当前模式] {_MODE_DIRECTIVES[directive_mode]}"]
    if executing_plan:
        parts.append("学生已批复以下计划，严格按计划执行，不越界、不加戏：\n"
                     f"<approved_plan>\n{executing_plan[:12_000]}\n</approved_plan>")
    outline = vault.vault_outline()
    if outline:
        parts.append("[仓库概览（已有笔记，可 [[链接]]）]\n"
                     + json.dumps(outline, ensure_ascii=False))
    note_id = str((context or {}).get("note_id") or "")
    meta = vault.find_note(note_id) if note_id else None
    if meta is not None:
        skeleton = ""
        template = get_template(str(meta.get("template_id") or ""))
        if template is not None:
            skeleton = f"\n[当前模板]\n{template.skeleton}"
        parts.append(
            f"[当前笔记] 《{meta.get('title')}》（id={meta['id']}，"
            f"标签：{'、'.join(meta.get('tags') or []) or '无'}）{skeleton}\n"
            f"<note_content>\n{vault.read_note(note_id)[:16_000]}\n</note_content>")
    return "\n\n".join(parts)


def _knowledge_cards(result_dict: dict[str, Any]) -> list[dict[str, str]]:
    """Convert RAG evidence into compact, deduplicable note-safe cards."""
    data = result_dict.get("data") or {}
    cards: list[dict[str, str]] = []
    for item in (data.get("results") or [])[:4]:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or item.get("filename") or "课程资料")
        chapter = str(item.get("chapter") or "")
        printed = str(item.get("printed_page") or "")
        page = str(item.get("page") or "")
        chunk = str(item.get("chunk_id") or item.get("index") or "")
        excerpt = re.sub(r"\s+", " ", str(item.get("evidence_excerpt") or "")).strip()
        summary = excerpt[:180].rstrip("，。；; ")
        if len(excerpt) > 180:
            summary += "……"
        if not summary:
            summary = "该来源包含与本次笔记修改相关的可靠证据。"
        raw_fp = "|".join([str(item.get("file_id") or ""), chunk,
                            str(item.get("context_hash") or item.get("raw_text_sha256") or "")])
        fingerprint = hashlib.sha256(raw_fp.encode("utf-8")).hexdigest()[:20]
        location = f"教材《{source}》"
        if chapter:
            location += f" · {chapter}"
        if printed:
            location += f" · 第 {printed} 页"
        elif page:
            location += f" · PDF 第 {page} 页"
        elif chunk:
            location += f" · chunk {chunk}"
        cards.append({"fingerprint": fingerprint, "source": source,
                      "location": location, "summary": summary,
                      "file_id": str(item.get("file_id") or ""),
                      "chunk_id": chunk})
    return cards


def _with_knowledge_cards(content: str, cards: list[dict[str, str]]) -> str:
    """Append only missing compact cards; never persist complete RAG excerpts."""
    out = str(content or "").rstrip()
    for card in cards:
        marker = f"knowledge-card:{card['fingerprint']}"
        if marker in out:
            continue
        block = (f"> [知识卡] 来源：{card['location']}\n"
                 f"> 摘要：{card['summary']}\n"
                 f"> <!-- {marker}; file_id={card['file_id']}; chunk_id={card['chunk_id']} -->")
        out = f"{out}\n\n{block}" if out else block
    return out + ("\n" if out else "")


def _attachment_meta(attachments: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """线程消息里记录的附件元数据（仅 id/filename，绝不内联图片数据）。"""
    return [{"id": str(a.get("id") or ""), "filename": str(a.get("filename") or "")}
            for a in (attachments or [])
            if isinstance(a, dict) and a.get("id")][:_MAX_CONTEXT_IMAGES]


async def run_notes_chat(student_id: str, *, message: str,
                         context: dict[str, Any] | None = None,
                         mode: str = "ask",
                         action: str = "",
                         attachments: list[dict[str, Any]] | None = None,
                         ) -> AsyncGenerator[dict, None]:
    """笔记助手对话（SSE 事件流，每笔记专属智能体）。

    事件：run_start / step / thinking{is_delta,summary:false}（live 思考直播）/
    answer{is_delta} / tool_start / tool_result /
    note_updated{note_id, title, revision, content, summary} /
    mode_changed{mode} / plan_card{plan} / retry / error / run_end / done。

    三模式（mode）：ask 仅问答；plan 只产计划卡（绝不写入）；authorize 直接
    修改**当前笔记**（不能新建、不能改其他笔记）。对话历史按笔记隔离存储
    （agent/<note_id>.json，首载时从旧线程按 context.note_id 懒迁移）。
    action：approve_plan = 批复 pending 计划（状态机保证仅一次）并自动切入
    authorize 执行；reject_plan = 驳回计划，停留在 plan 模式。
    context.note_id 为空 = 仓库级对话（无绑定笔记，任何模式都只读）。
    knowledge_search（教材/资料 RAG）三模式统一装配（只读）；attachments
    为笔记页上传的图片附件 id 列表，MULTIMODAL 配置时注入视觉通道。
    """
    context = context or {}
    note_id = str(context.get("note_id") or "").strip()
    mode = normalize_mode(mode)
    action = str(action or "").strip()

    vault = notes_store.load_vault(student_id)
    bound_meta = vault.find_note(note_id) if note_id else None
    if note_id and bound_meta is None:
        yield {"type": "error", "message": "笔记不存在或已删除，无法对话"}
        return

    state = notes_store.load_agent_history(student_id, note_id)
    stored_mode = str(state.get("mode") or "ask")

    # --- 计划批复状态机（结构性消灭重复批复） -------------------------------
    executing_plan = ""
    approved_execution = False
    if action == "reject_plan":
        plan = state.get("pending_plan")
        if not isinstance(plan, dict) or plan.get("status") != "pending":
            yield {"type": "error", "message": "没有待批复的计划"}
            return
        notes_store.update_pending_plan_status(student_id, note_id, "rejected",
                                               decided_at=time.time())
        yield {"type": "plan_card", "plan": {**plan, "status": "rejected"}}
        notes_store.append_agent_message(
            student_id, note_id, "user", "（驳回计划）",
            {"note_id": note_id, "mode": "plan", "action": "reject_plan"})
        notes_store.append_agent_message(
            student_id, note_id, "assistant",
            "计划已驳回。我们可以继续讨论，调整出新的方案后再批复。",
            {"note_id": note_id, "mode": "plan"})
        yield {"type": "run_end", "status": "completed", "can_stop": False}
        yield {"type": "done", "answer": "计划已驳回。", "mode": "plan",
               "note_id": note_id}
        return
    if action == "approve_plan":
        plan = state.get("pending_plan")
        if not isinstance(plan, dict):
            yield {"type": "error",
                   "message": "没有待批复的计划：请先在计划模式下让助手给出计划"}
            return
        if plan.get("status") != "pending":
            yield {"type": "error",
                   "message": "该计划已批复过，不能重复批复；如需继续修改请直接授权或重新出计划"}
            return
        if not note_id:
            yield {"type": "error",
                   "message": "仓库级对话没有绑定笔记，无法执行计划；请先打开一篇笔记"}
            return
        notes_store.update_pending_plan_status(student_id, note_id, "approved",
                                               decided_at=time.time())
        notes_store.set_agent_mode(student_id, note_id, "authorize")
        mode = "authorize"
        yield {"type": "mode_changed", "mode": "authorize", "note_id": note_id}
        executing_plan = str(plan.get("plan_text") or plan.get("title") or "")
        approved_execution = True
        extra = message.strip()
        user_content = ("<user_input>\n（学生已批复上述计划，开始执行"
                        + (f"；补充要求：{extra}" if extra else "")
                        + "）\n</user_input>")
        record_user = ("（批复计划，开始执行）" + extra).strip()
    elif action:
        yield {"type": "error", "message": f"未知操作：{action}"}
        return
    else:
        # 普通消息：请求模式与该笔记持久模式不一致时以请求为准并落盘
        # （兼容未走 PATCH 的客户端）。
        if mode != stored_mode:
            notes_store.set_agent_mode(student_id, note_id, mode)
        user_content = f"<user_input>\n{message}\n</user_input>"
        record_user = message

    # --- 工具装配（三模式边界） ----------------------------------------------
    read_tools: list[Tool] = [NotesSearchTool(student_id),
                              NotesReadTool(student_id)]
    ks_tool = _build_knowledge_search_tool(student_id, context)
    if ks_tool is not None:
        read_tools.append(ks_tool)
    if note_id and (mode == "authorize" or executing_plan):
        # 授权写入：绑定当前笔记 + 轮首版本快照做乐观并发
        write_tool = NotesWriteTool(
            student_id, note_id,
            expected_revision=int((bound_meta or {}).get("revision") or 1))
        tools = read_tools + [write_tool]
    else:
        tools = list(read_tools)
    tool_map = {t.name: t for t in tools}

    history = [{"role": m.get("role", "user"),
                "content": str(m.get("content") or "")}
               for m in (state.get("messages") or [])[-_CHAT_HISTORY_MESSAGES:]]

    messages: list[dict[str, Any]] = [
        {"role": "system",
         "content": get_prompt("notes_assistant_system").text
         + _context_system_tail(student_id, context, mode,
                                executing_plan=executing_plan)},
        *history,
        {"role": "user", "content": user_content},
    ]

    # 图片附件（笔记页上传）→ MULTIMODAL 视觉通道；未配置时静默降级
    # （OCR 文本已由前端包在 <ocr_material> 里随消息进入上下文）
    from ..core.multimodal_context import (_data_url, get_multimodal_llm,
                                           with_context_images)
    images: list[str] = []
    if attachments:
        try:
            store = notes_store.load_uploads_store(student_id)
            for a in [x for x in attachments if isinstance(x, dict)][:_MAX_CONTEXT_IMAGES]:
                fid = str(a.get("id") or "")
                meta = next((f for f in store.files
                             if str(f.get("id", "")) == fid), None)
                if meta is None or not str(meta.get("orig_ext", "")).startswith("."):
                    continue
                fname = str(meta.get("filename", "")).lower()
                if not any(fname.endswith(e) for e in _IMAGE_EXTS):
                    continue
                try:
                    url = _data_url(
                        (store.upload_dir
                         / f"{meta['id']}.orig{meta['orig_ext']}").read_bytes())
                except OSError:
                    url = None
                if url:
                    images.append(url)
        except Exception:
            images = []
    mm_llm = get_multimodal_llm() if images else None
    if mm_llm is None:
        images = []

    run_id = f"run_{int(time.time() * 1000)}"
    notes_store.set_agent_working(student_id, note_id, stage="analyzing",
                                  can_stop=True, run_id=run_id)
    yield {"type": "run_start", "run_id": run_id, "stage": "analyzing", "status": "running", "can_stop": True}
    yield {"type": "step", "stage": "thinking", "status": "running", "run_id": run_id}
    llm = mm_llm or get_llm()
    # live 思考直播（与主对话链路同一门控；显示流不落盘、不进 TTS）
    from ..core.config import settings as _settings
    from .reasoning_live import LiveThinkingGate
    live_gate = LiveThinkingGate(_settings.reasoning_live_max_chars)
    final_answer = ""
    live_answer = ""
    evidence_cards: list[dict[str, str]] = []
    tool_records: list[dict[str, str]] = []
    try:
        for _step in range(_CHAT_MAX_STEPS):
            answer_buf = ""
            tool_calls_raw: list[dict[str, Any]] = []
            step_messages = (with_context_images(messages, images)
                             if images else messages)
            async for ev in llm.stream(
                    messages=step_messages, tools=[t.to_schema() for t in tools],
                    temperature=0.3):
                if ev.get("kind") == "answer":
                    delta = ev.get("delta", "")
                    answer_buf += delta
                    live_answer += delta
                    yield {"type": "answer", "content": delta, "is_delta": True}
                elif ev.get("kind") == "thinking":
                    live_delta = live_gate.take(ev.get("delta", ""))
                    if live_delta:
                        yield {"type": "thinking", "content": live_delta,
                               "is_delta": True, "summary": False}
                elif ev.get("kind") == "retry":
                    yield {"type": "retry", "attempt": ev.get("attempt"),
                           "reason": ev.get("reason")}
                elif ev.get("kind") == "tool_calls":
                    tool_calls_raw = ev.get("calls") or []
            final_answer = answer_buf or final_answer

            if not tool_calls_raw:
                break
            for tc in tool_calls_raw[:3]:
                tool_name = str(tc.get("name") or "")
                tool_args = dict(tc.get("args") or {})
                if tool_name == "notes_write" and evidence_cards:
                    tool_args["content"] = _with_knowledge_cards(str(tool_args.get("content") or ""), evidence_cards)
                notes_store.set_agent_working(student_id, note_id, stage="tool",
                                              tool=tool_name, can_stop=True, run_id=run_id)
                public_args = {k: v for k, v in tool_args.items() if k != "content"}
                yield {"type": "tool_start", "name": tool_name, "args": public_args, "stage": "tool", "status": "running", "can_stop": True, "run_id": run_id}
                tool = tool_map.get(tool_name)
                if tool is None:
                    result = err(tool_name, ErrorCode.NO_TOOL,
                                 f"工具 '{tool_name}' 当前模式不可用")
                else:
                    try:
                        result = await tool.run(**tool_args)
                    except TypeError as exc:
                        result = err(tool_name, ErrorCode.BAD_ARGS, f"参数错误: {exc}")
                    except Exception as exc:
                        result = err(tool_name, ErrorCode.TOOL_ERROR, str(exc))
                result_dict = result.to_dict()
                tool_records.append({"tool": tool_name,
                                     "status": str(result_dict.get("status") or ""),
                                     "summary": str(result_dict.get("text") or "")[:240]})
                if tool_name == "knowledge_search" and result_dict.get("status") == "success":
                    known = {c["fingerprint"] for c in evidence_cards}
                    for card in _knowledge_cards(result_dict):
                        if card["fingerprint"] not in known:
                            evidence_cards.append(card)
                            known.add(card["fingerprint"])
                public_result = result_dict
                if tool_name == "knowledge_search":
                    public_result = {"tool": result_dict.get("tool"),
                                     "status": result_dict.get("status"),
                                     "text": str(result_dict.get("text") or "")[:1200],
                                     "data": {"count": len(evidence_cards),
                                              "knowledge_cards": evidence_cards}}
                yield {"type": "tool_result", "result": public_result, "stage": "tool", "status": result_dict.get("status", "success") }
                data = result_dict.get("data") or {}
                if "updated_note" in data:
                    updated = data["updated_note"]
                    content = data.get("content")
                    if content is None:
                        content = notes_store.load_vault(
                            student_id).read_note(str(updated.get("id") or ""))
                    yield {"type": "note_updated",
                           "note_id": updated.get("id"),
                           "title": updated.get("title"),
                           "revision": updated.get("revision"),
                           "content": content,
                           "summary": tool_args.get("summary", "")}
                messages.extend(build_openai_tool_messages(
                    answer_buf, call_id=str(tc.get("id", "")),
                    tool_name=tool_name, args=tool_args,
                    result_text=result_dict.get("text", "")))
            messages.append({"role": "user",
                             "content": "（工具已执行，请基于结果继续；"
                                        "如无更多操作请给出面向学生的总结回复）"})
    except (GeneratorExit, asyncio.CancelledError):
        notes_store.set_agent_working(student_id, note_id, stage="idle")
        notes_store.append_agent_message(student_id, note_id, "user", record_user,
                                         {"note_id": note_id, "stopped": True,
                                          "attachments": _attachment_meta(attachments)})
        notes_store.append_agent_message(student_id, note_id, "assistant",
                                         live_answer or "（本次运行已停止）",
                                         {"note_id": note_id, "mode": mode,
                                          "stopped": True})
        raise
    except Exception as exc:
        # 异常也完整收尾：写留痕 + run_end/done，前端 finally 统一刷新
        notes_store.set_agent_working(student_id, note_id, stage="idle")
        yield {"type": "error", "message": f"笔记助手出错：{exc}", "status": "error", "run_id": run_id}
        notes_store.append_agent_message(student_id, note_id, "user", record_user,
                                         {"note_id": note_id,
                                          "attachments": _attachment_meta(attachments)})
        notes_store.append_agent_message(student_id, note_id, "assistant",
                                         f"（本次运行出错：{exc}）",
                                         {"note_id": note_id, "mode": mode,
                                          "error": True})
        if approved_execution:
            notes_store.update_pending_plan_status(student_id, note_id, "executed",
                                                   executed_at=time.time())
        yield {"type": "run_end", "status": "error", "can_stop": False, "run_id": run_id}
        yield {"type": "done", "answer": f"笔记助手出错：{exc}", "mode": mode,
               "note_id": note_id}
        return

    if not final_answer:
        final_answer = "（本次没有生成文字回复，请重试）"

    # plan 模式：回复尾部的 JSON 计划卡 → 待批复状态 + plan_card 事件。
    # 解析不出（澄清问答/继续讨论）不设 pending，批复条不出现。
    if mode == "plan" and not executing_plan and final_answer:
        card = _parse_plan_card(final_answer)
        if card is not None:
            pending = {"status": "pending", **card, "plan_text": final_answer,
                       "created_at": time.time()}
            notes_store.set_pending_plan(student_id, note_id, pending)
            yield {"type": "plan_card", "plan": pending}

    notes_store.append_agent_message(student_id, note_id, "user", record_user,
                                     {"note_id": note_id,
                                      "attachments": _attachment_meta(attachments)})
    notes_store.append_agent_message(student_id, note_id, "assistant", final_answer,
                                     {"note_id": note_id, "mode": mode,
                                      "tools": tool_records})
    if approved_execution:
        notes_store.update_pending_plan_status(student_id, note_id, "executed",
                                               executed_at=time.time())
    notes_store.set_agent_working(student_id, note_id, stage="idle")
    yield {"type": "run_end", "status": "completed", "can_stop": False, "run_id": run_id}
    yield {"type": "done", "answer": final_answer, "mode": mode, "note_id": note_id}
