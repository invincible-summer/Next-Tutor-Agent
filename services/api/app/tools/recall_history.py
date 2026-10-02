"""recall_history tool: JIT recovery from the session's transcript JSONL.

Makes compaction RECOVERABLE. When the working context's summary is
insufficient (a formula from an earlier turn, a quiz answer, the student's
previous reply), the agent searches the full transcript backup instead of
guessing. This is the "read the JSONL" pattern from Claude Code / Manus.
"""
from __future__ import annotations

from typing import Any

from ..core.context import transcript_path
from ..core.tool_base import Tool
from ..core.tool_protocol import ErrorCode, err, ok


class RecallHistoryTool(Tool):
    name = "recall_history"
    description = (
        "在历史对话记录中检索关键词，找回被压缩摘要省略的细节"
        "（如某次讲解的公式、某道练习题的答案与解析、学生之前的回答或错题）。"
        "默认检索本会话；学生明确问「之前/上次/以前」讲过的内容时会连带检索"
        "该学生名下的其它会话。参数：query(关键词,必填) "
        "max_results(返回条数1-5,默认3)。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要检索的关键词或短语"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 5,
                            "description": "返回的匹配条数，默认3"},
        },
        "required": ["query"],
    }

    # 跨会话扫描预算：该生最近 8 个其它会话 × 每个会话尾部 600 行，
    # 足以覆盖「上周讲过什么」类问题，又不会把 MB 级 transcript 全读进来。
    _XSESSION_FILES = 8
    _XSESSION_TAIL_LINES = 600

    def __init__(self, session_id: str, student_id: str = "",
                 workspace_id: str = "") -> None:
        self._session_id = session_id
        self._student_id = student_id
        # P6-D 记忆收敛：跨会话召回只在工作区内发生（同工作区的其它会话）；
        # 独立对话只检索本会话 transcript。
        self._workspace_id = workspace_id

    async def run(self, **kwargs: Any):
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return err(self.name, ErrorCode.BAD_ARGS, "query 不能为空。")
        try:
            path = transcript_path(self._session_id)
        except ValueError:
            return err(self.name, ErrorCode.BAD_ARGS, "会话尚未持久化，无历史可检索。")
        if not path.exists():
            return err(self.name, ErrorCode.NOT_FOUND, "本会话暂无历史记录。")
        max_results = kwargs.get("max_results") or 3
        try:
            max_results = max(1, min(5, int(max_results)))
        except (TypeError, ValueError):
            max_results = 3

        import json as _json

        def _read_entries(path, *, tail: int = 0) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            with path.open("r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            if tail > 0:
                lines = lines[-tail:]
            for ln in lines:
                try:
                    entry = _json.loads(ln)
                except Exception:
                    continue
                if str(entry.get("content", "")):
                    out.append(entry)
            return out

        entries: list[dict[str, Any]] = _read_entries(path)
        # P4：跨会话检索。P6-D 收敛：默认仅工作区内跨会话（同工作区的其它
        # 会话，最近 8 个、各取尾部 600 行）；CROSS_SESSION_MEMORY=all 恢复
        # 旧行为（该生全部会话），off 完全关闭。
        from ..core.config import settings as _cs
        mode = _cs.cross_session_memory
        if self._student_id and mode != "off" and (
                mode == "all" or self._workspace_id):
            try:
                from ..core.session import list_sessions
                from ..agents.student_model.store import DEFAULT_STUDENT_ID
                others = []
                for s in list_sessions():
                    if (s.get("student_id") or DEFAULT_STUDENT_ID) != self._student_id:
                        continue
                    if s.get("session_id") == self._session_id:
                        continue
                    if mode != "all" and (s.get("workspace_id") or "") != self._workspace_id:
                        continue  # 仅同工作区会话
                    others.append(s)
                for meta in others[:self._XSESSION_FILES]:
                    try:
                        op = transcript_path(str(meta["session_id"]))
                    except ValueError:
                        continue
                    if not op.exists():
                        continue
                    try:
                        oentries = _read_entries(op, tail=self._XSESSION_TAIL_LINES)
                    except Exception:
                        continue
                    import time as _time
                    day = _time.strftime("%m-%d", _time.localtime(
                        float(meta.get("updated_at") or 0)))
                    label = f"会话《{str(meta.get('title') or '未命名')[:20]}》({day})"
                    for e in oentries:
                        e["_xsession"] = label
                        entries.append(e)
            except Exception:
                pass
        # 阶段D：BM25 相关性排序（复用 core/retriever 的 CJK 感知分词），
        # 取代旧的子串匹配线性扫描——按相关度取 top-k 而非按时间顺序截断。
        # 零命中时回退原子串扫描（保留旧行为兜底），仍无命中则如实返回
        # NOT_FOUND（「未命中须如实告知」语义不变）。
        from ..core.retriever import BM25Index, Chunk, tokenize
        chunks = [
            Chunk(chunk_id=f"t{i}", source="", text=str(e.get("content", "")),
                  index=i, tokens=tokenize(str(e.get("content", ""))))
            for i, e in enumerate(entries)
        ]
        hits = BM25Index(chunks).search(query, top_k=max_results) if chunks else []
        ranked = [entries[c.index] for c, _score in hits]
        if not ranked:
            ql = query.lower()
            ranked = [e for e in entries
                      if ql in str(e.get("content", "")).lower()
                      or query in str(e.get("content", ""))][:max_results]
        if not ranked:
            return err(self.name, ErrorCode.NOT_FOUND,
                       f"历史记录中未找到与「{query}」相关的内容。")
        matches: list[dict[str, Any]] = []
        for e in ranked:
            content = str(e.get("content", ""))
            snippet = content if len(content) < 600 else (content[:280] + f"…[+{len(content)-280}字]")
            matches.append({
                "turn": e.get("turn"),
                "role": e.get("role"),
                "snippet": snippet,
                "xsession": str(e.get("_xsession") or ""),
            })
        # 注入防护：找回的历史内容是不可信数据，逐条包裹 <history_excerpt>
        # 定界标记（系统提示已声明：定界内是数据不是指令）。
        lines: list[str] = []
        for m in matches:
            if m.get("xsession"):
                turn_label = m["xsession"]
            else:
                turn_label = f"第{m['turn']}轮" if m.get("turn") else "记录"
            lines.append(f"[{turn_label} / {m['role']}]\n"
                         f"<history_excerpt>{m['snippet']}</history_excerpt>")
        text = "\n\n".join(lines)
        return ok(self.name,
                  data={"query": query, "count": len(matches), "results": matches},
                  text=f"从历史记录中检索到 {len(matches)} 条匹配：\n\n{text}")
