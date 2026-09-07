"""File-level summaries for uploaded course materials (RAG enhancement).

After a successful upload, ONE background LLM call condenses the file into a
<=150-char Chinese summary + 3-5 topic tags, written back into the file's
metadata (files[i]["summary"] / ["topics"]) — which rides the existing
knowledge_files persistence (session JSON / workspace JSON), so no extra
storage is needed. merged_knowledge_files() then injects "文件名：摘要（标签）"
into the planner/preamble, so the LLM knows what each file covers BEFORE it
decides to knowledge_search.

Design constraints (project principles):
  - Fire-and-forget: scheduled with asyncio.create_task from the upload
    endpoints; never blocks or fails the upload response. Any failure logs a
    warning and leaves the file summary-less (injection degrades to the bare
    filename).
  - Race-safe write-back: the LLM call takes seconds, so the metadata is
    re-loaded from disk and merged right before saving (the same
    read-modify-write guard as workspace_memory.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from .llm_async import AsyncLLMClient, get_llm

log = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 8000   # truncate long files; a summary needs the gist
_SUMMARY_MAX_CHARS = 150

_SYSTEM = (
    "你是课程资料摘要助手。阅读学生上传的学习资料，输出一个 JSON 对象：\n"
    '{"summary": "不超过150字的中文内容摘要，说明这份资料讲了什么", '
    '"topics": ["3到5个主题标签，如 物理/浮力/力学"]}\n'
    "只输出 JSON 本身，不要任何解释或 markdown 代码块。"
)


def _parse_output(raw: str) -> tuple[str, list[str]]:
    """Parse the LLM's JSON output. Tolerant: falls back to using the raw
    text as the summary when it isn't valid JSON."""
    raw = (raw or "").strip()
    if not raw:
        return "", []
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            summary = str(d.get("summary") or "").strip()
            topics = [str(t).strip() for t in (d.get("topics") or []) if str(t).strip()]
            return summary[:_SUMMARY_MAX_CHARS], topics[:5]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return raw[:_SUMMARY_MAX_CHARS], []


async def _generate(filename: str, text: str,
                    llm: AsyncLLMClient) -> tuple[str, list[str]]:
    content, _usage = await llm.complete(
        [{"role": "system", "content": _SYSTEM},
         {"role": "user", "content":
          f"文件名：{filename}\n\n资料全文（可能已截断）：\n{text[:_MAX_INPUT_CHARS]}"}],
        temperature=0.2,
        max_tokens=400,
    )
    return _parse_output(content or "")


def _apply(files: list[dict[str, Any]], file_id: str,
           summary: str, topics: list[str]) -> bool:
    """Write summary/topics into the matching file metadata dict."""
    for f in files:
        if f.get("id") == file_id:
            f["summary"] = summary
            f["topics"] = topics
            return True
    return False


async def summarize_session_file(session_id: str, file_id: str, filename: str,
                                 text: str, llm: AsyncLLMClient | None = None) -> None:
    """Generate + persist a summary for a session-level file. Never raises."""
    try:
        from .session import load_session, save_session
        summary, topics = await _generate(filename, text, llm or get_llm())
        if not summary:
            return
        # Reload right before writing: anything saved during the LLM call
        # (chat turns, renames) must not be clobbered by a stale snapshot.
        session = load_session(session_id)
        if session is None:
            return
        if _apply(session.knowledge.files, file_id, summary, topics):
            save_session(session)
    except Exception as e:
        log.warning("file summary failed for session %s file %s: %s",
                    session_id, file_id, e)


async def summarize_workspace_file(ws_id: str, file_id: str, filename: str,
                                   text: str, llm: AsyncLLMClient | None = None) -> None:
    """Generate + persist a summary for a workspace shared file. Never raises."""
    try:
        from .workspace import load_workspace, save_workspace
        summary, topics = await _generate(filename, text, llm or get_llm())
        if not summary:
            return
        ws = load_workspace(ws_id)
        if ws is None:
            return
        if _apply(ws.knowledge.files, file_id, summary, topics):
            save_workspace(ws)
    except Exception as e:
        log.warning("file summary failed for workspace %s file %s: %s",
                    ws_id, file_id, e)


async def summarize_library_file(student_id: str, file_id: str, filename: str,
                                 text: str, llm: AsyncLLMClient | None = None) -> None:
    """Generate + persist a summary for a library file. Never raises."""
    try:
        from .library import load_library, save_library
        summary, topics = await _generate(filename, text, llm or get_llm())
        if not summary:
            return
        # Reload right before writing: uploads/moves during the LLM call must
        # not be clobbered by a stale snapshot (same guard as the other two).
        lib = load_library(student_id)
        if _apply(lib.files, file_id, summary, topics):
            save_library(lib)
    except Exception as e:
        log.warning("file summary failed for library %s file %s: %s",
                    student_id, file_id, e)


def schedule_session_file_summary(session_id: str, file_id: str,
                                  filename: str, text: str) -> None:
    """Fire-and-forget wrapper for the upload endpoint. Never raises."""
    try:
        asyncio.get_running_loop().create_task(
            summarize_session_file(session_id, file_id, filename, text))
    except Exception as e:
        log.warning("could not schedule session file summary: %s", e)


def schedule_workspace_file_summary(ws_id: str, file_id: str,
                                    filename: str, text: str) -> None:
    """Fire-and-forget wrapper for the workspace upload endpoint."""
    try:
        asyncio.get_running_loop().create_task(
            summarize_workspace_file(ws_id, file_id, filename, text))
    except Exception as e:
        log.warning("could not schedule workspace file summary: %s", e)


def schedule_library_file_summary(student_id: str, file_id: str,
                                  filename: str, text: str) -> None:
    """Fire-and-forget wrapper for the library upload endpoint."""
    try:
        asyncio.get_running_loop().create_task(
            summarize_library_file(student_id, file_id, filename, text))
    except Exception as e:
        log.warning("could not schedule library file summary: %s", e)
