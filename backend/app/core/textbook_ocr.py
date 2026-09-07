"""Durable textbook-only OCR round scheduler.

A round attempts only pending pages and returns before the retry delay.  Waiting
never holds the global OCR limiter nor the textbook build lock.  State is stored
inside TextbookRecord.ocr_state so process restarts can resume without a DB.
"""
from __future__ import annotations

import asyncio
import hashlib
import random
import time
from dataclasses import dataclass
from typing import Any

from . import ocr, ocr_policy, pdf_ocr
from .atomic import atomic_write_text

_TASKS: dict[tuple[str, str], asyncio.Task] = {}


class TextbookOCRDeferred(RuntimeError):
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


class TextbookParseCancelled(RuntimeError):
    """用户请求终止解析：构建各检查点观测到 parse_cancel_requested 后抛出，
    由 run_textbook_build 统一结算（保留已有文本，绝不自动重试）。"""


#: 永久性页面错误：模型已正常响应但该页无可提取内容（空白页/版权页）或页面
#: 无法渲染。重试不可能改变结果；达到 max_attempts 后按「空白页」收尾（保留
#: "" 占位、计入完成数），教材构建继续。瞬时错误（429/5xx/超时/连接）不在此列。
_PAGE_PERMANENT_ERRORS = {"empty_content", "render_failed"}


@dataclass(frozen=True)
class TextbookOCRRoundResult:
    status: str
    text: str
    state: dict[str, Any]


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _state_for(record: dict[str, Any], file_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    root = dict(record.get("ocr_state") or {})
    volumes = dict(root.get("volumes") or {})
    state = dict(volumes.get(file_id) or {})
    root["version"] = 1
    root["volumes"] = volumes
    return root, state


def _merge_volume_state(owner_id: str, textbook_id: str, file_id: str,
                        root: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """新鲜读-合并-写：把本卷最新 state 合并进记录当前 ocr_state。

    教材组并行建卷时多卷结算并发交错：若直接写轮起止快照（root），后写者
    会用旧快照回滚兄弟卷进度。这里每次重新读记录、只覆盖本卷键。同步函数
    且无 await，事件循环内原子；记录已删除时退回快照兜底（写路径自会 no-op）。
    """
    from . import textbook as tb_store
    rec = tb_store.find_textbook(owner_id, textbook_id)
    fresh = dict((rec or {}).get("ocr_state") or root)
    volumes = dict(fresh.get("volumes") or {})
    volumes[file_id] = state
    fresh["version"] = 1
    fresh["volumes"] = volumes
    fresh["updated_at"] = time.time()
    return fresh


def _save_state(owner_id: str, textbook_id: str, file_id: str,
                root: dict[str, Any], state: dict[str, Any], *, status: str,
                progress: dict[str, Any], error: str = "") -> None:
    from . import textbook as tb_store
    fresh = _merge_volume_state(owner_id, textbook_id, file_id, root, state)
    tb_store.update_textbook(owner_id, textbook_id, ocr_state=fresh, status=status,
                             progress=progress, error=error)


def _valid_pages(raw: Any, total_pages: int) -> set[int]:
    """Coerce a persisted 1-based page list into a valid in-range set."""
    try:
        return {int(p) for p in (raw or []) if 1 <= int(p) <= total_pages}
    except (TypeError, ValueError):
        return set()


def _write_text_and_chunks(owner_id: str, file_id: str, pages: list[str]) -> str:
    from .library import load_library, save_library, library_data_dir
    from .rag_index import _owner_rag_lock
    from .structured_chunker import active_chunk_schema, chunk_text_for_rag
    text = "\f".join(pages)
    # library 读改写须与 RAG 重建（to_thread + _owner_rag_lock）互斥：并发
    # 构建下两个 load→save 交错会互相丢 chunk 更新。整个 RMW 在工作线程
    # 执行（调用方 asyncio.to_thread），不阻塞事件循环。
    with _owner_rag_lock(owner_id):
        lib = load_library(owner_id)
        meta = lib.find_file(file_id)
        if meta is None:
            # 文件已被归档/直删：不写回，避免在磁盘上复活孤儿 .txt。
            return text
        if (dict(meta.get("rag_index") or {}).get("content_sha256") == _text_hash(text)):
            # 文本与已建索引一致（零进展的等待轮/崩溃后自愈复查）：跳过全书重切块。
            return text
        data = library_data_dir(owner_id)
        atomic_write_text(data / f"{file_id}.txt", text)
        chunks = chunk_text_for_rag(text, source=str(meta.get("filename") or ""), file_id=file_id)
        schema = active_chunk_schema()
        meta["chunk_schema"] = schema
        meta["rag_index"] = {"version": "rag-v2", "content_sha256": _text_hash(text),
                             "chunk_schema": schema, "status": "bm25_ready",
                             "chunk_count": len(chunks), "updated_at": time.time()}
        meta["char_count"] = len(text)
        meta["chunk_count"] = len(chunks)
        meta["updated_at"] = time.time()
        lib.chunks_by_file[file_id] = chunks
        save_library(lib)
    return text


async def _attempt_page(raw: bytes, page_idx: int, attempt: int,
                        timeout_seconds: int, *, local_fallback: bool = False):
    png = await asyncio.to_thread(pdf_ocr.render_page_pixmap, raw, page_idx)
    if png is None:
        return ocr.TextbookOCRResult(False, error_code="render_failed",
                                     error_summary="PDF 页面渲染失败", retryable=True,
                                     attempt=attempt), ""
    if local_fallback:
        text = await asyncio.to_thread(ocr._tesseract_ocr, png, psm=3)
        return None, text or ""
    result = await ocr.textbook_ocr_page_api(
        png, attempt=attempt, timeout_seconds=timeout_seconds)
    return result, ""


async def process_textbook_ocr_round(owner_id: str, textbook_id: str, file_id: str,
                                     raw: bytes, current_text: str, *,
                                     force_full: bool = False) -> TextbookOCRRoundResult:
    """Attempt one durable OCR round; schedule a later round when required.

    每页独立结算并立即落盘（.txt + ocr_state）：慢模型（单页可达 3 分钟）下
    进程中途被杀也不丢已完成页，重启后只重试 pending 页，绝不从头开始。
    """
    from . import textbook as tb_store
    record = tb_store.find_textbook(owner_id, textbook_id) or {}
    if record.get("parse_cancel_requested"):
        # 终止请求已到（端点先设标记再杀任务；本入口可能早于任务取消执行）
        root0, state0 = _state_for(record, file_id)
        state0["status"] = "cancelled"
        state0["pending_pages"] = []
        state0["next_retry_at"] = None
        _save_state(owner_id, textbook_id, file_id, root0, state0,
                    status=str(record.get("status") or "building"),
                    progress=dict(record.get("progress") or {}))
        return TextbookOCRRoundResult("cancelled", current_text, state0)
    metric_key = f"{owner_id}:{textbook_id}:{file_id}"
    page_texts = current_text.split("\f") if current_text else []
    # fitz 探针（页数 + 缺失时逐页文本层）走线程 + 全局锁：PyMuPDF 非线程
    # 安全，且整书 get_text 可能秒级，不能阻塞事件循环。
    def _probe(raw: bytes, texts: list[str]) -> tuple[int, list[str]]:
        from .pdf_ocr import FITZ_LOCK, pdf_page_count, pdf_page_texts
        if texts:
            return pdf_page_count(raw) or len(texts), texts
        probed = pdf_page_texts(raw)
        return (len(probed) or 0, probed) if probed else (0, texts)

    total_pages, page_texts = await asyncio.to_thread(_probe, raw, page_texts)
    if total_pages <= 0:
        total_pages = len(page_texts)
    if total_pages <= 0:
        # 探针失败（损坏 PDF/0 页）：写入卷级 failed 状态，让 deferred 出口
        # 的结算能落 graph_failed 且带错误摘要——无痕返回会把记录定格在
        # building（无卷状态可依据，重试驱动也不会介入）。书级 status 保持
        # 原值（与 cancelled 路径一致）：书级终态由 _settle_deferred_book_status
        # 在构建出口统一裁决，避免与并行兄弟卷的结算互相覆盖。
        root_pf, state_pf = _state_for(record, file_id)
        state_pf.update({
            "status": "failed", "pending_pages": [], "next_retry_at": None,
            "last_error_code": "probe_failed",
            "last_error_summary": "PDF 无法解析或页数为 0，OCR 未执行",
            "updated_at": time.time(),
        })
        _save_state(owner_id, textbook_id, file_id, root_pf, state_pf,
                    status=str(record.get("status") or "building"),
                    progress=dict(record.get("progress") or {}))
        return TextbookOCRRoundResult("failed", current_text, state_pf)
    if len(page_texts) < total_pages:
        page_texts.extend([""] * (total_pages - len(page_texts)))
    elif len(page_texts) > total_pages:
        page_texts = page_texts[:total_pages]

    from .config import settings
    cap = max(0, int(settings.pdf_ocr_max_pages))
    root, state = _state_for(record, file_id)
    _in_flight = state.get("status") in {"ocr", "waiting", "paused"}
    if (not force_full and bool(state.get("force_full"))
            and state.get("status") in {"ocr", "waiting"}):
        # force_full 意图继承（类级兜底）：任何调用路径丢了 force_full 标志，
        # 只要该卷仍处于未完成的全量轮次，就按全量继续——否则恢复轮会把
        # 已稠密页（旧 prompt 文本）当作无需 OCR，新 prompt 永远不生效。
        force_full = True
    elif (force_full and _in_flight and not bool(state.get("force_full"))):
        # 反向钳制：在途稀疏轮永不升级为全量。教材组的恢复/入队路径按组全局
        # 传播 force_full（任一卷全量未完成即整组传 True），若不加钳制，稀疏
        # 重试中的兄弟卷会命中下方意图不匹配重建，successful_pages/attempts
        # 全部清零、目标翻全量——整卷重 OCR（实测语文必修 150→0）。新的全量
        # 意图只对非在途状态（全新/已完结）生效；显式 full_ocr 刷新在上游清空
        # ocr_state，不受影响。
        force_full = False
    targets = (list(range(total_pages)) if force_full
               else pdf_ocr.pages_needing_ocr(page_texts))[:cap]
    source_hash = _text_hash(current_text)
    resumable = _in_flight
    if (not state or bool(state.get("force_full")) != bool(force_full)
            or (not resumable and state.get("source_text_sha256") != source_hash)):
        # 已判定空白的页跨状态重建继承：文本 hash 变化等触发的重建不再重复
        # OCR 已知空白页（显式 full_ocr 重建在上游清空 ocr_state，仍全量重试）。
        known_empty = _valid_pages(state.get("empty_pages"), total_pages) if state else set()
        targets = [i for i in targets if (i + 1) not in known_empty]
        state = {
            "version": 1, "status": "ocr", "force_full": bool(force_full),
            "source_text_sha256": source_hash, "total_pages": total_pages,
            "target_pages": [i + 1 for i in targets],
            "successful_pages": sorted(known_empty),
            "empty_pages": sorted(known_empty),
            "pending_pages": [i + 1 for i in targets],
            "paused_pages": [], "attempts": {}, "next_retry_at": None,
            "last_error_code": "", "last_error_summary": "",
            "api_success_count": 0, "local_fallback_count": 0,
            "updated_at": time.time(),
        }
    pending = sorted({int(p) - 1 for p in state.get("pending_pages") or []
                      if 1 <= int(p) <= total_pages})
    if not pending:
        state["status"] = "complete"
        state["updated_at"] = time.time()
        # 崩溃自愈复查：若 .txt 与已建 chunk 索引不一致（进程死在逐页写之后），
        # 这里补一次重切块；一致则内部按 hash 跳过，零开销。
        merged = await asyncio.to_thread(_write_text_and_chunks, owner_id, file_id, page_texts)
        done = len(state.get("successful_pages") or [])
        total = max(1, len(state.get("target_pages") or []))
        _save_state(owner_id, textbook_id, file_id, root, state, status="building",
                    progress={"stage": "ocr", "done": done, "total": total})
        return TextbookOCRRoundResult("complete", merged, state)

    policy = ocr_policy.get_retry_policy()
    attempts = {str(k): int(v) for k, v in dict(state.get("attempts") or {}).items()}
    successful = set(int(p) for p in state.get("successful_pages") or [])
    paused: set[int] = set(int(p) for p in state.get("paused_pages") or [])
    empty_pages = set(int(p) for p in state.get("empty_pages") or [])
    remaining = set(pending)
    failures: list[int] = []
    last_error = None
    total = max(1, len(state.get("target_pages") or []) or len(pending))
    alive = True  # 教材记录仍存在；归档删除后停止一切写回
    _user_cancelled = False  # parse_cancel_requested 检查点（逐页结算时更新）

    def _settle(page_idx: int, *, write_text: bool) -> None:
        """Persist one settled page immediately (crash-safe checkpoint)."""
        nonlocal alive, _user_cancelled
        remaining.discard(page_idx)
        if not alive:
            return
        try:
            if write_text:
                if tb_store.find_textbook(owner_id, textbook_id) is None:
                    alive = False  # 教材已删除：不再写回任何文件
                    return
                from .library import library_data_dir
                atomic_write_text(library_data_dir(owner_id) / f"{file_id}.txt",
                                  "\f".join(page_texts))
            state.update({
                "successful_pages": sorted(successful),
                "pending_pages": sorted(set(failures) | remaining),
                "paused_pages": sorted(paused),
                "empty_pages": sorted(empty_pages),
                "attempts": dict(attempts),
                "updated_at": time.time(),
            })
            fresh = _merge_volume_state(owner_id, textbook_id, file_id, root, state)
            updated = tb_store.update_textbook(
                owner_id, textbook_id, ocr_state=fresh,
                progress={"stage": "ocr", "done": len(successful), "total": total})
            alive = updated is not None
            if alive and updated.get("parse_cancel_requested"):
                _user_cancelled = True
        except Exception:
            pass

    async with ocr_policy.textbook_ocr_job() as job:
        async def one(page_idx: int):
            page_no = page_idx + 1
            attempt = attempts.get(str(page_no), 0) + 1
            try:
                result, _ = await ocr_policy.run_page(
                    job, lambda: _attempt_page(raw, page_idx, attempt,
                                               policy["request_timeout_seconds"]))
            except Exception as exc:  # 单页意外异常只记该页失败，不炸整轮
                result = ocr.TextbookOCRResult(False, error_code="round_error",
                                               error_summary=f"轮内异常：{exc}"[:200],
                                               retryable=True, attempt=attempt)
            return page_idx, attempt, result

        # 先按 pending 顺序显式建 task 再交给 as_completed：保证页启动顺序确定
        # （as_completed 对裸协程做 set() 会打乱），逐页结算仍按完成先后。
        page_tasks = [asyncio.ensure_future(one(i)) for i in pending]
        for fut in asyncio.as_completed(page_tasks):
            page_idx, attempt, result = await fut
            page_no = page_idx + 1
            attempts[str(page_no)] = attempt
            if result.success:
                if result.text.strip():
                    page_texts[page_idx] = result.text
                    successful.add(page_no)
                    empty_pages.discard(page_no)
                    state["api_success_count"] = int(state.get("api_success_count") or 0) + 1
                    _settle(page_idx, write_text=True)
                else:
                    successful.add(page_no)
                    _settle(page_idx, write_text=False)
                continue
            last_error = result
            at_limit = attempt >= int(policy["max_attempts"])
            if result.error_code in _PAGE_PERMANENT_ERRORS and at_limit:
                # 空白页/渲染失败：重试无意义，按空白页收尾（bounded_then_local
                # 仍先试一次本地 tesseract，符合该模式的兜底契约）。
                if policy["failure_mode"] == "bounded_then_local":
                    _, local_text = await _attempt_page(
                        raw, page_idx, attempt, policy["request_timeout_seconds"],
                        local_fallback=True)
                    if local_text.strip():
                        page_texts[page_idx] = local_text.strip()
                        successful.add(page_no)
                        empty_pages.discard(page_no)
                        state["local_fallback_count"] = int(
                            state.get("local_fallback_count") or 0) + 1
                        _settle(page_idx, write_text=True)
                        continue
                successful.add(page_no)
                empty_pages.add(page_no)
                _settle(page_idx, write_text=False)
            elif policy["failure_mode"] == "bounded_then_local" and at_limit:
                _, local_text = await _attempt_page(raw, page_idx, attempt,
                                                    policy["request_timeout_seconds"],
                                                    local_fallback=True)
                if local_text.strip():
                    page_texts[page_idx] = local_text.strip()
                    successful.add(page_no)
                    state["local_fallback_count"] = int(
                        state.get("local_fallback_count") or 0) + 1
                    _settle(page_idx, write_text=True)
                else:
                    paused.add(page_no)
                    _settle(page_idx, write_text=False)
            elif policy["failure_mode"] == "bounded_api_only" and at_limit:
                paused.add(page_no)
                _settle(page_idx, write_text=False)
            else:
                failures.append(page_no)
                _settle(page_idx, write_text=False)
            if _user_cancelled:
                break
        if _user_cancelled:
            # 用户终止：停掉在途页任务，state 落 cancelled（非 waiting，恢复
            # 调度器不会复活），已结算页全部保留在 .txt 里。
            for t in page_tasks:
                if not t.done():
                    t.cancel()
            state["status"] = "cancelled"
            state["pending_pages"] = []
            state["next_retry_at"] = None
            state["updated_at"] = time.time()
            _save_state(owner_id, textbook_id, file_id, root, state)
            ocr_policy.set_retry_waiting(0, None, metric_key)
            return TextbookOCRRoundResult(
                "cancelled",
                await asyncio.to_thread(_write_text_and_chunks, owner_id, file_id, page_texts),
                state)

    merged_text = await asyncio.to_thread(_write_text_and_chunks, owner_id, file_id, page_texts)
    state.update({
        "successful_pages": sorted(successful), "pending_pages": sorted(failures),
        "paused_pages": sorted(paused), "empty_pages": sorted(empty_pages),
        "attempts": attempts,
        "last_error_code": getattr(last_error, "error_code", "") if last_error else "",
        "last_error_summary": getattr(last_error, "error_summary", "") if last_error else "",
        "mode": policy["failure_mode"],
        "policy_mode": policy["failure_mode"], "policy_version": policy["policy_version"],
        "policy_generation": policy.get("policy_generation"),
        "configuration_blocked": bool(last_error and not last_error.retryable),
        "updated_at": time.time(),
    })
    done = len(successful)
    if failures:
        jitter = random.uniform(0, min(5.0, policy["retry_interval_seconds"] * 0.1))
        next_retry = time.time() + policy["retry_interval_seconds"] + jitter
        state["status"] = "waiting"
        state["next_retry_at"] = next_retry
        _save_state(owner_id, textbook_id, file_id, root, state, status="ocr_waiting",
                    progress={"stage": "ocr_waiting", "done": done, "total": total},
                    error=state["last_error_summary"])
        # 到点重试由队列 worker 的 _wait_book_terminal 门控就地驱动（唯一驱动），
        # 这里只登记等待指标供管理页展示。
        ocr_policy.set_retry_waiting(len(failures), next_retry, metric_key)
        return TextbookOCRRoundResult("waiting", merged_text, state)
    if paused:
        state["status"] = "paused"
        state["next_retry_at"] = None
        _save_state(owner_id, textbook_id, file_id, root, state, status="ocr_paused",
                    progress={"stage": "ocr_paused", "done": done, "total": total},
                    error=state["last_error_summary"] or "部分页面 OCR 已暂停")
        ocr_policy.set_retry_waiting(0, None, metric_key)
        return TextbookOCRRoundResult("paused", merged_text, state)

    state["status"] = "complete"
    state["pending_pages"] = []
    state["next_retry_at"] = None
    if empty_pages:
        state["last_error_code"] = "empty_pages"
        state["last_error_summary"] = (f"{len(empty_pages)} 页无可识别文本"
                                       f"（按空白页处理，不影响其余内容）")
    _save_state(owner_id, textbook_id, file_id, root, state, status="building",
                progress={"stage": "ocr", "done": total, "total": total})
    ocr_policy.set_retry_waiting(0, None, metric_key)
    return TextbookOCRRoundResult("complete", merged_text, state)


def schedule_textbook_resume(owner_id: str, textbook_id: str,
                             next_retry_at: float | None = None) -> None:
    """经 per-owner 构建队列入队一次自动续跑（串行契约下的唯一驱动入口）。

    旧的直连 resume runner 已移除：它在队列门控等待期间绕过队列直接构建，
    使书 A 等重试时其他书被并行开建。重试轮现由队列 worker 的
    _wait_book_terminal 门控就地驱动；本入口保留给回收站恢复等外部触发。
    next_retry_at 仅作签名兼容——入队后由 worker 按各卷状态决定到点时机。
    force_full 按组全局计算后传入，仅作「允许全量」的弱提示：在途稀疏轮在
    process_textbook_ocr_round 入口被按卷钳制，不会被升级成整卷重 OCR。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    rec = tb_store_find(owner_id, textbook_id)
    if rec is None or rec.get("status") not in {"ocr_waiting", "building"}:
        return
    # 终止请求：不再续跑（端点会另行结算终态）。
    if rec.get("parse_cancel_requested"):
        from . import textbook as tb_store
        tb_store.settle_cancelled_parse(owner_id, textbook_id)
        return
    volumes = ((rec.get("ocr_state") or {}).get("volumes") or {})
    force_full = any(bool(v.get("force_full"))
                     and v.get("status") in {"ocr", "waiting"}
                     for v in volumes.values())
    from app.agents.knowledge.textbook_builder import enqueue_textbook_build
    enqueue_textbook_build(owner_id, textbook_id, ocr_parallel=True,
                           force_reextract=False, force_full_ocr=force_full,
                           auto_retry=True)


async def _post_ready_rag(owner_id: str, textbook_id: str) -> None:
    """全书 ready 后的 RAG 收尾：重建 chunk 索引 + 向量 + 摘要（永不抛）。"""
    try:
        completed = tb_store_find(owner_id, textbook_id) or {}
        if completed.get("status") != "ready":
            return
        from .rag_index import (rebuild_textbook_rag, refresh_textbook_vectors,
                                summarize_textbook_rag)
        rag = await asyncio.to_thread(rebuild_textbook_rag, owner_id, completed,
                                      force=True)
        from . import textbook as tb_store
        tb_store.update_textbook(owner_id, textbook_id, rag_index=rag)
        await refresh_textbook_vectors(owner_id, completed)
        tb_store.update_textbook(owner_id, textbook_id,
                                 rag_index=summarize_textbook_rag(owner_id, completed))
    except Exception:
        pass


def tb_store_find(owner_id: str, textbook_id: str):
    from . import textbook as tb_store
    return tb_store.find_textbook(owner_id, textbook_id)


def cancel_textbook_ocr(owner_id: str, textbook_id: str) -> None:
    ocr_policy.clear_retry_waiting_prefix(f"{owner_id}:{textbook_id}:")
    task = _TASKS.pop((owner_id, textbook_id), None)
    if task is not None and not task.done():
        task.cancel()


def cancel_all_textbook_ocr() -> None:
    ocr_policy.clear_retry_waiting_prefix("")
    for task in list(_TASKS.values()):
        if not task.done():
            task.cancel()
    _TASKS.clear()


def resume_pending_textbook_ocr() -> int:
    """Schedule durable waiting records during FastAPI startup.

    经 per-owner 构建队列入队（严格串行：一本到达终态后才开建下一本），
    文件记录顺序即构建顺序。
    """
    from . import textbook as tb_store
    from app.agents.knowledge.textbook_builder import enqueue_textbook_build
    count = 0
    try:
        paths = list(tb_store._LIBRARY_DIR.glob("*.textbooks.json"))
    except Exception:
        return 0
    for path in paths:
        owner = path.name[:-len(".textbooks.json")]
        for rec in tb_store.load_textbooks(owner):
            if rec.get("status") != "ocr_waiting":
                continue
            if rec.get("parse_cancel_requested"):
                tb_store.settle_cancelled_parse(owner, rec["id"])
                continue
            # 进程重启续跑同样传播 force_full 意图（未完成的全量轮保持全量；
            # 在途稀疏轮由轮次入口按卷钳制，不受组级传播影响）。spec 缓存有
            # 效即复用（force_reextract=False）：prompt 升级经缓存指纹失效保证。
            volumes = ((rec.get("ocr_state") or {}).get("volumes") or {})
            force_full = any(bool(v.get("force_full"))
                             and v.get("status") in {"ocr", "waiting"}
                             for v in volumes.values())
            enqueue_textbook_build(owner, rec["id"], ocr_parallel=True,
                                   force_reextract=False, force_full_ocr=force_full,
                                   auto_retry=True)
            count += 1
    return count
