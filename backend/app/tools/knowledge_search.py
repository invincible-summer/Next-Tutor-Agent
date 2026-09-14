"""knowledge_search tool: RAG retrieval over uploaded course materials.

High-frequency, strong-certainty atomic tool (the Knowledge Agent capability).
Retrieval is hybrid when the vector track is configured (BM25 + embedding
recall fused with RRF via core/hybrid.py) and pure BM25 otherwise — the tool
contract and output format are identical either way, so the LLM never sees
the difference.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

from ..core.knowledge_store import KnowledgeStore
from ..core.tool_base import Tool
from ..core.tool_protocol import ErrorCode, err, ok

log = logging.getLogger(__name__)


def _gate_observability(gate: Any, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    duplicate_keys = {"same_page_duplicate", "exact_duplicate",
                      "near_duplicate_jaccard", "near_duplicate_simhash"}
    duplicate_drops = sum(int(gate.drop_reasons.get(key, 0) or 0)
                          for key in duplicate_keys)
    candidate_refs = sorted(
        f"{item.get('file_id') or item.get('source') or ''}:"
        f"{item.get('chunk_id') or item.get('index') or ''}"
        for item in candidates)
    fingerprint = hashlib.sha256("|".join(candidate_refs).encode()).hexdigest()
    return {
        "shadow_selected_count": len(gate.selected),
        "shadow_no_hit": bool(gate.no_hit),
        "tier": getattr(gate, "tier", "found"),
        "duplicate_drop_count": duplicate_drops,
        "duplicate_rate": round(duplicate_drops / max(1, len(candidates)), 4),
        "candidate_ref_sha256": fingerprint,
        "selected_context_hashes": [str(item.get("context_hash") or "")
                                    for item in gate.selected if item.get("context_hash")],
    }


def _confidence_tier(confidence: Any) -> str:
    """P9 置信度档位：未标定的线性组合分数不当概率展示，给模型可执行的档位语义。"""
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return ""
    if value >= 0.75:
        return "高"
    if value >= 0.45:
        return "中"
    return "低"


class KnowledgeSearchTool(Tool):
    name = "knowledge_search"
    description = (
        "在学生上传的课程资料/教材中检索与问题相关的知识点片段。"
        "当学生的问题涉及已上传资料内容、或需要引用教材原文讲解时调用。"
        "参数：query(检索问题,必填——优先直接使用篇目/课文名、章节名或概念名,"
        "如「沁园春长沙」「我与地坛」) top_k(返回片段数2-6,默认4)。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要检索的知识点或问题"},
            "top_k": {"type": "integer", "minimum": 2, "maximum": 6, "description": "返回的相关片段数量"},
        },
        "required": ["query"],
    }

    def __init__(self, store: KnowledgeStore,
                 scoped_stores: list[tuple[str, KnowledgeStore]] | None = None,
                 embed_client: Any | None = None,
                 student_id: str = "") -> None:
        # store: BM25 fallback (the session store, or the session+workspace
        # overlay). scoped_stores + embed_client: the optional vector track —
        # when both are present, run() goes through hybrid_search instead.
        # student_id: P6-C2 概念预索引加速通道（命中教材图谱概念/节时优先返回
        # 其所在章节的 chunks）；空则跳过该通道。
        self._store = store
        self._scoped_stores = scoped_stores
        self._embed_client = embed_client
        self._student_id = student_id
        self._file_meta = {str(f.get("id", "")): f
                           for f in getattr(store, "files", []) if f.get("id")}
        self._labels: dict[str, dict[str, str]] | None = None
        self._chunk_map: dict[str, Any] | None = None

    def _chunk_lookup(self):
        """chunk_id → {text, metadata} 视图（实例级缓存，供邻块扩展读取）。"""
        if self._chunk_map is None:
            index: dict[str, Any] = {}
            for c in getattr(self._store, "chunks", []) or []:
                if getattr(c, "chunk_id", None):
                    index[c.chunk_id] = c
            for _scope, s in (self._scoped_stores or []):
                for c in getattr(s, "chunks", []) or []:
                    if getattr(c, "chunk_id", None):
                        index.setdefault(c.chunk_id, c)
            self._chunk_map = index
        index = self._chunk_map

        def lookup(chunk_id: str):
            c = index.get(str(chunk_id or ""))
            if c is None:
                return None
            return {"text": c.text, "metadata": dict(c.metadata or {})}
        return lookup

    def _has_knowledge(self) -> bool:
        if self._scoped_stores:
            return any(getattr(s, "chunks", None) for _scope, s in self._scoped_stores)
        return self._store.has_knowledge()

    def _textbooks_building_hint(self) -> str:
        """NOT_FOUND 时区分「资料未含主题」与「教材仍在解析中」。

        只检查**本会话可见**文件对应的教材记录状态（自有 + 公共），不读内容。
        """
        try:
            from ..core import textbook as tb_store
            from ..core.textbook import PUBLIC_STUDENT_ID
            visible = {str(f.get("id", "")) for f in self._file_meta.values()}
            if not visible:
                return ""
            sids = {PUBLIC_STUDENT_ID}
            if self._student_id:
                sids.add(self._student_id)
            for sid in sids:
                for tb in tb_store.load_textbooks(sid):
                    if tb.get("status") not in {"building", "ocr_waiting"}:
                        continue
                    vols = ((tb.get("file_ids") or [])
                            if tb.get("kind") == "group"
                            else ([tb["file_id"]] if tb.get("file_id") else []))
                    if any(v in visible for v in vols):
                        return ("（注意：你选用的教材仍在后台解析/OCR 中，"
                                "完成后即可检索到其内容。）")
        except Exception:
            pass
        return ""

    async def run(self, **kwargs: Any):
        started_at = time.perf_counter()
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return err(self.name, ErrorCode.BAD_ARGS, "query 不能为空。")
        if not self._has_knowledge():
            return err(self.name, ErrorCode.NOT_FOUND,
                       "还没有上传任何课程资料。请先上传教材或笔记后再检索。")
        top_k = kwargs.get("top_k") or 4
        try:
            top_k = max(2, min(6, int(top_k)))
        except (TypeError, ValueError):
            top_k = 4
        raw_file_ids = kwargs.get("file_ids")
        file_ids = ({str(fid) for fid in raw_file_ids if str(fid).strip()}
                    if isinstance(raw_file_ids, (list, tuple, set)) else None)
        # focus_queries：内部参数（executor 预检索传入，不在 LLM 工具 schema 中）
        # ——task_understanding 让 LLM 预先分析的精炼检索词，优先占据变体槽位。
        raw_focus = kwargs.get("focus_queries")
        focus_queries = ([str(q).strip() for q in raw_focus if str(q).strip()][:3]
                         if isinstance(raw_focus, (list, tuple)) else None)
        # Recall broadly; Evidence Gate decides whether a candidate is safe to
        # show or inject. RRF rank itself is never treated as relevance proof.
        candidates = await self._multi_search(query, max(top_k * 8, 24),
                                              file_ids=file_ids,
                                              focus=focus_queries)
        if candidates:
            candidates = self._concept_boost(
                query, candidates, max(top_k * 8, 24), file_ids=file_ids)
            candidates = self._enrich_results(candidates)
        from ..core.config import settings
        from ..core.evidence_gate import apply_evidence_gate
        metadata_query = bool(re.search(
            r"作者|出版社|出版|版权|ISBN|CIP|目录|页码|哪一页", query, re.I))
        explicit_summary = bool(re.search(
            r"(?:这份|该份|这个|该文件|附件).*(?:讲|内容|总结|概括)|"
            r"(?:讲清楚|总结|概括).*(?:文件|资料|附件)?", query, re.I))
        # strict_relevance：内部参数（quiz grounding provider 传入，不在 LLM
        # 工具 schema 中）——出题依据必须过证据门的相关性判定；小材料直通
        # （allow_small_direct）只服务「总结这份文件」类问答，不得让 scope 外
        # 概念借文件引用拿到教材依据（plan.md §4.6）。
        strict_relevance = bool(kwargs.get("strict_relevance"))
        gate = apply_evidence_gate(
            query, candidates, top_k, allow_metadata=metadata_query,
            allow_small_direct=(not strict_relevance)
            and ((file_ids is not None) or (explicit_summary and len(candidates) <= 8)))
        if settings.rag_evidence_gate == "off":
            results = candidates[:top_k]
            omitted = max(0, len(candidates) - len(results))
        elif settings.rag_evidence_gate == "shadow":
            results = candidates[:top_k]
            omitted = gate.omitted
            for item in results:
                item.setdefault("evidence_excerpt", str(item.get("text") or "")[:500])
                item.setdefault("confidence", None)
                item.setdefault("context_hash", "")
        else:
            results = gate.selected
            omitted = gate.omitted
            if results:
                # P9 上下文重建：同课题块合并成课文节选、薄摘录补邻块语境。
                # 只消费 gate 已放行的结果——重建不做任何再召回。
                from ..core.evidence_context import reconstruct_evidence
                results = reconstruct_evidence(results, self._chunk_lookup())
        if settings.rag_evidence_gate == "on":
            import hashlib
            for item in results:
                raw_text = str(item.pop("text", "") or "")
                item["raw_text_sha256"] = hashlib.sha256(raw_text.encode()).hexdigest()
                item["raw_audit_ref"] = {"file_id": item.get("file_id"),
                                         "chunk_id": item.get("chunk_id")}
        if not results:
            latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            telemetry = {"gate_mode": settings.rag_evidence_gate,
                         "candidate_count": len(candidates), "selected_count": 0,
                         "omitted_count": len(candidates), "no_hit": True,
                         "tier": getattr(gate, "tier", "not_found"),
                         "drop_reasons": gate.drop_reasons, "latency_ms": latency_ms,
                         **_gate_observability(gate, candidates)}
            log.info("rag_evidence_gate %s", telemetry)
            scope_names = [str(m.get("filename") or fid)
                           for fid, m in self._file_meta.items()][:8]
            scope_hint = (f"（本次检索范围：{'、'.join(scope_names)}。"
                          "若学生要查的内容不在此范围，请先让学生在对话里引用"
                          "对应教材后重试。）" if scope_names else "")
            # P9：NOT_FOUND 只表示「没有词面/标题证据」，附上已尝试的内容核，
            # 允许模型换篇名/概念名重试一次，而不是直接放弃检索。
            from ..core.evidence_gate import effective_query
            core_terms = effective_query(query).strip()
            core_hint = (f"（本次检索使用的内容关键词：「{core_terms}」。"
                         "可换用篇目名/课文名/概念名再试一次；仍无结果才告知学生未找到。）"
                         if core_terms and core_terms != query else "")
            garble_drops = int(gate.drop_reasons.get("garble_text_layer", 0) or 0)
            garble_hint = ("（注意：命中的片段文本层疑似乱码（定制字体缺 ToUnicode "
                           "映射），无法作为可靠证据。建议学生在教材管理中检查该书"
                           "质量报告并重建索引。）"
                           if garble_drops and garble_drops >= max(2, len(candidates) // 2)
                           else "")
            return err(self.name, ErrorCode.NOT_FOUND,
                       f"在已上传的资料中没有找到与「{query}」相关的可靠证据。"
                       "禁止凭文件名猜测或编造资料内容——请如实告诉学生检索未命中，"
                       "并建议换关键词重试或检查资料是否包含该主题。"
                       + core_hint + garble_hint
                       + scope_hint
                       + self._textbooks_building_hint(),
                       data={"query": query, "count": 0,
                             "omitted_count": len(candidates), "no_hit": True,
                             "search_scope": scope_names,
                             "telemetry": telemetry})
        # Card and model context consume the same excerpt and context hash.
        def _locate(r: dict[str, Any]) -> str:
            parts: list[str] = []
            rng = r.get("printed_page_range")
            if rng and len(rng) == 2:
                parts.append(f" · 教材第{rng[0]}–{rng[1]}页")
            elif r.get("printed_page"):
                parts.append(f" · 教材第{r['printed_page']}页")
                if r.get("page"):
                    parts.append(f"（PDF第{r['page']}页）")
            elif r.get("page"):
                parts.append(f" · PDF第{r['page']}页")
            if r.get("block_type") == "figure":
                parts.append(" · [图]")
            elif r.get("block_type") == "table":
                parts.append(" · [表]")
            return "".join(parts)

        partial = getattr(gate, "tier", "found") == "partial" or any(
            r.get("partial") for r in results)
        head_line = (f"从课程资料中找到 {len(results)} 条部分相关证据（低置信，"
                     f"过滤 {omitted} 条；引用时须向学生说明依据较弱）"
                     if partial else
                     f"从课程资料中筛选出 {len(results)} 条可靠证据（过滤 {omitted} 条）")

        def _confidence_note(r: dict[str, Any]) -> str:
            conf = r.get("confidence", "legacy")
            tier = _confidence_tier(conf)
            return f"置信度 {conf}·{tier}" if tier else f"置信度 {conf}"

        snippets = "\n\n".join(
            f"[来源：{r.get('source') or r.get('filename', '资料')}"
            + (f" · 课文《{r['lesson_label']}》节选" if r.get("lesson_label") else "")
            + f"{' · ' + str(r['chapter']) if r.get('chapter') else ''}"
            + f"{' · ' + str(r['section']) if r.get('section') and r.get('section') != r.get('chapter') else ''}"
            + f"{_locate(r)}"
            + f" · chunk {r['index']}]\n({_confidence_note(r)})\n"
            f"<material_excerpt>{r.get('evidence_excerpt') or ''}</material_excerpt>"
            for r in results
        ) + "\n（需要更多上下文时，可用 knowledge_read 按上面的 chunk 指针读取完整原文及相邻片段。）"
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        telemetry = {"gate_mode": settings.rag_evidence_gate,
                     "candidate_count": len(candidates),
                     "selected_count": len(results), "omitted_count": omitted,
                     "no_hit": False, "tier": getattr(gate, "tier", "found"),
                     "drop_reasons": gate.drop_reasons,
                     "latency_ms": latency_ms,
                     **_gate_observability(gate, candidates)}
        log.info("rag_evidence_gate %s", telemetry)
        bundle = {"query": query, "selected": results,
                  "omitted_count": omitted, "no_hit": False,
                  "context_hashes": [r.get("context_hash", "") for r in results]}
        return ok(self.name,
                  data={"query": query, "results": results,
                        "count": len(results), "omitted_count": omitted,
                        "partial": partial,
                        "evidence_bundle": bundle, "telemetry": telemetry,
                        "file_ids": sorted(file_ids) if file_ids is not None else None},
                  text=f"{head_line}：\n\n{snippets}")

    async def _search(self, query: str, top_k: int,
                      *, file_ids: set[str] | None = None) -> list[dict[str, Any]]:
        """Hybrid (BM25 + vector RRF) when the vector track is wired, else the
        store's deterministic BM25. Any hybrid failure falls back to BM25."""
        if self._scoped_stores and self._embed_client is not None:
            try:
                from ..core.hybrid import hybrid_search
                scoped = self._scoped_stores
                if file_ids is not None:
                    scoped = [(scope, self._filtered_store(store, file_ids))
                              for scope, store in scoped]
                return await hybrid_search(scoped, query, top_k,
                                           embed_client=self._embed_client)
            except Exception:
                pass  # fall through to the deterministic track
        return self._store.search(query, top_k=top_k, file_ids=file_ids)

    async def _multi_search(self, query: str, top_k: int,
                            *, file_ids: set[str] | None = None,
                            focus: list[str] | None = None) -> list[dict[str, Any]]:
        """Bounded deterministic multi-query recall + cross-file coverage."""
        variants = self._query_variants(query, focus)
        by_id: dict[str, dict[str, Any]] = {}
        rrf: dict[str, float] = {}
        for variant in variants:
            hits = await self._search(variant, max(top_k * 8, 16), file_ids=file_ids)
            for rank, hit in enumerate(hits):
                cid = str(hit.get("chunk_id") or
                          f"{hit.get('file_id')}#{hit.get('index')}")
                if cid not in by_id:
                    by_id[cid] = dict(hit)
                    by_id[cid]["bm25_score"] = float(hit.get("bm25_score", hit.get("score") or 0.0) or 0.0)
                    by_id[cid]["variant_hits"] = [variant]
                else:
                    by_id[cid].setdefault("variant_hits", []).append(variant)
                    by_id[cid]["bm25_score"] = max(
                        float(by_id[cid].get("bm25_score") or 0.0),
                        float(hit.get("bm25_score", hit.get("score") or 0.0) or 0.0))
                rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (60 + rank + 1)
        ranked = []
        for cid, score in sorted(rrf.items(), key=lambda item: item[1], reverse=True):
            item = dict(by_id[cid])
            item["score"] = round(score, 4)
            item["retrieval_strategy"] = ("multi_query_rrf" if len(variants) > 1
                                           else "bm25_or_hybrid")
            ranked.append(item)
        return self._diversify_files(ranked, top_k)

    # Deterministic bilingual aliases for common textbook terminology.
    _ALIASES = {
        "洛伦兹变化": "洛伦兹变换 Lorentz transformation",
        "洛伦兹变换": "Lorentz transformation",
        "牛顿第二定律": "Newton second law",
        "动量守恒": "conservation of momentum",
        "能量守恒": "conservation of energy",
        "电磁感应": "electromagnetic induction Faraday",
        "导数": "derivative",
        "积分": "integral",
    }

    @staticmethod
    def _compact_variant(query: str) -> str:
        """Polite/punctuation-stripped compact variant; "" when nothing is left."""
        compact = re.sub(
            r"(?:请|帮我|能否|可以|结合|根据|关于|讲一下|解释一下|分析一下|这份|该份|附件|教材|资料|文件)",
            " ", query, flags=re.IGNORECASE)
        compact = re.sub(r"[，。！？,.!?：:；;（）()\[\]{}]+", " ", compact)
        compact = " ".join(part for part in compact.split() if part).strip()
        return compact if compact and len(compact) >= 2 else ""

    @staticmethod
    def _question_core_variant(query: str) -> str:
        """Content-word core of a natural-language question; "" for non-questions."""
        # 自然语言问句：内容词核变体（剥疑问尾巴/学段词，证据门同源逻辑），
        # 让 BM25 对口语问句直接按内容词召回，而非被疑问词稀释。
        from ..core.evidence_gate import is_natural_question, question_core
        if not is_natural_question(query):
            return ""
        core_q = re.sub(r"\s+", " ", question_core(query)).strip()
        return core_q if len(core_q.replace(" ", "")) >= 2 else ""

    @classmethod
    def _query_variants(cls, query: str, focus: list[str] | None = None) -> list[str]:
        """Variant set (max 3) for multi-query recall.

        `focus` carries the LLM-refined search terms the executor's
        pre-retrieval got from task_understanding. When present they take the
        variant slots first — a spoken full sentence is a poor BM25 query and
        a top reason pre-retrieval misses — while the deterministic
        compact/question-core variants of the original message keep recall
        breadth in any remaining slot.
        """
        focus_terms = [str(v).strip() for v in (focus or []) if str(v).strip()]
        if focus_terms:
            variants = list(dict.fromkeys(focus_terms))[:3]
            for extra in (cls._compact_variant(query),
                          cls._question_core_variant(query)):
                if len(variants) >= 3:
                    break
                if extra and extra not in variants:
                    variants.append(extra)
            for zh, en in cls._ALIASES.items():
                if len(variants) >= 3:
                    break
                if any(zh in term for term in focus_terms):
                    expansion = f"{zh} {en}"
                    if expansion not in variants:
                        variants.append(expansion)
            return [v for v in variants if v][:3]
        variants = [query.strip()]
        compact = cls._compact_variant(query)
        if compact and compact != variants[0]:
            variants.append(compact)
        core_q = cls._question_core_variant(query)
        if core_q and core_q not in variants:
            variants.append(core_q)
        for zh, en in cls._ALIASES.items():
            if zh in query:
                variants.append(f"{zh} {en}")
        if re.search(r"作者|谁写|编者", query):
            variants.append("作者 编者 编写 统稿")
        return list(dict.fromkeys(v for v in variants if v))[:3]

    @staticmethod
    def _diversify_files(results: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        if len(results) <= top_k:
            return results
        by_file: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for item in results:
            key = str(item.get("file_id") or item.get("source") or "unknown")
            if key not in by_file:
                by_file[key] = []
                order.append(key)
            by_file[key].append(item)
        chosen: list[dict[str, Any]] = []
        used: set[str] = set()
        # First pass gives each file with a real candidate one slot.
        for key in order:
            if len(chosen) >= top_k:
                break
            item = by_file[key][0]
            chosen.append(item)
            used.add(str(item.get("chunk_id")))
        for item in results:
            if len(chosen) >= top_k:
                break
            cid = str(item.get("chunk_id"))
            if cid not in used:
                chosen.append(item)
                used.add(cid)
        return chosen

    @staticmethod
    def _infer_section(text: str) -> str:
        for line in (text or "").splitlines()[:4]:
            clean = line.strip().strip("#*- ")
            if not clean or len(clean) > 80:
                continue
            if re.search(
                    r"第\s*[一二三四五六七八九十百0-9]+\s*[章节篇课讲单元]"
                    r"|chapter\s*\d+",
                    clean, re.I):
                return clean
        return ""

    def _chapter_section_labels(self) -> dict[str, dict[str, str]]:
        """chunk_id(库侧) → {chapter, section} 标注表（懒加载，工具实例级缓存）。

        来源是可见教材的概念/节预索引（tb-*.chunks.json）：节条目给出精确的
        「单元 · 课/篇目」定位，概念条目兜底章名。只加载卷 file_id 与当前
        检索域相交的教材索引。
        """
        if self._labels is not None:
            return self._labels
        labels: dict[str, dict[str, str]] = {}
        try:
            visible: set[str] = set()
            for fid, meta in self._file_meta.items():
                visible.add(str(meta.get("library_file_id") or fid))
            for _scope, store in (self._scoped_stores or []):
                for c in getattr(store, "chunks", []) or []:
                    if c.file_id:
                        visible.add(c.file_id)
            if not visible:
                self._labels = labels
                return labels
            from ..core import textbook as tb_store
            from ..core.textbook import PUBLIC_STUDENT_ID
            from ..agents.knowledge.store import load_concept_chunks
            sids: list[str] = []
            if self._student_id:
                sids.append(self._student_id)
            if PUBLIC_STUDENT_ID not in sids:
                sids.append(PUBLIC_STUDENT_ID)
            for sid in sids:
                for tb in tb_store.load_textbooks(sid):
                    fids = (tb.get("file_ids") or []
                            if tb.get("kind") == "group"
                            else ([tb["file_id"]] if tb.get("file_id") else []))
                    if not fids or not (set(fids) & visible):
                        continue
                    idx = load_concept_chunks(sid, str(tb.get("topic_key") or ""))
                    for entry in ((idx or {}).get("concepts") or {}).values():
                        chapter = str(entry.get("chapter") or "")
                        section = (str(entry.get("name") or "")
                                   if str(entry.get("kind") or "") == "section" else "")
                        if not chapter and not section:
                            continue
                        for cid in entry.get("chunk_ids") or []:
                            cur = labels.get(cid) or {}
                            if chapter and not cur.get("chapter"):
                                cur["chapter"] = chapter
                            if section and not cur.get("section"):
                                cur["section"] = section
                            if cur:
                                labels[cid] = cur
        except Exception:
            pass
        self._labels = labels
        return labels

    def _enrich_results(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        labels = self._chapter_section_labels()
        out: list[dict[str, Any]] = []
        for raw in results:
            item = dict(raw)
            fid = str(item.get("file_id", ""))
            meta = self._file_meta.get(fid, {})
            item.setdefault("filename", item.get("source") or meta.get("filename", ""))
            # 章/节标注优先级：概念索引（节/章条目） > concept_boost 附带章名
            # > 结构化 section_path 启发式。会话引用的教材是库文件的快照副本
            # （chunk_id 前缀是会话 id），经 library_file_id 映射回库侧 id 查表。
            lookup_id = str(item.get("chunk_id") or "")
            lib_fid = str(meta.get("library_file_id") or "")
            if lib_fid and lookup_id.startswith(f"{fid}#"):
                lookup_id = f"{lib_fid}#{lookup_id.split('#', 1)[-1]}"
            loc = labels.get(lookup_id) or {}
            item.setdefault("section", loc.get("section")
                            or item.get("section")
                            or self._infer_section(str(item.get("text", ""))))
            item.setdefault("chapter", item.get("concept_chapter")
                            or loc.get("chapter") or item.get("section") or "")
            item.setdefault("source_scope", meta.get("source_scope", "session"))
            item.setdefault("source_visibility", meta.get("source_visibility", "session_private"))
            item["location_type"] = ("printed_page" if item.get("printed_page")
                                     else "page" if item.get("page") else "chunk")
            printed = item.get("printed_page")
            page = item.get("page")
            if printed:
                item["location_label"] = (f"教材第 {printed} 页"
                                          + (f"（PDF 第 {page} 页）" if page else ""))
            elif page:
                item["location_label"] = f"PDF 第 {page} 页"
            else:
                item["location_label"] = f"片段 {int(item.get('index', 0)) + 1}"
            block_type = (item.get("block_types") or [None])[0]
            if block_type:
                item["block_type"] = block_type
            out.append(item)
        return out

    @staticmethod
    def _filtered_store(store: KnowledgeStore, file_ids: set[str]) -> KnowledgeStore:
        filtered = KnowledgeStore(upload_dir=store.upload_dir)
        filtered.chunks = [c for c in getattr(store, "chunks", [])
                           if c.file_id in file_ids]
        return filtered

    def _concept_boost(self, query: str, results: list[dict[str, Any]],
                       top_k: int, *, file_ids: set[str] | None = None) -> list[dict[str, Any]]:
        """P6-C2 概念级预索引加速：query 命中教材图谱概念且该概念有预索引时，
        先在其**章节检索域**（构建期预计算的 chunk 集合）内检索并前置，再补
        普通结果——章节内命中更准、检索域更小。纯确定性、零 LLM；
        教材未选入会话（chunks 不在合并 store）或任何失败 → 原样返回。
        """
        sid = self._student_id
        if not sid:
            return results
        try:
            from ..agents.knowledge import get_knowledge_service, is_enabled
            if not is_enabled():
                return results
            node = get_knowledge_service().match_concept(query, student_id=sid)
            if node is None or not node.id.startswith("custom.tb-"):
                return results
            parts = node.id.split(".")
            if len(parts) < 3:
                return results
            topic_key = parts[1]
            from ..agents.knowledge.store import load_concept_chunks
            from ..core.textbook import PUBLIC_STUDENT_ID
            idx = load_concept_chunks(sid, topic_key)
            if idx is None and sid != PUBLIC_STUDENT_ID:
                idx = load_concept_chunks(PUBLIC_STUDENT_ID, topic_key)
            entry = (idx or {}).get("concepts", {}).get(node.id)
            if not entry:
                return results
            ids = set(entry.get("chunk_ids") or [])
            pool = [c for c in getattr(self._store, "chunks", [])
                    if c.chunk_id in ids and
                    (file_ids is None or c.file_id in file_ids)]
            if not pool:
                return results  # 教材未被选入会话：索引不可用，回落普通检索
            from ..core.retriever import BM25Index
            boosted: list[dict[str, Any]] = []
            seen: set[tuple] = set()
            for c, sc in BM25Index(pool).search(query, top_k=2):
                boosted.append({"source": c.source, "filename": c.source,
                                "file_id": c.file_id, "chunk_id": c.chunk_id,
                                "index": c.index, "text": c.text,
                                "score": round(sc, 1), "page": c.page,
                                "concept": entry.get("name", ""),
                                "concept_chapter": entry.get("chapter", ""),
                                "concept_bonus": 1.0})
                seen.add((c.source, c.index))
            for r in results:
                if (r.get("source"), r.get("index")) not in seen:
                    boosted.append(r)
            return boosted[:max(top_k, 2)]
        except Exception:
            return results
