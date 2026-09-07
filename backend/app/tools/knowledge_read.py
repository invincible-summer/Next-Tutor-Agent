"""knowledge_read tool: fetch full chunk text by the pointer shown on evidence cards.

P9 反碎片化的读取侧：knowledge_search 的证据卡只携带 250-900 字符摘录，
模型看到「chunk N」指针后可用本工具取该片段完整原文及相邻片段（消费
structured chunker 的 prev/next 链）。纯读取、零 LLM、不经评分门——
指针本身来自已过门的检索结果，越权面与 knowledge_search 完全一致
（同一 store 构造，只读授权域）。
"""
from __future__ import annotations

import re
from typing import Any

from ..core.knowledge_store import KnowledgeStore
from ..core.tool_base import Tool
from ..core.tool_protocol import ErrorCode, err, ok

MAX_CHARS = 4000
DEFAULT_CHARS = 2500
NEIGHBOR_CHARS = 400


def _clean_head(text: str, chars: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "").strip())
    if len(text) <= chars:
        return text
    window = text[:chars]
    for cut in range(len(window) - 1, max(30, chars // 2), -1):
        if window[cut] in "。！？；，、":
            return window[:cut + 1]
    return window


def _escape(text: str) -> str:
    return re.sub(r"<\s*(/?)\s*(material_excerpt|ocr_material|user_input)[^>]*>",
                  lambda m: f"［{m.group(1)}{m.group(2)}］", text, flags=re.I)


class KnowledgeReadTool(Tool):
    name = "knowledge_read"
    description = (
        "按 knowledge_search 证据卡上的指针（chunk 序号/PDF页码）读取教材原文的"
        "完整片段及其相邻片段。当证据摘录太短、需要前后文（课文上下文、定理完整"
        "表述、推导过程）时调用。参数：chunk(证据卡上的 chunk 序号，与 page "
        "二选一) page(PDF页码) file_id(多文件重名时消歧，可选) "
        "span(上下文范围 current/prev/next/both，默认 both)。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "chunk": {"type": "integer", "minimum": 0,
                      "description": "证据卡 [来源：… · chunk N] 里的 N"},
            "page": {"type": "integer", "minimum": 1,
                      "description": "PDF 页码（无 chunk 序号时按页取该页起始片段）"},
            "file_id": {"type": "string", "description": "目标文件 id（跨文件消歧）"},
            "span": {"type": "string", "enum": ["current", "prev", "next", "both"],
                     "description": "是否附带相邻片段，默认 both"},
            "chars": {"type": "integer", "minimum": 200, "maximum": MAX_CHARS,
                      "description": f"正文字符上限，默认 {DEFAULT_CHARS}"},
        },
        "required": [],
    }

    def __init__(self, store: KnowledgeStore,
                 scoped_stores: list[tuple[str, KnowledgeStore]] | None = None) -> None:
        self._store = store
        self._scoped_stores = scoped_stores

    # --- 授权域内的 chunk 池（与 knowledge_search 相同的可见性边界） --------
    def _ordered_chunks(self) -> list[Any]:
        seen: set[str] = set()
        out: list[Any] = []
        for store in [self._store] + [s for _scope, s in (self._scoped_stores or [])]:
            for c in getattr(store, "chunks", []) or []:
                cid = str(getattr(c, "chunk_id", "") or "")
                if cid and cid not in seen:
                    seen.add(cid)
                    out.append(c)
        return out

    async def run(self, **kwargs: Any):
        chunk_no = kwargs.get("chunk")
        page = kwargs.get("page")
        if chunk_no is None and page is None:
            return err(self.name, ErrorCode.BAD_ARGS,
                       "请提供 chunk 序号（证据卡上的指针）或 PDF 页码。")
        try:
            chunk_no = None if chunk_no is None else max(0, int(chunk_no))
            page = None if page is None else max(1, int(page))
        except (TypeError, ValueError):
            return err(self.name, ErrorCode.BAD_ARGS, "chunk/page 必须是整数。")
        file_id = str(kwargs.get("file_id") or "").strip() or None
        span = str(kwargs.get("span") or "both").strip().lower()
        if span not in {"current", "prev", "next", "both"}:
            span = "both"
        try:
            chars = max(200, min(MAX_CHARS, int(kwargs.get("chars") or DEFAULT_CHARS)))
        except (TypeError, ValueError):
            chars = DEFAULT_CHARS
        chunks = self._ordered_chunks()
        if not chunks:
            return err(self.name, ErrorCode.NOT_FOUND, "当前会话没有可读的课程资料。")

        def _matches(c: Any) -> bool:
            if file_id and str(getattr(c, "file_id", "") or "") != file_id:
                return False
            if chunk_no is not None:
                return int(getattr(c, "index", -1)) == chunk_no
            return int(getattr(c, "page", -1) or -1) == page
        hits = [c for c in chunks if _matches(c)]
        if not hits:
            return err(self.name, ErrorCode.NOT_FOUND,
                       f"没有找到匹配的片段（chunk={chunk_no}, page={page}）。"
                       "请以证据卡上的指针为准。")
        if len({str(getattr(c, "file_id", "") or "") for c in hits}) > 1 and not file_id:
            names = sorted({str(getattr(c, "source", "") or "") for c in hits})[:5]
            return err(self.name, ErrorCode.BAD_ARGS,
                       "该指针在多个文件中都存在，请补充 file_id 消歧："
                       + "、".join(names))
        # 按 (file, index) 排序取池，邻块取目标前后各一个。
        pool = sorted([c for c in chunks
                       if str(getattr(c, "file_id", "") or "")
                       == str(getattr(hits[0], "file_id", "") or "")],
                      key=lambda c: int(getattr(c, "index", 0) or 0))
        target = hits[0] if chunk_no is not None else min(
            hits, key=lambda c: int(getattr(c, "index", 0) or 0))
        pos = next((i for i, c in enumerate(pool)
                    if str(getattr(c, "chunk_id", "")) == str(getattr(target, "chunk_id", ""))),
                   0)

        def _label(c: Any) -> str:
            metadata = getattr(c, "metadata", {}) or {}
            parts = [str(getattr(c, "source", "") or "资料")]
            if metadata.get("lesson"):
                parts.append(f"课文《{metadata['lesson']}》")
            printed = metadata.get("printed_page")
            if printed:
                parts.append(f"教材第{printed}页")
            elif getattr(c, "page", None):
                parts.append(f"PDF第{c.page}页")
            parts.append(f"chunk {int(getattr(c, 'index', 0) or 0)}")
            return " · ".join(parts)

        def _block(c: Any, body: str) -> str:
            return (f"[原文：{_label(c)}]\n"
                    f"<material_excerpt>{_escape(body)}</material_excerpt>")

        from ..core.text_quality import text_garble_ratio
        segments: list[str] = []
        if span in {"prev", "both"} and pos > 0:
            prev = pool[pos - 1]
            segments.append(_block(prev, _clean_head(prev.text, NEIGHBOR_CHARS)))
        body = str(target.text or "").strip()
        garble_note = ""
        if text_garble_ratio(body) >= 0.05:
            garble_note = ("\n（注意：该片段文本层疑似乱码，内容可能不可读——"
                           "建议学生在教材管理中重建该书的索引。）")
        segments.append(_block(target, body[:chars]))
        if span in {"next", "both"} and pos + 1 < len(pool):
            nxt = pool[pos + 1]
            segments.append(_block(nxt, _clean_head(nxt.text, NEIGHBOR_CHARS)))
        metadata = getattr(target, "metadata", {}) or {}
        text = "\n\n".join(segments) + garble_note
        return ok(self.name,
                  data={"chunk_id": getattr(target, "chunk_id", ""),
                        "file_id": getattr(target, "file_id", ""),
                        "index": int(getattr(target, "index", 0) or 0),
                        "page": getattr(target, "page", None),
                        "printed_page": metadata.get("printed_page"),
                        "lesson": metadata.get("lesson"),
                        "chars": min(len(body), chars), "span": span,
                        "garbled": bool(garble_note)},
                  text=f"已读取教材原文片段：\n\n{text}")
