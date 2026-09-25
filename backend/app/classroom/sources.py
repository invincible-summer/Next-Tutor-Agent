"""课堂来源解析、冻结与复核（plan.md §7.1）。

resolve_classroom_sources 把备课 Brief 的 SourceSelection 解析为服务端签发的
SourceRecord 冻结集 + scope fingerprint。所有读取只走既有授权面
（core.workspace.readable_files / resolve_textbook_file / 本人 session），
绝不旁路扫描文件系统；模型侧永远只见 src_… ID。

namespace 约定（FileLocator.namespace，复核时按此重新定位正文）：
  - <student_key>           工作区上传（owner 的 library 数据目录）
  - "public"                公用教材（只读命名空间）
  - "session:<session_id>"  会话附件（session 的 upload dir）

冻结内容包括：file_id、namespace、全文 sha256、chunk_id 集、物理页/印刷页、
章节路径、取得时间、workspace scope revision；每份材料摘录 ≤4000 字、全课
证据总量 ≤40000 字（§15.4），不复制整本书进课堂目录。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from ..core.retriever import BM25Index, Chunk
from ..schemas import classroom as sc
from . import limits
from .errors import ClassroomError

# 会话附件命名空间前缀（见模块 docstring）。
SESSION_NS_PREFIX = "session:"

# 复核状态（verify_sources 返回值；发布前/展示期统一口径）。
STATUS_AVAILABLE = "available"    # 授权仍在、内容 hash 一致
STATUS_CHANGED = "changed"        # 仍可读但内容已变（显示"原始来源已变化"）
STATUS_REVOKED = "revoked"        # 授权丢失或正文已删（显示"原始来源不可用"）


@dataclass
class SourceIssue:
    """非致命解析问题：对应来源被跳过或摘录被截断，随 job snapshot 上报。"""

    code: str      # source_unauthorized / source_not_ready / evidence_truncated
    ref: str       # file_id 或 session_id
    message: str


@dataclass
class ResolvedSources:
    records: list[sc.SourceRecord] = field(default_factory=list)
    issues: list[SourceIssue] = field(default_factory=list)
    scope_fingerprint: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.records)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _trim_excerpt(text: str, limit: int) -> str:
    """句子边界截断：不留半句，回退硬截。"""
    text = text.strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    for cut in range(len(window) - 1, max(30, limit // 2), -1):
        if window[cut] in "。！？；.!?;":
            return window[:cut + 1].rstrip()
    return window.rstrip()


def _sorted_by_book_order(chunks: Iterable[Chunk]) -> list[Chunk]:
    """教材组卷顺序：chunk.index 是文件内的绝对位置，跨页/跨节稳定。"""
    return sorted(chunks, key=lambda c: c.index)


def _select_evidence(pool: list[Chunk], query: str,
                     top_k: int) -> list[Chunk]:
    """与 KnowledgeStore.search 同口径的 BM25 选取（小库全可见），
    结果再按组卷顺序排列——证据卡顺序=教材顺序，不按 BM25 分数。"""
    if not pool:
        return []
    if not query.strip() or len(pool) <= max(top_k, 8):
        return _sorted_by_book_order(pool)[:top_k]
    index = BM25Index(pool)
    hits = index.search(query, top_k=top_k)
    return _sorted_by_book_order(c for c, _score in hits)


def _chapter_matcher(chapters: list[sc.ChapterSelection]):
    """章节筛选：优先 section_path 前缀匹配；空 path 时按章节标题匹配
    chunk 的 section_path 或课程单元（lesson）。未选章节=全文件可用。"""

    def keep(chunk: Chunk) -> bool:
        if not chapters:
            return True
        section = [str(x) for x in (chunk.metadata.get("section_path") or [])]
        lesson = str(chunk.metadata.get("lesson") or "")
        for ch in chapters:
            prefix = [str(x) for x in ch.section_path]
            if prefix and section[:len(prefix)] == prefix:
                return True
            if not prefix and ch.title and (ch.title in section
                                            or ch.title == lesson):
                return True
        return False

    return keep


def _owned_workspace(workspace_id: str, owner: str):
    from ..core.workspace import _owner_of, load_workspace

    ws = load_workspace(workspace_id)
    if ws is None or _owner_of(ws) != owner:
        raise ClassroomError("source_not_found", "工作学习区不存在")
    return ws


def _authorized_file_map(owner: str, ws: Any) -> dict[str, tuple[dict, str, sc.SourceKind]]:
    """readable_files(ws) 的授权映射：file_id -> (meta, namespace, kind)。

    namespace 复用教材解析链（resolve_textbook_file）：自有教材=owner、
    公用教材=public、工作区上传=owner。未授权文件根本不进这个 map。"""
    from ..core.workspace import readable_files, resolve_textbook_file

    out: dict[str, tuple[dict, str, sc.SourceKind]] = {}
    for meta in readable_files(ws):
        fid = str(meta.get("id") or "")
        if not fid:
            continue
        scope = str(meta.get("source_scope") or "")
        if scope == "workspace":
            out[fid] = (meta, owner, sc.SourceKind.workspace_file)
        elif scope == "workspace_textbook":
            resolved, ns = resolve_textbook_file(owner, fid)
            if resolved is not None:
                out[fid] = (resolved, ns, sc.SourceKind.textbook)
    return out


def _library_chunks(namespace: str, file_id: str) -> list[Chunk]:
    from ..core.library import load_library

    return list(load_library(namespace).chunks_for(file_id))


def _library_text_path(namespace: str, file_id: str):
    from ..core.library import library_data_dir, load_library

    if load_library(namespace).find_file(file_id) is None:
        return None
    path = library_data_dir(namespace) / f"{file_id}.txt"
    return path if path.exists() else None


def _session_attachment(owner: str, namespace: str, file_id: str):
    """(chunks, text_path)；会话不存在/非本人/文件不在该会话 → (None, None)。"""
    from ..core.session import load_session

    sid = namespace[len(SESSION_NS_PREFIX):]
    sess = load_session(sid)
    if sess is None:
        return None, None
    if not sess.student_id or sess.student_id != owner:
        return None, None
    if next((f for f in sess.knowledge.files if f.get("id") == file_id),
            None) is None:
        return None, None
    chunks = [c for c in sess.knowledge.chunks if c.file_id == file_id]
    path = sess.knowledge.upload_dir / f"{file_id}.txt"
    return chunks, (path if path.exists() else None)


def _locate(owner: str, locator: sc.FileLocator):
    """复核/回读共用的定位：返回 (chunks, text_path)；不可用返回 (None, None)。"""
    if locator.namespace.startswith(SESSION_NS_PREFIX):
        return _session_attachment(owner, locator.namespace, locator.file_id)
    chunks = _library_chunks(locator.namespace, locator.file_id)
    path = _library_text_path(locator.namespace, locator.file_id)
    return chunks, path


def _content_hash(text_path) -> str | None:
    if text_path is None:
        return None
    try:
        return _sha256_bytes(text_path.read_bytes())
    except OSError:
        return None


def _fingerprint(owner: str, workspace_id: str,
                 entries: list[tuple[str, str, str]]) -> str:
    payload = json.dumps(
        {"owner": owner, "workspace": workspace_id,
         "sources": sorted(entries)},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(payload)


# ---------------------------------------------------------------------------
# 解析入口（§7.1 步骤 1–5）
# ---------------------------------------------------------------------------

def resolve_classroom_sources(
    owner: str,
    workspace_id: str,
    selection: sc.SourceSelection,
    *,
    topic: str = "",
    goals: Iterable[str] = (),
    now: datetime | None = None,
) -> ResolvedSources:
    """把 SourceSelection 解析为冻结 SourceRecord 集。

    非致命问题（未授权/未解析/截断）记入 issues 由调用方决定 needs_input；
    工作区不存在或非本人 → ClassroomError(source_not_found)。"""
    moment = now or datetime.now(timezone.utc)
    ws = _owned_workspace(workspace_id, owner)
    authorized = _authorized_file_map(owner, ws)

    query = " ".join([topic, *goals]).strip()
    result = ResolvedSources()
    seen: set[str] = set()
    fingerprint_entries: list[tuple[str, str, str]] = []
    budget_chars = limits.EVIDENCE_CHARS_TOTAL

    def _freeze(*, kind: sc.SourceKind, meta: dict, namespace: str,
                chunks: list[Chunk], text_path, ref: str) -> None:
        nonlocal budget_chars
        digest = _content_hash(text_path)
        if digest is None:
            result.issues.append(SourceIssue(
                "source_not_ready", ref, "资料尚未就绪（无已解析正文）"))
            return
        picked = _select_evidence(chunks, query, limits.SOURCE_CHUNKS_PER_FILE)
        if not picked:
            result.issues.append(SourceIssue(
                "source_not_ready", ref, "资料尚未就绪（无可检索片段）"))
            return
        excerpt_raw = "\n".join(c.text.strip() for c in picked if c.text.strip())
        if budget_chars <= 0:
            excerpt = ""
            result.issues.append(SourceIssue(
                "evidence_truncated", ref,
                "全课证据总量已达上限，该材料摘录被截断"))
        else:
            excerpt = _trim_excerpt(excerpt_raw,
                                    min(limits.EXCERPT_CHARS_PER_MATERIAL,
                                        budget_chars))
            if len(excerpt) < min(len(excerpt_raw),
                                  limits.EXCERPT_CHARS_PER_MATERIAL):
                result.issues.append(SourceIssue(
                    "evidence_truncated", ref, "摘录超出全课证据总量上限"))
        budget_chars -= len(excerpt)

        first = picked[0]
        printed = first.metadata.get("printed_page")
        record = sc.SourceRecord(
            source_id=_new_source_id(),
            kind=kind,
            title=str(meta.get("filename") or meta.get("original_filename")
                      or ref)[:200],
            locator=sc.FileLocator(
                namespace=namespace,
                file_id=str(meta.get("id") or ref),
                chunk_ids=[c.chunk_id for c in picked][:32],
                page=(first.page if isinstance(first.page, int)
                      and first.page >= 1 else None),
                printed_page=(str(printed)[:20] if printed else None),
                section_path=[str(s) for s in
                              (first.metadata.get("section_path") or [])][:8],
                content_hash=digest,
            ),
            excerpt=excerpt,
            excerpt_hash=_sha256_text(excerpt),
            retrieved_at=moment,
            verification={"workspace_scope_revision": ws.updated_at},
        )
        result.records.append(record)
        fingerprint_entries.append(
            (namespace, record.locator.file_id, digest))

    # 1) 教材/本区文件：必须落在 readable_files 授权面内
    for file_sel in selection.files:
        fid = file_sel.file_id
        if fid in seen:
            continue
        seen.add(fid)
        got = authorized.get(fid)
        if got is None:
            result.issues.append(SourceIssue(
                "source_unauthorized", fid,
                "文件不在该工作学习区的可读来源内"))
            continue
        meta, namespace, kind = got
        pool = [c for c in _library_chunks(namespace, fid)
                if _chapter_matcher(file_sel.chapters)(c)]
        text_path = _library_text_path(namespace, fid)
        if not pool and text_path is not None:
            # 文件可读但所选章节没有命中任何片段：按 plan §7.1 不得编内容
            result.issues.append(SourceIssue(
                "source_not_ready", fid, "所选章节在资料中未找到内容"))
            continue
        _freeze(kind=kind, meta=meta, namespace=namespace,
                chunks=pool, text_path=text_path, ref=fid)

    # 2) 显式会话附件：本人 session 且文件在该 session 可读
    for extra in selection.extra_sessions:
        sess = _load_own_session(extra.session_id, owner)
        if sess is None:
            result.issues.append(SourceIssue(
                "source_unauthorized", extra.session_id,
                "会话不存在或不属于当前用户"))
            continue
        wanted = list(extra.attachment_file_ids) or [
            str(f.get("id")) for f in sess.knowledge.files]
        for fid in wanted:
            key = f"{SESSION_NS_PREFIX}{extra.session_id}:{fid}"
            if key in seen:
                continue
            seen.add(key)
            meta = next((f for f in sess.knowledge.files
                         if f.get("id") == fid), None)
            if meta is None:
                result.issues.append(SourceIssue(
                    "source_unauthorized", fid,
                    f"文件不是会话 {extra.session_id} 的附件"))
                continue
            namespace = f"{SESSION_NS_PREFIX}{sess.session_id}"
            pool = [c for c in sess.knowledge.chunks if c.file_id == fid]
            path = sess.knowledge.upload_dir / f"{fid}.txt"
            _freeze(kind=sc.SourceKind.session_file, meta=meta,
                    namespace=namespace, chunks=pool,
                    text_path=(path if path.exists() else None), ref=fid)

    result.scope_fingerprint = _fingerprint(owner, workspace_id,
                                            fingerprint_entries)
    return result


def _new_source_id() -> str:
    from ..core import classroom_store as store

    return store.new_id("src")


def _load_own_session(session_id: str, owner: str):
    from ..core.session import load_session

    sess = load_session(session_id)
    if sess is None or not sess.student_id or sess.student_id != owner:
        return None
    return sess


# ---------------------------------------------------------------------------
# 复核（§7.1 步骤 6：发布前/生成途中重查授权与 hash）
# ---------------------------------------------------------------------------

def verify_sources(owner: str, workspace_id: str,
                   records: list[sc.SourceRecord]) -> dict[str, str]:
    """逐条复核冻结来源：available / changed / revoked。

    web 来源是发布时的快照，无需（也无法）重验外部页面。"""
    statuses: dict[str, str] = {}
    readable_ids: set[str] | None = None
    for record in records:
        if record.kind == sc.SourceKind.web:
            statuses[record.source_id] = STATUS_AVAILABLE
            continue
        locator = record.locator
        assert isinstance(locator, sc.FileLocator)
        if locator.namespace.startswith(SESSION_NS_PREFIX):
            chunks, path = _session_attachment(
                owner, locator.namespace, locator.file_id)
            if path is None:
                statuses[record.source_id] = STATUS_REVOKED
                continue
            digest = _content_hash(path)
            statuses[record.source_id] = (
                STATUS_AVAILABLE if digest == locator.content_hash
                else STATUS_CHANGED)
            continue
        # 工作区来源：授权面重查（取消选入/删除/换主都算 revoked）
        if readable_ids is None:
            try:
                ws = _owned_workspace(workspace_id, owner)
                readable_ids = {fid for fid in _authorized_file_map(owner, ws)}
            except ClassroomError:
                readable_ids = set()
        if locator.file_id not in readable_ids:
            statuses[record.source_id] = STATUS_REVOKED
            continue
        digest = _content_hash(
            _library_text_path(locator.namespace, locator.file_id))
        if digest is None:
            statuses[record.source_id] = STATUS_REVOKED
        elif digest != locator.content_hash:
            statuses[record.source_id] = STATUS_CHANGED
        else:
            statuses[record.source_id] = STATUS_AVAILABLE
    return statuses


def assert_sources_authorized(owner: str, workspace_id: str,
                              records: list[sc.SourceRecord]) -> None:
    """发布/继续生成前的硬门：任一 revoked/changed 即中止（§7.1 步骤 6）。"""
    statuses = verify_sources(owner, workspace_id, records)
    revoked = [rid for rid, s in statuses.items() if s == STATUS_REVOKED]
    changed = [rid for rid, s in statuses.items() if s == STATUS_CHANGED]
    if revoked:
        raise ClassroomError(
            "scope_changed",
            f"原始来源授权已丢失：{', '.join(sorted(revoked)[:8])}")
    if changed:
        raise ClassroomError(
            "source_changed",
            f"原始来源内容已变化：{', '.join(sorted(changed)[:8])}")


def load_locator_chunks(owner: str,
                        locator: sc.FileLocator) -> list[Chunk]:
    """按冻结 locator 回读证据 chunk（knowledge_read 的授权等价物）。

    返回顺序=冻结时的组卷顺序；正文 hash 不一致 → source_changed，
    文件/会话不可达 → scope_changed。绝不回退到未冻结的相邻片段。"""
    chunks, path = _locate(owner, locator)
    if path is None:
        raise ClassroomError("scope_changed", "来源正文已不可用")
    if _content_hash(path) != locator.content_hash:
        raise ClassroomError("source_changed", "来源正文与冻结时不一致")
    by_id = {c.chunk_id: c for c in chunks or []}
    return [by_id[cid] for cid in locator.chunk_ids if cid in by_id]
