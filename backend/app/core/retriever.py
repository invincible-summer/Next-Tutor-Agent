"""Retrieval core for uploaded course materials: structure-aware chunker +
pure-Python BM25 index.

The chunker preserves document structure: `\\f` hard page boundaries (PDF
pages / PPTX slides) become `page` metadata, paragraphs are packed whole,
and chunk boundaries snap to sentence ends (never cutting a sentence), with
sentence-level tail overlap between consecutive chunks. Chunks carry
file_id/page metadata so the vector track (core/vector_store.py) can cite
and delete by file.

BM25 stays the deterministic always-on retrieval track (CJK-aware
tokenization: character bigrams for Chinese, word tokens for Latin); the
optional embedding track fuses with it via RRF in core/hybrid.py.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field


_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """CJK-aware tokenizer: char bigrams for CJK, lowercase words for Latin."""
    tokens: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if _CJK_RE.match(ch):
            # emit unigram + bigram for CJK runs
            tokens.append(ch)
            if i + 1 < len(text) and _CJK_RE.match(text[i + 1]):
                tokens.append(ch + text[i + 1])
            i += 1
        else:
            m = _LATIN_WORD_RE.match(text, i)
            if m:
                tokens.append(m.group().lower())
                i = m.end()
            else:
                i += 1
    return tokens


@dataclass
class Chunk:
    chunk_id: str
    source: str          # filename
    text: str
    index: int           # position within source
    tokens: list[str] = field(default_factory=list)
    file_id: str = ""        # owning file id ("" for legacy/ad-hoc chunks)
    page: int | None = None  # 1-based page/slide number when known (PDF/PPTX)
    metadata: dict = field(default_factory=dict)


def retrievable_text(chunk: Chunk) -> str:
    """Index-time contextualized text（确定性 contextual-retrieval）。

    在正文前拼「书名 · 课题 · 章节路径 · 印刷页码」面包屑头，供 BM25
    tokenize 与（未来激活的）embedding 共用同一入口；展示文本（chunk.text）
    不变。动机：课文/章节的标题常只出现在标题行，正文 chunk 对
    「《湖畔夜色》讲了什么」类课题查询零词面覆盖 → BM25 召回为空
    （2026-08 trace 实证 no_absolute_evidence 假阴性）。面包屑头让每个
    chunk 都携带其所属课文/章节的词面，Anthropic contextual-retrieval
    的零 LLM 确定性版本。
    """
    meta = chunk.metadata or {}
    parts: list[str] = [chunk.source] if chunk.source else []
    section = " ".join(str(s) for s in (meta.get("section_path") or []) if str(s).strip())
    lesson = str(meta.get("lesson") or "").strip()
    if lesson and lesson not in section:
        parts.append(lesson)
    if section:
        parts.append(section)
    printed = meta.get("printed_page")
    if printed:
        parts.append(f"第{printed}页")
    head = " ".join(p for p in parts if p).strip()
    if not head or chunk.text.strip().startswith(head):
        return chunk.text
    return f"{head}\n{chunk.text}"


# Sentence-ending punctuation for CJK + Latin. The chunker never cuts inside
# a sentence: split points only exist right after one of these.
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；.!?])")
# Paragraph boundaries inside a page: a markdown-style --- separator line
# (checked first so it is consumed, not kept as content), blank lines, or
# single newlines.
_PARA_SPLIT_RE = re.compile(r"(?:^|\n)\s*-{3,}\s*(?=\n|$)|\n\s*\n|\n")
_SEPARATOR_ONLY_RE = re.compile(r"^-{3,}$")


def _split_sentences(text: str) -> list[str]:
    """Split into sentences, keeping terminators. Never loses characters."""
    return [s for s in _SENT_SPLIT_RE.split(text) if s]


def _atomic_units(paragraph: str, page: int | None, chunk_size: int) -> list[tuple[str, int | None]]:
    """Break one paragraph into units that each fit within chunk_size.

    Paragraphs that fit stay whole. Oversized paragraphs split on sentence
    boundaries; a sentence that still exceeds chunk_size is hard-cut by
    characters (last resort — the only place a sentence may be broken, and
    only because it cannot fit in a chunk at all).
    """
    if len(paragraph) <= chunk_size:
        return [(paragraph, page)]
    units: list[tuple[str, int | None]] = []
    for sent in _split_sentences(paragraph):
        if len(sent) <= chunk_size:
            units.append((sent, page))
        else:
            for i in range(0, len(sent), chunk_size):
                units.append((sent[i:i + chunk_size], page))
    return units


def _tail_overlap(text: str, overlap: int) -> str:
    """Trailing whole sentences of `text` totalling <= overlap chars.

    Overlap across chunk boundaries carries full sentences only — the old
    sliding window resumed mid-sentence, which produced chunks starting with
    half a sentence (bad for both BM25 tokens and embedding quality).
    """
    if overlap <= 0:
        return ""
    kept: list[str] = []
    total = 0
    for sent in reversed(_split_sentences(text)):
        sent = sent.strip()
        if not sent or total + len(sent) > overlap:
            break
        kept.append(sent)
        total += len(sent)
    return "".join(reversed(kept))


def chunk_text(text: str, source: str, file_id: str = "", chunk_size: int = 500,
               overlap: int = 80) -> list[Chunk]:
    """Structure-aware chunker: page/paragraph packing with sentence snapping.

    Two-level split: `\\f` hard page boundaries first (PDF pages / PPTX
    slides — a chunk never spans pages, and records the 1-based `page`), then
    paragraphs inside each page. Paragraphs (or sentences of oversized
    paragraphs) are greedily packed into chunks of <= chunk_size chars; chunk
    overlap reuses the previous chunk's trailing whole sentences, so no
    sentence is ever cut (except a single sentence longer than chunk_size,
    hard-cut as a last resort). chunk_id is "{file_id or source}#{idx}" —
    deterministic, so the vector index can re-derive it without storage.
    """
    text = text.strip()
    if not text:
        return []
    chunks: list[Chunk] = []
    pages = text.split("\f")
    multi_page = len(pages) > 1
    for page_no, page_text in enumerate(pages, start=1):
        page_text = page_text.strip()
        if not page_text:
            continue
        page = page_no if multi_page else None
        # Flatten this page to atomic (unit_text, page) pairs that each fit.
        units: list[tuple[str, int | None]] = []
        for para in _PARA_SPLIT_RE.split(page_text):
            para = para.strip()
            if para and not _SEPARATOR_ONLY_RE.match(para):
                units.extend(_atomic_units(para, page, chunk_size))
        _pack_units(units, chunks, source, file_id, chunk_size, overlap)
    return chunks


def _pack_units(units: list[tuple[str, int | None]], chunks: list[Chunk],
                source: str, file_id: str, chunk_size: int, overlap: int) -> None:
    """Greedy packing: fill each chunk up to chunk_size, then start a new one
    seeded with the previous chunk's sentence-level tail overlap. Appends to
    `chunks` (chunk ids stay globally sequential across pages)."""
    cur_parts: list[str] = []
    cur_len = 0
    cur_page: int | None = None

    def _flush() -> None:
        if not cur_parts:
            return
        piece = "\n".join(cur_parts)
        idx = len(chunks)
        chunks.append(Chunk(
            chunk_id=f"{file_id or source}#{idx}", source=source, text=piece,
            index=idx, tokens=tokenize(piece), file_id=file_id, page=cur_page,
        ))

    for unit, page in units:
        if cur_parts and cur_len + 1 + len(unit) > chunk_size:
            prev_text = "\n".join(cur_parts)
            _flush()
            prefix = _tail_overlap(prev_text, overlap)
            # Seed the next chunk with the overlap prefix only if the coming
            # unit still fits alongside it; otherwise start empty (avoids
            # flushing a prefix-only chunk when a hard-cut unit follows).
            if prefix and len(prefix) + 1 + len(unit) <= chunk_size:
                cur_parts = [prefix]
                cur_len = len(prefix)
            else:
                cur_parts = []
                cur_len = 0
            cur_page = None
        if cur_page is None and unit.strip():
            cur_page = page
        cur_parts.append(unit)
        cur_len += len(unit) + (1 if len(cur_parts) > 1 else 0)
    _flush()


class BM25Index:
    """BM25 over a set of chunks. Built once per session after upload."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.n = len(chunks)
        self.doc_len = [len(c.tokens) for c in chunks]
        self.avgdl = sum(self.doc_len) / self.n if self.n else 0.0
        # term frequencies per doc
        self.tf: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for c in chunks:
            freq: dict[str, int] = {}
            for t in c.tokens:
                freq[t] = freq.get(t, 0) + 1
            self.tf.append(freq)
            for t in freq:
                df[t] = df.get(t, 0) + 1
        # IDF (BM25 variant, always positive)
        self.idf: dict[str, float] = {}
        for t, d in df.items():
            self.idf[t] = math.log(1 + (self.n - d + 0.5) / (d + 0.5))

    def search(self, query: str, top_k: int = 4) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        q_tokens = tokenize(query)
        scores: list[tuple[int, float]] = []
        for i in range(self.n):
            s = 0.0
            freq = self.tf[i]
            dl = self.doc_len[i] or 1
            for qt in q_tokens:
                if qt not in freq:
                    continue
                f = freq[qt]
                idf = self.idf.get(qt, 0.0)
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                s += idf * f * (self.k1 + 1) / denom
            if s > 0:
                scores.append((i, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [(self.chunks[i], s) for i, s in scores[:top_k]]
