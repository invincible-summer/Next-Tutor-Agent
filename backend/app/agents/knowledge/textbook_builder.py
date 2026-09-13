"""Textbook → knowledge-graph background build pipeline (P2).

教材 → 章节切片 → 骨架 LLM 调用（学科/学段推断）→ 逐章概念抽取 LLM 调用 ×N
→ 确定性合并（DAG 守卫/锚定/上限）→ 写入 M5.7 store。永不抛出：任何异常落
记录 status="graph_failed"（教材仍可检索），可经 rebuild_graph 重试。

设计契约（DESIGN §1.4 护栏 + §5.2）：
  - 后台任务（asyncio.create_task，对话零等待）；per-student 构建锁 + 全局
    Semaphore(2) 防 429 风暴；LLM 调用全走 complete(disable_thinking=True)。
  - 复用 custom_graph.spec_to_graph 的确定性件（id 命名空间/两遍扫描/DAG 守卫/
    严格锚定），仅把上限/level 参数化。图谱写入 M5.7 store（同一套唯一性铁律）。
  - 失败只记 status/warnings，绝不影响教材检索或对话流。
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import time
import unicodedata
from bisect import bisect_right
from collections import deque
from typing import Any

from ...core import textbook as tb_store
from ...core import pdf_ocr
from ...core import textbook_pipeline
from ...core.config import settings
from . import custom_graph as cg
from . import store as kg_store
from .manager import get_knowledge_service
from .taxonomy_normalizer import normalize_textbook_spec, graph_quality, is_back_matter
from ...prompts.registry import get as get_prompt

logger = logging.getLogger(__name__)

# 每章概念抽取喂给 LLM 的文本上限。 dense 教材一章 20-30 页可达 3 万字符，
# 24000 在 64K 窗口下仍留足输出空间（输入约 2.4 万 token + 输出 8000）。
_CHAPTER_TEXT_CAP = 24000
_CHAPTER_EXCERPT_VERSION = "2"
# 骨架/目录调用喂给 LLM 的文本上限。
_TOC_TEXT_CAP = 8000
# 小教材快速路径阈值（全文 ≤ 此值 → 单次 generate_spec，跳过逐章循环）。
_FAST_PATH_CHARS = cg.MAX_MATERIAL_CHARS  # 20000
# 长教材若仍只能退化为“全书”单章，必须显式标记为需重抽取。这个信号只影响
# 图谱质量状态，不影响已经写入 Library 的全文/chunks/vector。
_LONG_TEXTBOOK_CHARS = 50000
_LONG_TEXTBOOK_PAGES = 40

# per-book 构建锁：同一本书绝不并发构建（队列自动构建 vs 手动重建互斥）。
# 不同书可并行，本数由 textbook_pipeline.build_concurrency() 控制（legacy
# 模式 = 1，即原 per-student 一本接一本语义）。
_BUILD_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()


async def _lock_for(student_id: str, tb_id: str) -> asyncio.Lock:
    async with _LOCKS_GUARD:
        key = (student_id, tb_id)
        lock = _BUILD_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _BUILD_LOCKS[key] = lock
        return lock


# ---------------------------------------------------------------------------
# per-owner build queue（有界并发流水线；legacy 模式退化为严格串行）
#
# 构建锁只在单次构建期间持有：一本书 OCR 转入 ocr_waiting 等重试时锁即释放。
# 此队列保证同一 owner 的自动构建（上传/启动恢复/删卷重建/容量策略重合并）
# 同时最多 build_concurrency 本在构建（默认 2；legacy=1 即历史严格串行）——
# 书 B 的 OCR 可与书 A 的逐章概念抽取重叠（LLM 与 vision 是不同资源池，
# 各自由 textbook_pipeline.llm_gate / ocr_policy 全局限流）。同一本书绝不
# 并行构建（per-book 锁 + 派发去重）。队首批次到达终态
# （ready/partial/graph_failed/failed/ocr_paused/已删除）后腾出名额开建
# 后续教材。OCR 重试轮本身仍由 core/textbook_ocr 的 resume 调度驱动，队列只
# 负责门控。手动刷新（rebuild_graph 三模式）不走队列，仍由 per-book 构建
# 锁互斥。
# ---------------------------------------------------------------------------

_BUILD_QUEUES: dict[str, dict[str, Any]] = {}
#: 队列 worker 轮询非终态记录的间隔（测试可调小）。
QUEUE_POLL_SECONDS = 5.0
#: 停滞看门狗窗口：门控等待中的非终态记录超过该时长无任何写入、无在途
#: 构建（per-book 锁空闲）且无计划内 OCR 重试时，结算 graph_failed 释放
#: 并发名额——防御任何未知路径留下的僵尸 building（运行期的重启收割对
#: 应物）。在途构建（锁被持有）与慢 LLM 调用链不受影响。测试可调小。
BUILD_STALL_SECONDS = 1800.0
_TERMINAL_STATUSES = {"ready", "partial", "graph_failed", "failed", "ocr_paused"}


# build intent 的合法 kwargs（与 run_textbook_build 签名对齐；plan.md §10）。
_BUILD_INTENT_KEYS = ("ocr_parallel", "force_reextract", "use_llm", "skip_ocr",
                      "skip_harvest", "force_full_ocr")


def _persist_build_intent(student_id: str, tb_id: str,
                          build_kwargs: dict[str, Any]) -> None:
    """入队前持久化 build intent（plan.md §11.2）：进程死掉后都知道用户
    最后一次要求做什么。不改变任何书级状态；永不抛出。"""
    try:
        rec = tb_store.find_textbook(student_id, tb_id)
        if rec is None:
            return
        prev = rec.get("build_job") or {}
        try:
            attempt = int(prev.get("attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        intent = {k: bool(v) for k, v in build_kwargs.items()
                  if k in _BUILD_INTENT_KEYS}
        tb_store.set_build_job(
            student_id, tb_id, state="queued", phase="prepare",
            mode=str(build_kwargs.get("_mode") or "auto"),
            attempt=attempt, requested_at=time.time(), intent=intent)
    except Exception:
        return


def _mark_build_running(student_id: str, tb_id: str) -> None:
    """worker 真正取得执行权时置 running + attempt+=1（plan.md §11.2）。"""
    try:
        rec = tb_store.find_textbook(student_id, tb_id)
        if rec is None:
            return
        job = rec.get("build_job") or {}
        try:
            attempt = int(job.get("attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        tb_store.set_build_job(student_id, tb_id, state="running",
                               phase="prepare", attempt=attempt + 1,
                               started_at=time.time(), last_error="")
    except Exception:
        return


def _settle_build_job_terminal(student_id: str, tb_id: str) -> None:
    """队列项结束后的 build_job 终态收口（plan.md §11.2）：
    ready -> ready；graph_failed/failed -> failed(+last_error)；ocr_waiting/
    ocr_paused -> waiting_ocr；其它非终态保持 running 交给 reconcile。"""
    try:
        rec = tb_store.find_textbook(student_id, tb_id)
        if rec is None:
            return
        job = rec.get("build_job") or {}
        if not job or job.get("state") in {"ready", "failed", "cancelled"}:
            return
        status = str(rec.get("status") or "")
        if status == "ready":
            tb_store.set_build_job(student_id, tb_id, state="ready",
                                   phase="finalize", last_error="")
        elif status in ("graph_failed", "failed"):
            tb_store.set_build_job(student_id, tb_id, state="failed",
                                   last_error=str(rec.get("error") or "")[:120])
        elif status in ("ocr_waiting", "ocr_paused"):
            tb_store.set_build_job(student_id, tb_id, state="waiting_ocr")
    except Exception:
        return


async def run_textbook_build(student_id: str, tb_id: str, *,
                             ocr_parallel: bool = False,
                             force_reextract: bool = False,
                             use_llm: bool = True,
                             skip_ocr: bool = False,
                             skip_harvest: bool = False,
                             force_full_ocr: bool = False,
                             auto_retry: bool = False) -> None:
    """按记录 kind 派发一次构建；永不抛出（异常落 graph_failed）。

    与 API 层共用：队列 worker、重试驱动与手动刷新（_safe_build）都经此
    入口，语义唯一。auto_retry=True 表示自动续跑项：记录已删除或已到终态
    时直接跳过（手动刷新不受此限，仍可重建 ready 书）。
    """
    try:
        rec = tb_store.find_textbook(student_id, tb_id) or {}
        if auto_retry and (not rec
                           or str(rec.get("status") or "") in _TERMINAL_STATUSES):
            return
        # 消费终止请求：一次取消只取消一轮构建——结算终态并清除标记。
        # （不能只在入口清标记：队列里排队的构建可能先于用户取消执行。）
        if rec.get("parse_cancel_requested"):
            tb_store.settle_cancelled_parse(student_id, tb_id)
            tb_store.update_textbook(student_id, tb_id, parse_cancel_requested=False)
            tb_store.set_build_job(student_id, tb_id, state="cancelled",
                                   last_error="cancelled")
            return
        # P1-B（plan.md §11.2）：worker 真正取得执行权 -> running + attempt+=1。
        _mark_build_running(student_id, tb_id)
        if rec.get("kind") == "group":
            await build_group_graph(
                student_id, tb_id, _get_llm_cached() if use_llm else None,
                ocr_parallel=ocr_parallel, force_reextract=force_reextract,
                skip_ocr=skip_ocr, skip_harvest=skip_harvest,
                force_full_ocr=force_full_ocr)
        else:
            await build_textbook_graph(
                student_id, tb_id, _get_llm_cached() if use_llm else None,
                ocr_parallel=ocr_parallel, skip_ocr=skip_ocr,
                skip_harvest=skip_harvest, force_full_ocr=force_full_ocr)
    except Exception as exc:
        from ...core.textbook_ocr import TextbookParseCancelled
        logger.warning("textbook build %s/%s crashed: %s", student_id, tb_id, exc)
        try:
            if isinstance(exc, TextbookParseCancelled):
                tb_store.settle_cancelled_parse(student_id, tb_id)
                tb_store.set_build_job(student_id, tb_id, state="cancelled",
                                       last_error="cancelled")
            else:
                tb_store.update_textbook(student_id, tb_id, status="graph_failed",
                                         error="后台构建异常")
                tb_store.set_build_job(student_id, tb_id, state="failed",
                                       last_error="crashed")
        except Exception:
            pass


def _get_llm_cached() -> Any | None:
    from ...core.llm_async import AsyncLLMClient
    try:
        # 客户端侧并发取策略上限（不再构成约束）；实际节流统一由
        # textbook_pipeline.llm_gate() 动态门负责，管理员在线调整即刻生效。
        ceiling = 8
        return AsyncLLMClient(concurrency=ceiling)
    except Exception:
        return None


def enqueue_textbook_build(student_id: str, tb_id: str, **build_kwargs) \
        -> "asyncio.Future[None] | None":
    """入队一次构建并确保 worker 在跑；返回该次构建（含门控等待）的完成
    Future，供手动刷新等待队列执行完毕；无事件循环时返回 None。

    只在事件循环线程内调用（端点/启动 lifespan）。dict/deque 操作在单线程
    事件循环内原子，无需额外锁。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    # P1-B（plan.md §11.2）：入队前持久化 build intent——无论任务来自首次
    # 上传、失败重试、管理员 rebuild 还是 full OCR，进程死掉后都知道用户
    # 最后一次要求做什么。
    _persist_build_intent(student_id, tb_id, build_kwargs)
    queue = _BUILD_QUEUES.setdefault(student_id, {"items": deque(), "worker": None})
    future: asyncio.Future = loop.create_future()
    queue["items"].append({"textbook_id": tb_id, "kwargs": build_kwargs,
                           "future": future})
    worker = queue["worker"]
    if worker is None or worker.done():
        queue["worker"] = loop.create_task(_queue_worker(student_id, queue))
    return future


async def resume_interrupted_textbook_builds() -> int:
    """P1-B 启动恢复（plan.md §11.3）：把 reconcile 置为 queued 的中断 build
    intent 重新入现有 per-owner 队列。

    必须在事件循环内调用（FastAPI lifespan），不能在 import/init 阶段。
    恢复是幂等重执行整个 intent（auto_retry=True：记录已删除/已终态则跳过），
    不是从函数中间续跑。同一 owner 仍由现有队列串行化。返回入队条数。
    """
    resumed = 0
    try:
        items = tb_store.interrupted_build_jobs()
    except Exception:
        return 0
    for sid, tb_id, record in items:
        job = record.get("build_job") or {}
        intent = job.get("intent") if isinstance(job.get("intent"), dict) else {}
        kwargs = {k: bool(v) for k, v in intent.items() if k in _BUILD_INTENT_KEYS}
        try:
            future = enqueue_textbook_build(sid, tb_id, auto_retry=True, **kwargs)
        except Exception:
            future = None
        if future is not None:
            resumed += 1
            logger.info("textbook build resumed after restart: %s/%s (attempt %s)",
                        sid, tb_id, job.get("attempt"))
    return resumed


def _settle_deferred_book_status(student_id: str, tb_id: str, exc_status: str,
                                 warnings: list[str] | None = None) -> None:
    """TextbookOCRDeferred 出口的权威结算：按 ocr_state.volumes 卷级状态
    重写书级 status/progress。

    并行建卷时书级 status 是后写者赢：waiting 卷的 ocr_waiting 结算可能被
    兄弟卷完成结算覆盖回 building（_merge_volume_state 只保护卷级
    ocr_state，不保护书级 status/progress）。若在此处裸 return，记录会定格
    在 building+chapters N/N，重试驱动与前端展示全部失真 → 队列项永久占用
    并发名额（实测卡死形态：抽取概念 3/3 三小时不动）。
    """
    try:
        rec = tb_store.find_textbook(student_id, tb_id)
    except Exception:
        return
    if rec is None:
        return
    volumes = [v for v in ((rec.get("ocr_state") or {}).get("volumes") or {}).values()
               if isinstance(v, dict)]
    waiting = [v for v in volumes if v.get("status") == "waiting"]
    paused = [v for v in volumes if v.get("status") == "paused"]

    def _pages(states: list[dict[str, Any]]) -> dict[str, int]:
        return {"done": sum(len(v.get("successful_pages") or []) for v in states),
                "total": max(1, sum(len(v.get("target_pages") or []) or 1
                                    for v in states))}

    if waiting:
        tb_store.update_textbook(
            student_id, tb_id, status="ocr_waiting",
            progress={"stage": "ocr_waiting", **_pages(waiting)},
            error=str(waiting[0].get("last_error_summary") or "等待多模态 OCR 重试"))
        tb_store.set_build_job(student_id, tb_id, state="waiting_ocr")
        logger.warning("textbook %s/%s OCR deferred (%s) -> ocr_waiting",
                       student_id, tb_id, exc_status)
        return
    if paused:
        tb_store.update_textbook(
            student_id, tb_id, status="ocr_paused",
            progress={"stage": "ocr_paused", **_pages(paused)},
            error=str(paused[0].get("last_error_summary") or "部分页面 OCR 已暂停"))
        tb_store.set_build_job(student_id, tb_id, state="waiting_ocr")
        logger.warning("textbook %s/%s OCR deferred (%s) -> ocr_paused",
                       student_id, tb_id, exc_status)
        return
    # 无 waiting/paused：OCR 轮以 failed（如 PDF 探针失败）或异常状态退出 →
    # 落终态失败释放名额；错误摘要优先取卷级 last_error_summary。
    err = next((str(v.get("last_error_summary") or "") for v in volumes
                if str(v.get("last_error_summary") or "")), "")
    tb_store.update_textbook(
        student_id, tb_id, status="graph_failed",
        error=(err or f"OCR 轮以 {exc_status or 'unknown'} 退出且无待重试页，"
                      "可点击「重建图谱」重试"),
        warnings=list(warnings or []),
        progress={"stage": "merge", "done": 0, "total": 1})
    logger.warning("textbook %s/%s OCR deferred (%s) -> graph_failed: %s",
                   student_id, tb_id, exc_status, err)


async def _run_queued_item(student_id: str, item: dict[str, Any]) -> None:
    """单个队列项的完整生命周期：一次构建 + 到终态的门控等待（含重试驱动）。"""
    tb_id = str(item["textbook_id"])
    future: asyncio.Future | None = item.get("future")
    try:
        await run_textbook_build(student_id, tb_id, **item["kwargs"])
        await _wait_book_terminal(student_id, tb_id)
    except Exception:
        pass  # run_textbook_build 自带异常网；门控自身永不抛
    finally:
        # P1-B：build_job 终态收口（ready/failed/waiting_ocr；plan.md §11.2）。
        _settle_build_job_terminal(student_id, tb_id)
        # Future 必须结算（含异常路径），否则等待方（手动刷新）悬挂。
        if future is not None and not future.done():
            future.set_result(None)


async def _queue_worker(student_id: str, queue: dict[str, Any]) -> None:
    items: deque = queue["items"]
    running: dict[asyncio.Task, str] = {}  # task -> textbook_id（同书去重用）
    try:
        while items or running:
            # 动态读取并发上限：管理员在线调整后对后续派发立即生效。
            limit = textbook_pipeline.build_concurrency()
            while items and len(running) < limit:
                # 同书去重：正在构建的书不重复派发（避免占住名额等 per-book 锁）。
                item = next((it for it in items
                             if str(it["textbook_id"]) not in running.values()), None)
                if item is None:
                    break
                items.remove(item)
                task = asyncio.get_running_loop().create_task(
                    _run_queued_item(student_id, item))
                running[task] = str(item["textbook_id"])
            if not running:
                # 名额为 0 的防御态（策略异常值被钳制后不会发生）；避免忙等。
                await asyncio.sleep(QUEUE_POLL_SECONDS)
                continue
            done, _pending = await asyncio.wait(
                set(running), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                running.pop(task, None)
                try:
                    task.result()
                except Exception:
                    pass  # _run_queued_item 自带异常网
    finally:
        # 先置 None 再检查残留：覆盖「入队方看到 worker 未结束而不再拉起」的
        # 窗口——若此刻队列又非空，自我重启续跑。
        queue["worker"] = None
        if items:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            queue["worker"] = loop.create_task(_queue_worker(student_id, queue))


async def _wait_book_terminal(student_id: str, tb_id: str) -> None:
    """队列内门控 + 重试驱动：当前书到达终态（或被删除）前本队列项不结束
    （不腾出并发名额）。

    ocr_waiting 是「本书尚未构建完」的合法中间态。到点的重试轮就地在本
    项内执行——这是唯一驱动（直连 resume runner 会绕过队列自行重建，与
    per-book 锁组合成不可控的双驱动）。重试是轻量的：
    force_reextract=False（spec 缓存有效即复用，失效条件由 prompt 指纹/文本
    hash 独立保证），重试周期不再被整书 LLM 重抽 gating；重试完成全书 ready
    则就地补 RAG 收尾。无在途等待卷信息的非终态记录走停滞看门狗（见
    BUILD_STALL_SECONDS），不再无限被动轮询。

    重试判定看卷级 waiting 状态而非书级 status：并行建卷时书级 status 后写
    者赢（见 _settle_deferred_book_status），只认书级 ocr_waiting 会漏驱动。
    """
    last_updated: float | None = None
    stalled_since: float | None = None
    while True:
        await asyncio.sleep(QUEUE_POLL_SECONDS)
        try:
            rec = tb_store.find_textbook(student_id, tb_id)
        except Exception:
            return
        if rec is None:
            return  # 已删除/归档
        status = str(rec.get("status") or "")
        if status not in {"building", "ocr_waiting"}:
            return  # 终态：下一本开建
        volumes = ((rec.get("ocr_state") or {}).get("volumes") or {})
        retry_ats = [float(v.get("next_retry_at") or 0)
                     for v in volumes.values()
                     if isinstance(v, dict) and v.get("status") == "waiting"]
        now = time.time()
        updated = float(rec.get("updated_at") or 0.0)
        if retry_ats and now < min(retry_ats):
            stalled_since = None  # 计划内的 OCR 休眠（重试间隔可达小时级）
        elif updated != last_updated:
            stalled_since = None  # 记录有新写入：重置停滞计时
            last_updated = updated
        elif stalled_since is None:
            stalled_since = now
        elif now - stalled_since >= BUILD_STALL_SECONDS:
            if (await _lock_for(student_id, tb_id)).locked():
                # 有在途构建（如手动刷新绕过队列）在推进：慢 LLM 调用链静默
                # 一小时以上是合法的，不误杀。
                stalled_since = None
            else:
                logger.warning(
                    "textbook %s/%s stalled in %s for %.0fs without progress; "
                    "settling graph_failed to release queue slot",
                    student_id, tb_id, status, now - stalled_since)
                try:
                    tb_store.update_textbook(
                        student_id, tb_id, status="graph_failed",
                        error=(f"构建停滞：{int(BUILD_STALL_SECONDS // 60)} 分钟"
                               "无进度，可点击「重建图谱」重试"),
                        progress={"stage": "merge", "done": 0, "total": 1})
                except Exception:
                    pass
                return
        if not retry_ats:
            continue  # 无在途等待卷信息：被动轮询，由停滞看门狗兜底
        if time.time() < min(retry_ats):
            continue
        force_full = any(bool(v.get("force_full"))
                         and v.get("status") in {"ocr", "waiting"}
                         for v in volumes.values() if isinstance(v, dict))
        try:
            await run_textbook_build(student_id, tb_id, ocr_parallel=True,
                                     force_reextract=False,
                                     force_full_ocr=force_full, auto_retry=True)
        except Exception:
            pass  # run_textbook_build 自带异常网
        try:
            from ...core.textbook_ocr import _post_ready_rag
            await _post_ready_rag(student_id, tb_id)
        except Exception:
            pass  # RAG 收尾失败只记 trace 级别，不影响队列推进


# ---------------------------------------------------------------------------
# chapter slicing (3-tier fallback)
# ---------------------------------------------------------------------------

# --- 前置页剔除（封面/扉页/版权/目录/前言不进入图谱正文）-----------------
# 图谱侧按「页」判定；检索侧另有 chunker 的 toc/preface 噪声标记，两者独立。
_COVER_COPYRIGHT_RE = re.compile(
    r"封面|扉页|版权页?|版权所有|ISBN|出版|印刷|审定|总主编|主编[:：]|编写人员|"
    r"责任编辑|美术编辑|网址|下载站|再版|修订本", re.IGNORECASE)
_BOOK_TITLE_HEADER_RE = re.compile(
    r"教科书|教材|课本|义务教育|必修|选择性必修|选修|年级", re.IGNORECASE)
_TOC_PAGE_RE = re.compile(r"目\s*录")
_DOTTED_LEADER_RE = re.compile(r"…+|\.{3,}|····+")
_FRONT_MATTER_MAX_PAGES = 25


def _page_is_front_matter(page_text: str, page_no: int, total: int) -> bool:
    """封面/扉页/版权/目录/前言页判定（章节切片前置页剔除用）。

    保守规则：只对全书前 ≤25 页生效，命中即把该页排除在章节正文外——
    否则版权页/目录文字会被 LLM 概念抽取当成知识点（「人民教育出版社」
    「教育部组织编写」当概念进图谱，实测污染）。正文页不匹配这些特征
    （页眉「普通高中教科书」须与正文同页才不误判，故要求短页或前 3 页）。
    """
    compact = re.sub(r"\s+", "", page_text or "")
    if not compact:
        return True  # 空页/空白占位页：前置页的一部分
    head = compact[:200]
    short = len(compact) < 600
    if page_no > _FRONT_MATTER_MAX_PAGES:
        return False
    if _TOC_PAGE_RE.search(head):
        return True
    if _COVER_COPYRIGHT_RE.search(head) and (short or page_no <= 3):
        return True
    if page_no <= 3 and short and _BOOK_TITLE_HEADER_RE.search(head):
        return True  # 书名页/扉页
    lines = [ln for ln in (page_text or "").splitlines() if ln.strip()]
    ellipsis = sum(1 for ln in lines if _DOTTED_LEADER_RE.search(ln))
    numerics = sum(1 for ln in lines if re.fullmatch(r"\s*[0-9０-９]{1,3}\s*", ln))
    if len(lines) >= 12 and ellipsis >= 3:
        return True  # 目录页（省略号行 + 页码列）
    if ellipsis + numerics >= max(4, len(lines) // 4):
        return True  # 目录特征：密集短行 + 页码/点线
    return False


def _body_start_page(pages: list[str]) -> int:
    """前置页结束位置（0-based）：第一个不再命中前置判定的页。"""
    total = len(pages)
    limit = min(total, max(12, int(total * 0.15)), _FRONT_MATTER_MAX_PAGES)
    i = 0
    while i < limit and _page_is_front_matter(pages[i], i + 1, total):
        i += 1
    return i


def _body_text(text: str) -> str:
    """整书文本 → 剔除前置页后的正文（\f 页切分后重拼接）。"""
    pages = _split_pages(text)
    start = _body_start_page(pages)
    return "\f".join(pages[start:]) if start else text


def _split_pages(text: str) -> list[str]:
    """PyMuPDF/file_parser 页边界 \\f → 页数组。"""
    return text.split("\f")


_CHAPTER_NUMBER = r"(?:[0-9０-９]+|[一二三四五六七八九十百零〇两]+)"
_CHAPTER_TITLE_RE = re.compile(
    rf"第\s*{_CHAPTER_NUMBER}\s*(?:章|回|节|单元)|chapter\s*[0-9０-９]+",
    re.IGNORECASE,
)
# 章名的「纯编号前缀」形态（第N章/单元/课…，可带后续文字）——LLM 给单元名
# 加副标题后全名锚定失败时的兜底锚。
_CHAPTER_PREFIX_ONLY_RE = re.compile(
    rf"第\s*{_CHAPTER_NUMBER}\s*(?:章|回|节|单元|课|讲|篇|部分)|chapter\s*[0-9０-９]+",
    re.IGNORECASE,
)
_HEADING_LINE_RE = re.compile(
    rf"^(?:第\s*{_CHAPTER_NUMBER}\s*(?:章|单元)\s+\S.*|"
    r"chapter\s*[0-9]+\s+\S.*)$",
    re.IGNORECASE,
)
# 独立短行教学标题（语文等教材单元标题独占一行时也无后续文字）：第N单元/课/部分
_BARE_HEADING_LINE_RE = re.compile(
    rf"^第\s*{_CHAPTER_NUMBER}\s*(?:章|单元|课|讲|部分)\s*$",
    re.IGNORECASE,
)

# --- 书签标题质检（类级规则，无书名词表）----------------------------------
# 印刷厂/转换工具产出的伪章节名结构特征：文件名（.pdf/_DJD 下划线）、段号
# （3 位以上数字前缀 + 连字符）、工单标记（"2.23小"）、卷/书名包装（含
# 出版社/教科书/第N版，或归一化后与卷文件名相同）。真实教学标题不含这些。
_OUTLINE_NOISE_RE = re.compile(
    r"封面|扉页|版权页?|书名页|封底|致谢|参考文献|索引|目录", re.IGNORECASE)
_OUTLINE_GARBAGE_RE = re.compile(
    r"_|\.pdf\b|\.djvu\b|出版社|教科书|印刷|第\s*\d+\s*版|"
    r"\d+\.\d{1,2}\s*[大小]|^\d{3,}\s*[-_・·]")
_ASCII_ONLY_TITLE_RE = re.compile(r"^[A-Za-z0-9 _\-./:()（）·&+%'\"’]*$")
_TEACHING_KEYWORD_RE = re.compile(
    r"unit|chapter|part|lesson|section|module|topic|课|章|单元|讲|部分|回",
    re.IGNORECASE,
)


def _strip_title_junk(title: str) -> str:
    """归一化书名前缀/空白（含扩展名剥离），用于“标题≈卷文件名”的等价判定。"""
    t = unicodedata.normalize("NFKC", title or "").casefold()
    t = re.sub(r"\.(pdf|djvu|docx?|pptx?|txt|md)$", "", t)
    return re.sub(r"\s+", "", t)


def _garbage_outline_title(title: str, volume_hint: str = "") -> bool:
    """Tier 1 书签标题质检：True = 该条目不是教学单元（文件名/工单/卷包装）。

    返回 True 的条目要么整条剔除（页码并入邻居），要么整个 Tier 1 弃用
    （比例过高时——印刷厂分段的范围本身也不是教学单元边界）。
    """
    t = (title or "").strip()
    if not t:
        return True
    if _OUTLINE_NOISE_RE.search(t):
        return True
    if _OUTLINE_GARBAGE_RE.search(t):
        return True
    if is_back_matter(t):
        return True
    if (_ASCII_ONLY_TITLE_RE.match(t) and not _TEACHING_KEYWORD_RE.search(t)
            and (re.search(r"\d{2,}", t) or len(t) >= 32)):
        # 无教学关键词的长 ASCII 串（含长数字段）更像路径/编号而非标题；
        # "Sustainable Development" 这类正常英文节标题（无数字、长度适中）保留。
        return True
    if volume_hint:
        stem = _strip_title_junk(volume_hint)
        if stem and _strip_title_junk(t) == stem:
            return True
        if stem and len(stem) >= 4 and _strip_title_junk(t).startswith(stem):
            return True  # "化学反应原理第1章" → 卷书名包装
    return False


def _normalize_chapter_name(name: str, volume_hint: str = "") -> str:
    """章名规范化：剥离卷书名前缀（"化学反应原理第1章"→"第1章"），保底去
    空白。真实教学标题（含长单元名）完整保留——显示层负责可读性。"""
    out = re.sub(r"\s+", " ", (name or "").strip())
    if volume_hint:
        stem = _strip_title_junk(volume_hint)
        core = _strip_title_junk(out)
        if stem and len(stem) >= 2 and core.startswith(stem) and len(core) > len(stem):
            tail = core[len(stem):]
            out = re.sub(r"\s+", " ", tail).strip()
    return out[:80] or "未命名章节"


def _volume_prefix_tail(title: str, volume_hint: str) -> str:
    """卷名包装标题 → 剥离后的真实章名；非包装形态返回 ""。

    "化学反应原理第1章" + 卷名"化学反应原理.pdf" → "第1章"。剥离出的尾巴
    必须是像章节标题的短串，否则不认（防把整书名误判为前缀）。"""
    if not volume_hint:
        return ""
    stem = _strip_title_junk(volume_hint)
    core = _strip_title_junk(title or "")
    if not (stem and len(stem) >= 2 and core.startswith(stem)
            and len(core) > len(stem)):
        return ""
    tail = core[len(stem):]
    if len(tail) < 2:
        return ""
    if not (_CHAPTER_TITLE_RE.search(tail) or len(tail) <= 24):
        return ""
    return re.sub(r"\s+", " ", tail).strip()[:80]


def _compact_with_index(text: str) -> tuple[str, list[int]]:
    """NFKC + 去空白，并保留每个规范化字符对应的原文下标。

    PDF 文本常把普通空格、U+3000、换行和全角数字混用。直接 ``find`` 会让
    LLM 返回的正确目录标题无法定位正文。逐原字符规范化可在命中后安全回到
    原始文本切片，不把规范化副本写入 RAG 或图谱内容。
    """
    chars: list[str] = []
    positions: list[int] = []
    for index, original in enumerate(text):
        normalized = unicodedata.normalize("NFKC", original).casefold()
        for ch in normalized:
            if ch.isspace():
                continue
            chars.append(ch)
            positions.append(index)
    return "".join(chars), positions


def _compact_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title or "").casefold()
    return "".join(ch for ch in normalized if not ch.isspace())


# --- 节级（课/篇目/小节）压缩定位 -------------------------------------------
# 章级定位的 _compact_with_index 只去空白；节标题（篇目名）在目录/正文里的
# 间隔号、编号、页码装饰差异更大（「1 沁园春·长沙」vs「沁园春·长沙」），需要
# 更强的归一化：仅保留字母数字/CJK，并剥离课序前缀。
_WORDISH_RE = re.compile(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_SECTION_NUM_PREFIX_RE = re.compile(
    r"^(?:第[0-9a-z一二三四五六七八九十百零]+(?:课|讲|篇)|[0-9]+)+")


def _wordish(text: str) -> str:
    """NFKC + casefold + 仅保留字母数字/CJK（去空白/标点/间隔号）。

    注音序号①②（NFKC 后成数字 1/2）夹在 CJK 中会被剔除——「我与地坛①
    （节选）」与「我与地坛（节选）」压成同一形式。
    """
    return _wordish_with_index(text)[0]


def _section_anchor_key(name: str) -> str:
    """节标题的定位键：wordish + 剥离「第N课/纯数字」前缀。

    「1 沁园春·长沙」「第1课 沁园春·长沙」「沁园春·长沙」→「沁园春长沙」。
    """
    return _SECTION_NUM_PREFIX_RE.sub("", _wordish(name))


def _wordish_with_index(text: str) -> tuple[str, list[int]]:
    """wordish 压缩副本 + 每个压缩字符对应的原文下标（定位后回原文切页码）。"""
    chars: list[str] = []
    positions: list[int] = []
    for index, original in enumerate(text):
        for ch in unicodedata.normalize("NFKC", original).casefold():
            if ch and not _WORDISH_RE.fullmatch(ch):
                chars.append(ch)
                positions.append(index)
    # 剔除 CJK 环境中的单个数字（正文标题注音序号 ①②③ → NFKC 成 1/2/3，
    # 夹在篇目名中间会打断压缩匹配；两侧同一变换，普通数字标题不受影响）。
    keep: list[int] = []
    for i, ch in enumerate(chars):
        if (ch.isdigit() and 0 < i < len(chars) - 1
                and _is_cjk(chars[i - 1]) and _is_cjk(chars[i + 1])):
            continue
        keep.append(i)
    if len(keep) != len(chars):
        chars = [chars[i] for i in keep]
        positions = [positions[i] for i in keep]
    return "".join(chars), positions


def _is_cjk(ch: str) -> bool:
    return bool(_CJK_CHAR_RE.fullmatch(ch))


def _page_starts(text: str) -> list[int]:
    """\\f 分页下每页起始的原文偏移（0-based），供偏移→1-based 页码换算。"""
    starts = [0]
    for m in re.finditer("\f", text):
        starts.append(m.start() + 1)
    return starts


def _title_occurrences(text_compact: str, index_map: list[int], title: str) -> list[int]:
    needle = _compact_title(title)
    if not needle or not text_compact:
        return []
    out: list[int] = []
    start = 0
    while True:
        found = text_compact.find(needle, start)
        if found < 0:
            break
        original = index_map[found]
        if not out or out[-1] != original:
            out.append(original)
        start = found + max(1, len(needle))
    if out:
        return out
    # 编号前缀兜底：LLM 常给单元名补副标题（「第一单元 青春的价值」），而
    # 正文/目录里单元名只有「第一单元」。全名找不到时用编号前缀锚定。
    prefix = _compact_title(_CHAPTER_PREFIX_ONLY_RE.search(title).group(0)) \
        if _CHAPTER_PREFIX_ONLY_RE.search(title) else ""
    if not prefix or len(prefix) < 3:
        return []
    start = 0
    while True:
        found = text_compact.find(prefix, start)
        if found < 0:
            break
        original = index_map[found]
        if not out or out[-1] != original:
            out.append(original)
        start = found + max(1, len(prefix))
    return out


def _deterministic_chapter_headings(text_head: str) -> list[str]:
    """从教材开头提取短章节标题，作为 LLM/书签失败后的确定性兜底。

    只接受独立短行，避免把前言中“第一章突出了……”一类叙述误当章节；
    去重后至少两个标题才由调用方采用。
    """
    headings: list[str] = []
    seen: set[str] = set()
    for raw_line in text_head.splitlines():
        line = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", raw_line)).strip()
        if not line or len(line) > 90:
            continue
        # 带标题的章节行，或独立短行「第N单元/课/部分」（语文等教材单元标题
        # 独占一行时无后续文字；仍必须是完整短行，防误吞正文叙述）。
        if not (_HEADING_LINE_RE.fullmatch(line)
                or (_BARE_HEADING_LINE_RE.fullmatch(line) and len(line) <= 24)):
            continue
        key = _compact_title(line)
        if key in seen:
            continue
        seen.add(key)
        headings.append(line[:80])
        if len(headings) >= 40:
            break
    return headings


# 目录页课目行：编号（1..99 或 *）+ 篇目（可带「/作者」）+ 页码尾巴。
# 实测语文必修形态：「1　沁园春·长沙/毛泽东　…2」「　红烛/闻一多　…4」。
_TOC_SECTION_LINE_RE = re.compile(
    r"^(\*?)\s*([0-9０-９]{1,2})?[\u3000\s]+(\S[^…]*?)(?:/[^/\s][^…]*)?"
    r"[\u3000\s.·、]*[0-9０-９]{1,4}\s*$")
_TOC_CHAPTER_TAIL_RE = re.compile(r"[\u3000\s.·、]*[0-9０-９]{1,4}\s*$")
_TOC_NOISE_SECTION_RE = re.compile(r"^(?:单元学习任务|学习任务|本章小结|小结|习题|复习)")


def _clean_toc_section_name(title: str) -> str:
    """目录课目标题清理：去星号/页码点线/空白归一、去括注序号（①②）尾部。"""
    name = re.sub(r"^[\*\s]+", "", title or "")
    name = re.sub(r"[\u3000\s.·、…．]+$|^[.·、…．]+", "", name).strip()
    name = re.sub(r"[①②③④⑤⑥⑦⑧⑨⑩]+$", "", name).strip()
    return name[:40]


def _toc_chapter_line(line: str) -> str | None:
    """目录章行 → 干净章名；非章行返回 None。

    两种形态：①「第N单元…页码」（页码尾巴可选）；②纯编号短行「第N单元」
    （语文单元名独占一行、页码在下一课目行的形态）。
    """
    m = _CHAPTER_PREFIX_ONLY_RE.match(line)
    if not m:
        return None
    prefix = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", m.group(0))).strip()
    rest = line[len(m.group(0)):].strip()
    if not rest:
        return prefix if len(prefix) <= 12 else None
    page_stripped = _TOC_CHAPTER_TAIL_RE.sub("", rest).strip()
    if not page_stripped:
        # 「第N单元 + 页码」形态：章名即编号前缀
        return prefix
    # 「第N章 标题 …页码」形态：保留标题
    cleaned_tail = re.sub(r"[\u3000\s.·、…]+", " ", page_stripped).strip()
    name = f"{prefix} {cleaned_tail}"
    return name[:40] or None


def deterministic_toc(text_head: str) -> tuple[list[str], dict[str, list[str]]]:
    """目录页确定性两级解析：([章名], {章名: [节名]})。

    目录页是语文/理科教材里结构最规整的区域：章行「第N单元 … 页码」、
    课目行「编号 篇目/作者 … 页码」。纯规则解析，零 LLM；只有解析出
    ≥2 个章行且至少一个章带课目时才被认为有效（否则调用方走旧兜底）。
    """
    chapters: list[str] = []
    sections_by_name: dict[str, list[str]] = {}
    current: str = ""
    seen_ch: set[str] = set()
    seen_sec: set[str] = set()
    for raw_line in text_head.splitlines():
        line = unicodedata.normalize("NFKC", raw_line or "").strip()
        if not line or len(line) > 90:
            continue
        ch = _toc_chapter_line(line)
        if ch:
            name = re.sub(r"[\u3000\s]+$", "", ch).strip()[:40]
            if name and name not in seen_ch:
                seen_ch.add(name)
                chapters.append(name)
                current = name
                sections_by_name.setdefault(current, [])
            continue
        if not current:
            continue
        sec = _TOC_SECTION_LINE_RE.match(line)
        if not sec:
            continue
        title = _clean_toc_section_name(sec.group(3) or "")
        if (not title or len(title) < 2 or _TOC_NOISE_SECTION_RE.match(title)
                or _OUTLINE_NOISE_RE.search(title) or _garbage_outline_title(title, "")):
            continue
        key = _compact_title(title)
        if not key or key in seen_sec:
            continue
        seen_sec.add(key)
        sections_by_name[current].append(title[:40])
    has_sections = any(v for v in sections_by_name.values())
    if len(chapters) < 2 or not has_sections:
        return [], {}
    return chapters[:40], sections_by_name


def _is_long_textbook(text: str) -> bool:
    return len(text) >= _LONG_TEXTBOOK_CHARS or len(_split_pages(text)) >= _LONG_TEXTBOOK_PAGES


def _chapter_excerpt(text: str, cap: int = _CHAPTER_TEXT_CAP) -> str:
    """在固定上下文预算内覆盖长章节的章首、章中和章末。

    旧逻辑只取 ``chapter_text[:24000]``，会稳定漏掉长章末尾的总结性知识点
    （本次真实样本中的“正定二次型/正定矩阵”即位于第四章后段）。分层抽样
    保持输入预算不增长，同时让概念抽取覆盖整章，而不是只覆盖前半章。
    """
    if len(text) <= cap:
        return text
    marker1 = "\n\n<章节中段节选>\n"
    marker2 = "\n\n<章节末段节选>\n"
    budget = max(3, cap - len(marker1) - len(marker2))
    head_len = budget // 3
    middle_len = budget // 3
    tail_len = budget - head_len - middle_len
    middle_start = max(head_len, (len(text) - middle_len) // 2)
    middle_end = min(len(text) - tail_len, middle_start + middle_len)
    return (text[:head_len] + marker1 + text[middle_start:middle_end]
            + marker2 + text[-tail_len:])


def extract_chapters_pdf(raw: bytes, text: str | None = None,
                         volume_hint: str = "") -> tuple[list[tuple[str, str]], str] | None:
    """Tier 1: PDF 书签目录 → 页码范围切片。失败返回 None。

    fitz.get_toc() 返回 [[level, title, page_1based], ...]。
    - 层级选择：优先「章粒度」——某层 ≥2 条条目匹配 第N章/Chapter N 才选它
      （有的书 level1 是「篇」容器 + 前言，直接选 level1 会把 270 页切成一章）；
      无章粒度层时回退旧逻辑（level1 ≥2 条 → level1，否则 level2，再否则全部）。
    - 标题质检（类级规则）：文件名/印刷工单/卷包装/目录噪声条目剔除；垃圾占比
      过高 → 整个 Tier 1 弃用（印刷厂分段的页码范围也不是教学单元边界），让
      Tier 2 LLM 从正文定位真实单元。
    - 切片文本：``text`` 为 None 时逐页 get_text（旧行为，文本版 PDF）；
      传入时按 \\f 页切它——**扫描版 PDF 因此能用书签目录切 OCR 文本**
      （OCR 合并文本空页占位，页序与物理页一一对应）。超出文本页数范围的
      条目（如 OCR 截断后的页码）跳过/截断。
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        return None
    from ...core.pdf_ocr import FITZ_LOCK  # PyMuPDF 非线程安全：文档操作持锁
    try:
        with FITZ_LOCK:
            doc = fitz.open(stream=raw, filetype="pdf")
            try:
                toc = doc.get_toc()  # [[level, title, page], ...]
                page_count = doc.page_count
            finally:
                doc.close()
    except Exception:
        return None
    if not toc:
        return None

    def entries_at(level: int) -> list[tuple[str, int]]:
        return [(t, p) for (lv, t, p) in toc if lv == level and t and p]

    # 章粒度优先：找 ≥2 条「第N章/Chapter N」条目的层级。
    entries: list[tuple[str, int]] = []
    chapter_level = 0
    for lvl in (1, 2, 3):
        es = entries_at(lvl)
        if sum(1 for t, _p in es if _CHAPTER_TITLE_RE.search(t)) >= 2:
            entries = es
            chapter_level = lvl
            break
    if not entries:
        # 旧逻辑：level1（无则 level2）≥2 条取之，再否则全部条目。
        for target_level in (1, 2):
            entries = entries_at(target_level)
            if len(entries) >= 2:
                chapter_level = target_level
                break
        else:
            entries = [(t, p) for (lv, t, p) in toc if t and p]
    if len(entries) < 2:
        return None

    # 标题质检：噪声条目（目录/封面/版权…）直接剔除；卷名包装条目剥前缀当
    # 真实章名保留（页码范围是真实边界）；其余垃圾条目（文件名/工单）若占比
    # 过半，说明这层书签不是教学单元边界，整个 Tier 1 弃用。
    good: list[tuple[str, int]] = []
    garbage = 0
    for t, p in entries:
        if _OUTLINE_NOISE_RE.search(t or ""):
            continue  # 剔除：页码范围并入相邻真章
        tail = _volume_prefix_tail(t, volume_hint)
        if tail:
            good.append((tail, p))
            continue
        if _garbage_outline_title(t, volume_hint):
            garbage += 1
        else:
            good.append((t, p))
    if not good or len(good) < 2:
        return None
    if garbage >= max(2, len(good)):
        return None  # 垃圾过半：页码范围不可信，交 Tier 2 定位
    entries = good

    if text is None:
        # 旧行为：原文文本层逐页提取。
        try:
            with FITZ_LOCK:
                doc = fitz.open(stream=raw, filetype="pdf")
                try:
                    pages = [doc[i].get_text() for i in range(page_count)]
                finally:
                    doc.close()
        except Exception:
            return None
    else:
        pages = text.split("\f")
    total = len(pages)
    # 节级（v5）：章层级的下一级书签 = 章 内二级条目（课/篇目/小节），
    # 带精确页码。篇目名无「课/章」关键词，只做噪声/垃圾质检，不做教学
    # 关键词要求。
    child_entries: list[tuple[str, int]] = []
    if chapter_level:
        for lv, t, p in toc:
            if lv == chapter_level + 1 and t and p \
                    and not _OUTLINE_NOISE_RE.search(t or "") \
                    and not _garbage_outline_title(t, volume_hint):
                child_entries.append((t, int(p)))
    slices: list[tuple[str, str, tuple[int, int], list[dict[str, Any]]]] = []
    body_start = _body_start_page(pages)  # 前置页（封面/版权/目录）不进图谱正文
    for i, (title, page) in enumerate(entries):
        start = max(body_start, int(page) - 1)
        if start >= total:
            continue  # 超出文本范围（OCR 截断等），跳过
        nxt = int(entries[i + 1][1]) - 1 if i + 1 < len(entries) else total
        end = max(start + 1, min(total, nxt))
        chunk = "\n".join(pages[start:end]).strip()
        if chunk:
            sections = _sections_from_bookmarks(
                child_entries, start, end, volume_hint)
            slices.append((_normalize_chapter_name(title, volume_hint) or f"第{i + 1}章",
                           chunk, (start + 1, end), sections))  # 1-based 页码区间（含端点）
    if len(slices) < 2:
        return None
    toc_text = "\n".join(f"- {t}" for t, _, _, _ in slices)
    return slices, toc_text


def _sections_from_bookmarks(child_entries: list[tuple[str, int]],
                             start0: int, end0: int,
                             volume_hint: str = "") -> list[dict[str, Any]]:
    """把落在 [start0, end0)（0-based 页）内的子书签转成节条目（1-based 区间）。

    节区间 = 该子书签页 → 下一子书签前页（钳制在章区间内）；标题走同一套
    卷名剥离/空白归一。超量截断（防印刷厂分层书签爆量）。
    """
    in_range = [(t, p) for (t, p) in child_entries if start0 <= p - 1 < end0]
    sections: list[dict[str, Any]] = []
    for j, (ct, cp) in enumerate(in_range):
        nxt_cp = int(in_range[j + 1][1]) if j + 1 < len(in_range) else end0 + 1
        sec_end = max(cp, min(nxt_cp - 1, end0))
        name = _normalize_chapter_name(ct, volume_hint)
        if name:
            sections.append({"name": name[:60], "page_range": [cp, sec_end]})
        if len(sections) >= 40:
            break
    return sections


def locate_chapters(text: str, names: list[str],
                    sections_by_name: dict[str, list[str]] | None = None
                    ) -> list[tuple[str, str, None, list[dict[str, Any]]]]:
    """按章节名在全文做确定性定位切片（Tier 2 辅助）。

    找不到锚点的章节并入前一章（避免空章）。返回 [(name, text, None,
    sections), ...]——第三项页码区间 Tier 2 不可得（概念预索引退化为章名
    关键词匹配）；第四项为章内节条目（v5，可带页码区间）。

    目录陷阱（P5a-A3）：章节名的**首次**出现几乎总位于目录页，直接 find 会把
    切片锚到目录区而非正文区。因此优先取每个名字的**第二次**出现（正文标题），
    只出现一次的名字仍取其唯一位置（无目录的小文档不受影响）。
    """
    if not names:
        return []
    # 收集每个名字的锚点位置（第二次出现优先）。匹配在 NFKC/去空白副本上
    # 完成，但切片始终使用 index_map 还原后的原文位置。
    compact, index_map = _compact_with_index(text)
    positions: list[tuple[int, str]] = []
    used_positions: set[int] = set()
    for nm in names:
        nm = (nm or "").strip()
        if not nm:
            continue
        occurrences = _title_occurrences(compact, index_map, nm)
        if not occurrences:
            continue
        chosen = occurrences[1] if len(occurrences) >= 2 else occurrences[0]
        if chosen in used_positions:
            continue
        used_positions.add(chosen)
        positions.append((chosen, nm))
    if not positions:
        return []
    # 前置页剔除：正文起点之前的锚点（目录页里的标题）钳到正文起点。
    pages = _split_pages(text)
    body_start = _body_start_page(pages)
    body_offset = sum(len(p) + 1 for p in pages[:body_start])  # \f 分隔符占 1
    page_starts = _page_starts(text)
    positions.sort()
    # 去掉位置倒退/重复的（目录后正文再次出现同名的情形取首次）。
    slices: list[tuple[str, str, None, list[dict[str, Any]]]] = []
    for i, (idx, nm) in enumerate(positions):
        start = max(body_offset, idx)
        nxt = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        end = max(start, nxt)
        chunk = text[start:end].strip()
        if chunk:
            lead = len(chunk) - len(chunk.lstrip())
            sections = locate_sections(
                chunk, start + lead,
                (sections_by_name or {}).get(nm) or [],
                page_starts) if sections_by_name else []
            slices.append((nm[:80], chunk, None, sections))
    return slices


def locate_sections(slice_text: str, slice_start_offset: int,
                    section_names: list[str],
                    page_starts: list[int]) -> list[dict[str, Any]]:
    """在章切片内定位节（课/篇目/小节）锚点，输出节条目（含页码区间）。

    压缩匹配（wordish：去空白/标点/间隔号 + 剥课序前缀），使「1 沁园春·长沙」
    能锚到正文「沁园春·长沙」。优先取**行首**命中（正文标题独占一行），
    退而取首次出现。返回按出现顺序排列；``_span`` 是切片内字符区间，
    供构建期概念挂节使用（消费方落盘前剔除）。找不到的节名直接丢弃。
    """
    if not section_names or not slice_text.strip():
        return []
    compact, positions = _wordish_with_index(slice_text)
    if not compact:
        return []
    lines: list[tuple[int, str]] = []   # (原文下标, 节名)
    for nm in section_names:
        nm = (nm or "").strip()
        needle = _section_anchor_key(nm)
        if len(needle) < 2:
            continue
        cursor = 0
        heading_hit = -1
        first_hit = -1
        while cursor < len(compact):
            found = compact.find(needle, cursor)
            if found < 0:
                break
            original = positions[found]
            if _at_section_heading_position(slice_text, original):
                heading_hit = original
                break  # 标题行命中（允许行内编号前缀）优先
            if first_hit < 0:
                first_hit = original
            cursor = found + 1
        fallback = heading_hit if heading_hit >= 0 else first_hit
        if fallback >= 0:
            lines.append((fallback, nm))
    if not lines:
        return []
    lines.sort()
    out: list[dict[str, Any]] = []
    for j, (off, nm) in enumerate(lines):
        nxt = lines[j + 1][0] if j + 1 < len(lines) else len(slice_text)
        start_page = bisect_right(page_starts, slice_start_offset + off)
        end_pos = nxt - 1
        while end_pos > off and slice_text[end_pos] in "\f\n\r \t":
            end_pos -= 1
        end_page = bisect_right(page_starts, slice_start_offset + end_pos)
        out.append({"name": nm, "page_range": [start_page, max(start_page, end_page)],
                    "_span": (off, max(off + 1, nxt))})
        if len(out) >= 40:
            break
    return out


def _at_section_heading_position(slice_text: str, offset: int) -> bool:
    """标题位置判定：命中点位于行首，或行内前缀**仅是课序编号**。

    正文标题常见形态「1 沁园春·长沙」「第1课 沁园春·长沙」——命中点前有
    编号但同属标题行；而「单元导语：本单元学习《沁园春·长沙》……」的中行
    提及前缀是正文文字，不算标题位。"""
    line_start = max(slice_text.rfind("\n", 0, offset),
                     slice_text.rfind("\f", 0, offset)) + 1
    prefix = _wordish(slice_text[line_start:offset])
    if not prefix:
        return True
    return bool(prefix.isdigit()
                or re.fullmatch(r"(?:第[0-9a-z一二三四五六七八九十百零]+[课讲篇])+", prefix))


def whole_book_chapter(text: str) -> list[tuple[str, str, None, list[dict[str, Any]]]]:
    """Tier 3: 整书单章快速路径。统一短名「全册」（跨学段/学科显示一致）。

    只取剔除前置页（封面/扉页/版权/目录）后的正文——否则版权页/目录文字
    会被概念抽取当成知识点进图谱。"""
    body = _body_text(text)
    return [("全册", body, None, [])] if body.strip() else []


async def _llm_extract_toc(text_head: str, llm: Any) -> list[dict[str, Any]]:
    """Tier 2: extract clean teaching headings (chapters + sections) from the
    opening pages. 返回 [{"name": 章名, "sections": [节名, ...]}, ...]；旧版
    纯章名数组（prompt 2.1 输出）按无节兼容解析。"""
    prompt = get_prompt("textbook_toc_extract").text.format(text=text_head)
    try:
        async with textbook_pipeline.llm_gate():
            raw, _u = await llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=2000, disable_thinking=True)
    except Exception:
        return []
    try:
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        data = json.loads(m.group(0) if m else raw)
        chapters = data.get("chapters", []) if isinstance(data, dict) else []
        out: list[dict[str, Any]] = []
        for ch in chapters[:40]:
            if isinstance(ch, str):
                name = ch.strip()
                if name:
                    out.append({"name": name, "sections": []})
            elif isinstance(ch, dict):
                name = str(ch.get("name") or "").strip()
                if not name:
                    continue
                secs = [str(s).strip() for s in (ch.get("sections") or [])
                        if str(s).strip()]
                out.append({"name": name, "sections": secs[:40]})
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# DS 图谱设计阶段（P7.7.4 W9b）：标签统一 / 同义归并 / 跨章继承
# ---------------------------------------------------------------------------

async def _graph_design_pass(spec: dict[str, Any], llm: Any | None,
                             topic_hint: str) -> dict[str, Any]:
    """合并 spec 后由主 LLM 做一次图谱设计（纯增强，失败零影响）。

    输出三类建议：chapter_labels（统一标题/剔除目录封面伪章）、
    concept_merges（完全同义概念归并）、cross_prereq（跨章前置依赖）。
    本地侧只按名应用：名称解析与 DAG 环守卫由 spec_to_graph 兜底，坏建议
    最多被忽略，不可能污染图谱。开关 GRAPH_DESIGN_MODE=0 时直接跳过。
    """
    from ...core.config import settings as _s
    if llm is None or not _s.graph_design_mode:
        return {}
    chapters = [c for c in (spec.get("chapters") or []) if isinstance(c, dict)]
    if not chapters:
        return {}
    lines = []
    for i, ch in enumerate(chapters):
        concepts = [str(c.get("name") or "").strip()[:30]
                    for c in (ch.get("concepts") or []) if c.get("name")]
        lines.append(f"[{i}] {str(ch.get('name') or '').strip()[:60]}")
        if concepts:
            lines.append("    概念：" + "、".join(concepts[:40]))
    prompt = get_prompt("textbook_graph_design").text.format(
        topic=str(topic_hint or "")[:60],
        subject=str(spec.get("subject") or "")[:30],
        level=str(spec.get("level") or "")[:20],
        chapters="\n".join(lines)[:12000])
    try:
        async with textbook_pipeline.llm_gate():
            raw, _u = await llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=4000, disable_thinking=True)
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        data = json.loads(m.group(0) if m else raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _apply_graph_design(spec: dict[str, Any], design: dict[str, Any]) -> list[str]:
    """把设计建议落到 spec 上（按名应用，空名章剔除，全部本地校验）。"""
    if not design:
        return []
    applied: list[str] = []
    chapters = [c for c in (spec.get("chapters") or []) if isinstance(c, dict)]
    remove_idx: list[int] = []
    for fix in design.get("chapter_labels") or []:
        try:
            i = int(fix.get("index", -1))
        except (TypeError, ValueError):
            continue
        if not (0 <= i < len(chapters)):
            continue
        name = str(fix.get("name") or "").strip()
        if not name:
            remove_idx.append(i)  # 伪章（目录/封面/版权内容）：整章剔除
            applied.append(f"剔除伪章[{i}]")
        else:
            chapters[i]["name"] = name[:80]
            applied.append(f"章[{i}]→{name[:24]}")
    for i in sorted(set(remove_idx), reverse=True):
        chapters.pop(i)
    if remove_idx:
        spec["chapters"] = chapters  # 列表替换回 spec（剔除伪章）
    merges: dict[str, str] = {}
    for m in design.get("concept_merges") or []:
        src = str(m.get("name") or "").strip()
        dst = str(m.get("into") or "").strip()
        if src and dst and src != dst:
            merges[src] = dst
    if merges:
        for ch in chapters:
            for c in ch.get("concepts") or []:
                cname = str(c.get("name") or "").strip()
                if cname in merges:
                    c["name"] = merges[cname]
        applied.append(f"概念归并 {len(merges)} 组")
    prereqs = [(str(e.get("from") or "").strip(), str(e.get("to") or "").strip())
               for e in design.get("cross_prereq") or []]
    prereqs = [(f, t) for f, t in prereqs if f and t and f != t]
    if prereqs:
        for ch in chapters:
            for c in ch.get("concepts") or []:
                to = str(c.get("name") or "").strip()
                for frm, dst in prereqs:
                    if dst == to:
                        pre = [str(p).strip() for p in (c.get("prerequisites") or [])]
                        if frm not in pre:
                            pre.append(frm)
                            c["prerequisites"] = pre
        applied.append(f"跨章前置 {len(prereqs)} 条")
    return applied


async def extract_chapters(text: str, raw: bytes | None, llm: Any | None,
                           volume_hint: str = "") \
        -> tuple[list[tuple[str, str, tuple[int, int] | None, list[dict[str, Any]]]], str]:
    """三级回退章节切片，返回 (slices, toc_text)。

    slices 元素为 (章名, 章文本, 页码区间|None, 节条目)——Tier 1（书签目录）
    带 1-based 章页码区间与子书签节条目，供概念级 RAG 预索引（P6-C2）限定
    检索域；Tier 2（LLM 目录 + locate_chapters）节条目经压缩定位可得页码，
    章级区间为 None；Tier 3 为整书单章（无节）。

    1. PDF 内嵌目录（extract_chapters_pdf，含标题质检与卷名剥离）
    2. LLM 目录提取（_llm_extract_toc + locate_chapters），需 llm
    3. 整书单章（whole_book_chapter）
    """
    if raw:
        pdf = extract_chapters_pdf(raw, text, volume_hint=volume_hint)
        if pdf is not None:
            return pdf
    text_head = text[:_TOC_TEXT_CAP]
    if llm is not None and len(text) > 2000:
        entries = await _llm_extract_toc(text_head, llm)
        if entries:
            names = [e["name"] for e in entries]
            sections_by_name = {e["name"]: e.get("sections") or [] for e in entries}
            slices = locate_chapters(text, names, sections_by_name=sections_by_name)
            if len(slices) >= 2:
                toc_text = "\n".join(f"- {t}" for t, _, _, _ in slices)
                return slices, toc_text
    # LLM 可能暂时失败，也可能返回与 PDF 空白/全角格式不一致的标题。确定性
    # 两级目录解析（章行「第N单元…页码」+ 课目行「编号 篇目/作者…页码」）
    # 是无网络的结构化兜底，优先生效；旧短行兜底仍保留为最后一级。
    toc_chapters, toc_sections = deterministic_toc(text_head)
    if len(toc_chapters) >= 2:
        slices = locate_chapters(text, toc_chapters,
                                 sections_by_name=toc_sections)
        if len(slices) >= 2:
            toc_text = "\n".join(f"- {t}" for t, _, _, _ in slices)
            return slices, toc_text
    deterministic = _deterministic_chapter_headings(text_head)
    if len(deterministic) >= 2:
        slices = locate_chapters(text, deterministic)
        if len(slices) >= 2:
            toc_text = "\n".join(f"- {t}" for t, _, _, _ in slices)
            return slices, toc_text
    slices = whole_book_chapter(text)
    return slices, "- 全书"


# ---------------------------------------------------------------------------
# skeleton + per-chapter LLM calls
# ---------------------------------------------------------------------------

def _skeleton_prompt(toc_text: str, filename_hint: str) -> str:
    return get_prompt("textbook_skeleton").text.format(
        toc_text=toc_text, filename_hint=filename_hint)


async def _skeleton_call(toc_text: str, filename_hint: str, llm: Any) -> dict[str, str]:
    """单次 LLM 调用推断 subject/level；失败时不把文件名写入 taxonomy。"""
    fallback = {"subject": "", "level": ""}
    try:
        async with textbook_pipeline.llm_gate():
            raw, _u = await llm.complete(
                [{"role": "user", "content": _skeleton_prompt(toc_text, filename_hint)}],
                temperature=0.0, max_tokens=2000, disable_thinking=True)
    except Exception:
        return fallback
    try:
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        data = json.loads(m.group(0) if m else raw)
        if isinstance(data, dict):
            return {
                "subject": str(data.get("subject") or "").strip()[:30] or fallback["subject"],
                "level": str(data.get("level") or "").strip(),
            }
    except Exception:
        pass
    return fallback


_CHAPTER_PROMPT = get_prompt("textbook_chapter_concepts").text


def _attach_concepts_to_sections(concepts: list[dict[str, Any]],
                                 chapter_text: str,
                                 sections: list[dict[str, Any]]) -> None:
    """确定性概念挂节（零 LLM）：概念名/别名压缩出现在唯一节的文本区间内
    → ``c["section"] = 节名``；多节命中或无节区间 → 保持章级归属。

    匹配用 wordish 压缩（去空白/标点/间隔号），OCR 的标点差异不再漏配。
    """
    spans = [(s.get("_span"), str(s.get("name") or ""))
             for s in sections if s.get("_span") and str(s.get("name") or "").strip()]
    if not spans:
        return
    span_text = {name: _wordish(chapter_text[a:b]) for (a, b), name in spans}
    for c in concepts:
        terms = [str(c.get("name") or "")] + [
            str(a) for a in (c.get("aliases") or [])]
        needles = [t for t in (_wordish(x) for x in terms) if len(t) >= 2]
        if not needles:
            continue
        hits = {name for name, text in span_text.items()
                if any(n in text for n in needles)}
        if len(hits) == 1:
            c["section"] = next(iter(hits))


async def _extract_chapter_concepts(chapter: str, chapter_text: str,
                                     subject: str, level: str, llm: Any) -> list[dict[str, Any]]:
    """单章概念抽取。失败返回 []。"""
    prompt = _CHAPTER_PROMPT.format(
        subject=subject or "未指定", level=level or "未指定",
        chapter=chapter[:80],
        text=_chapter_excerpt(chapter_text))
    try:
        async with textbook_pipeline.llm_gate():
            raw, _u = await llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=8000, disable_thinking=True)
    except Exception:
        return []
    try:
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        data = json.loads(m.group(0) if m else raw)
        concepts = data.get("concepts", []) if isinstance(data, dict) else []
        out = []
        for c in concepts:
            if not (isinstance(c, dict) and str(c.get("name") or "").strip()):
                continue
            # 概念名长度防御（跨学段/学科显示一致）：>12 字截断到词边界。
            name = re.sub(r"\s+", " ", str(c["name"]).strip())
            if len(name) > _MAX_CONCEPT_NAME_CHARS:
                name = name[:_MAX_CONCEPT_NAME_CHARS].rstrip("，。；、的与和或")
            c["name"] = name
            out.append(c)
        return out[:40]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------

def _record_alive(student_id: str, tb_id: str) -> bool:
    """删除/终止守卫：记录被归档删除或用户请求终止后，构建任务静默中止——
    绝不把已删除 topic_key 的图谱/概念索引写回磁盘（孤儿复活竞态）；终止时
    就地结算终态（幂等），队列随即放行下一本。"""
    try:
        rec = tb_store.find_textbook(student_id, tb_id)
        if rec is None:
            return False
        if rec.get("parse_cancel_requested"):
            try:
                tb_store.settle_cancelled_parse(student_id, tb_id)
            except Exception:
                pass
            return False
        return True
    except Exception:
        return True  # 存储读取失败不阻断构建（写路径自有原子写保护）


def _read_library_text(student_id: str, file_id: str) -> tuple[str, bytes | None, str]:
    """Read a library file's extracted text + original binary (for TOC/OCR) + ext."""
    from ...core.library import load_library, library_data_dir
    lib = load_library(student_id)
    meta = lib.find_file(file_id)
    data = library_data_dir(student_id)
    txt_path = data / f"{file_id}.txt"
    text = txt_path.read_text(encoding="utf-8") if txt_path.exists() else ""
    raw: bytes | None = None
    orig_ext = ""
    if meta is not None:
        orig_ext = meta.get("orig_ext") or ""
        if orig_ext:
            orig_path = data / f"{file_id}.orig{orig_ext}"
            if orig_path.exists():
                raw = orig_path.read_bytes()
    return text, raw, orig_ext


async def _ocr_scanned_pdf(student_id: str, tb: dict[str, Any],
                            raw: bytes | None, warnings: list[str],
                            *, current_text: str = "",
                            ocr_budget: int | None = None,
                            ocr_parallel: bool = False) -> str:
    """PDF 逐页择优 OCR（P5a），合并文本写回 library 的 ``.txt``，返回合并文本。

    触发由调用方判定（mode=on 强制 / auto 且存在稀疏页）。本函数内部：
      - mode=on：整本逐页 OCR（``ocr_pdf_pages``，cap 总页数）。
      - mode=auto：逐页择优（``ocr_pdf_pages_mixed``）——稀疏判定基于
        ``current_text``（当前 .txt，含既往 OCR 成果）而非原始文本层：
        已 OCR 变稠密的页不再重复 OCR（扫描书 rebuild 因此零重 OCR）。
    视觉模型优先（复用 MULTIMODAL_*），tesseract 回退。每页更新 progress.stage="ocr"。
    合并结果用 ``\\f`` 按页拼接且**空页以 "" 占位**（A1：页序与物理页一一对应，
    chunk_text 的页码引用不漂移）。写回走原子写（A5）并同步 library 元数据
    （char_count/chunk_count/chunks）。失败/部分成功落 warning；返回 "" 表示
    OCR 无果（调用方按原文本层或 failed 处理）。
    """
    from ...core import pdf_ocr
    from ...core.config import settings as _s
    if _s.pdf_ocr_mode == "off" or not raw:
        return ""
    tb_store.update_textbook(student_id, tb["id"],
                             progress={"stage": "ocr", "done": 0, "total": 1})

    def _progress(done: int, total: int) -> None:
        tb_store.update_textbook(student_id, tb["id"],
                                 progress={"stage": "ocr", "done": done,
                                           "total": max(1, total)})

    from ...core import ocr as _ocr
    cap = ocr_budget or _s.pdf_ocr_max_pages
    # 并行开关有效值由调用方（用户偏好）决定；批次大小取自实例配置。
    concurrency = _s.pdf_ocr_concurrency if ocr_parallel else 1
    try:
        if _s.pdf_ocr_mode == "on":
            pages = await pdf_ocr.ocr_pdf_pages(
                raw, _ocr.ocr_page_image,
                max_pages=cap, dpi=_s.pdf_ocr_dpi,
                on_progress=_progress, concurrency=concurrency,
                global_limit=ocr_parallel)
            stats = {"sparse": len(pages), "ocr_done": len(pages),
                     "ocr_failed": sum(1 for p in pages if not p.strip())}
            truncated = len(pages) >= cap
        else:
            # 稀疏判定基于当前 .txt（含既往 OCR 成果）；为空则退回原始文本层。
            pt = current_text.split("\f") if current_text.strip() else None
            pages, stats = await pdf_ocr.ocr_pdf_pages_mixed(
                raw, _ocr.ocr_page_image, page_texts=pt,
                max_ocr_pages=cap, dpi=_s.pdf_ocr_dpi,
                on_progress=_progress, concurrency=concurrency,
                global_limit=ocr_parallel)
            truncated = stats["sparse"] > stats["ocr_done"]
    except Exception as e:
        warnings.append(f"OCR 失败：{e}")
        return ""
    text = "\f".join(pages)  # A1：空页占位保留，页码不漂移
    if not text.strip():
        warnings.append("OCR 未识别到任何文本")
        return ""
    if stats["ocr_failed"]:
        warnings.append(f"OCR 有 {stats['ocr_failed']}/{len(pages)} 页无文本（保留原文本层/空页占位）")
    if truncated:
        warnings.append(f"OCR 达到页数上限 {cap}，已截断")
    # 写回 library 的 .txt（原子写）+ 同步元数据：后续切块/RAG/图谱全部复用既有路径。
    try:
        from ...core.atomic import atomic_write_text
        from ...core.library import load_library, save_library, library_data_dir
        from ...core.retriever import chunk_text
        data = library_data_dir(student_id)
        atomic_write_text(data / f"{tb['file_id']}.txt", text)
        lib = load_library(student_id)
        meta = lib.find_file(tb["file_id"])
        if meta is not None:
            chunks = chunk_text(text, source=meta.get("filename", ""),
                                file_id=tb["file_id"])
            meta["char_count"] = len(text)
            meta["chunk_count"] = len(chunks)
            lib.chunks_by_file[tb["file_id"]] = chunks
            save_library(lib)
    except Exception as e:
        warnings.append(f"OCR 文本写回失败：{e}")
    # 覆盖追踪（P5a）：累计已 OCR 页数；若稀疏页已全部尝试（含空页占位），
    # 视为当前上限内覆盖完成——rebuild 不再重复 OCR，调高上限后才续扩。
    try:
        covered = int(tb.get("ocr_pages") or 0) + stats["ocr_done"]
        if stats["ocr_done"] >= stats["sparse"]:
            covered = max(covered, min(len(pages), _s.pdf_ocr_max_pages))
        tb_store.update_textbook(student_id, tb["id"], ocr_pages=covered)
    except Exception:
        pass
    return text


async def build_textbook_graph(student_id: str, textbook_id: str,
                                llm: Any | None = None,
                                *, ocr_parallel: bool = False,
                                skip_ocr: bool = False,
                                skip_harvest: bool = False,
                                force_full_ocr: bool = False) -> None:
    """Background task: 教材 → 章节切片 → 逐章概念抽取 → 确定性合并 → M5.7 store。

    永不抛出：任何异常落记录 status=graph_failed/failed。图谱开关关闭时直接
    ready（chapter_count=0，教材仍可检索）。
    ``ocr_parallel``：扫描页 OCR 是否按 PDF_OCR_CONCURRENCY 批并发（调用方按
    触发用户的 prefs.ocr_parallel / 实例默认解析）。
    """
    if not settings.textbook_graph_enabled:
        # 开关关闭：跳过图谱构建，直接 ready（教材仍可检索）。
        tb_store.update_textbook(student_id, textbook_id,
                                 status="ready", chapter_count=0, concept_count=0,
                                 progress={"stage": "merge", "done": 1, "total": 1})
        return

    tb = tb_store.find_textbook(student_id, textbook_id)
    if tb is None:
        return
    # per-book 构建锁：同一本书绝不并发构建（不同书可并行，见队列契约）。
    lock = await _lock_for(student_id, textbook_id)
    async with lock:
        await _build_inner(student_id, tb, llm, ocr_parallel=ocr_parallel,
                           skip_ocr=skip_ocr, skip_harvest=skip_harvest,
                           force_full_ocr=force_full_ocr)


async def _volume_spec(student_id: str, rec_id: str, file_id: str, title: str,
                       level: str, llm: Any | None, warnings: list[str],
                       *, ocr_parallel: bool = False, skip_ocr: bool = False,
                       skip_harvest: bool = False,
                       force_full_ocr: bool = False
                       ) -> tuple[str, dict[str, Any] | None]:
    """单卷「读文本 → OCR 判定/写回 → fast/full path spec」，返回 (text, spec)。

    进度更新落到 ``rec_id``（单教材=教材记录，教材组=组记录）；OCR 写回与
    chunk 重建按卷 ``file_id``。文本为空返回 ("", None)；spec 失败返回
    (text, None)——状态流转由调用方决定（单教材落 failed/graph_failed，
    教材组跳过该卷并 warning）。
    """
    vol = {"id": rec_id, "file_id": file_id, "title": title, "level": level}
    from ...core.textbook_ocr import TextbookParseCancelled
    if tb_store.parse_cancelled(student_id, rec_id):
        raise TextbookParseCancelled(f"{file_id} 构建前终止")
    text, raw, orig_ext = _read_library_text(student_id, file_id)
    ocr_branch_used = False
    # OCR 触发（P5a-A2 逐页判定 + 覆盖推导）：mode=on 强制整本；auto 时——
    # 文本层为空（首次构建）或存在稀疏页且覆盖未达配置上限才触发。
    # 覆盖 = 当前文本的稠密页数（既往 OCR 成果天然计入，不依赖计数器，
    # 旧记录零迁移）；调高 PDF_OCR_MAX_PAGES 后 rebuild 自动续扩覆盖。
    if (not skip_ocr and raw and orig_ext == ".pdf"
            and settings.pdf_ocr_mode != "off"):
        rec = tb_store.find_textbook(student_id, rec_id) or {}
        prior = (((rec.get("ocr_state") or {}).get("volumes") or {}).get(file_id) or {})
        resume_full = bool(prior.get("force_full")) and prior.get("status") in {"ocr", "waiting"}
        force_full_ocr = bool(force_full_ocr or resume_full)
        page_texts = text.split("\f")
        needs = pdf_ocr.pages_needing_ocr(page_texts)
        cap = settings.pdf_ocr_max_pages
        intended = min(len(page_texts), cap) if text.strip() else cap
        covered = len(page_texts) - len(needs)
        budget = max(0, intended - covered)
        if force_full_ocr or settings.pdf_ocr_mode == "on" or not text.strip() or (needs and budget > 0):
            ocr_branch_used = True
            from ...core.textbook_ocr import (TextbookOCRDeferred,
                                               process_textbook_ocr_round)
            result = await process_textbook_ocr_round(
                student_id, rec_id, file_id, raw, text,
                force_full=force_full_ocr or settings.pdf_ocr_mode == "on")
            if result.text.strip():
                text = result.text
            if result.status == "cancelled":
                from ...core.textbook_ocr import TextbookParseCancelled
                raise TextbookParseCancelled(f"{file_id} OCR 已终止")
            if result.status != "complete":
                raise TextbookOCRDeferred(result.status)
    if not text.strip():
        return "", None

    # 原生 PDF（文本层路径）图表/印刷页码收割：OCR 路径的 [图]/[表]/[页码]
    # 标记由 prompt v2 直接产出；这里只给未走 OCR 的文本层书补齐表格结构、
    # 插图图述与 PDF page label 印刷页码，并入 .txt 事实源（图谱 spec 仍用
    # 纯正文，避免标记噪声进入 TOC/骨架抽取）。无收获时文本不变（hash 稳定）。
    base_text = text
    if (raw and orig_ext == ".pdf" and settings.rag_figure_harvest
            and not ocr_branch_used and not skip_harvest):
        try:
            from ...core.figure_harvest import (harvest_native_blocks,
                                                merge_harvest_into_text)
            harvested = await harvest_native_blocks(raw)
            merged = merge_harvest_into_text(text, harvested)
            if merged != text:
                if tb_store.find_textbook(student_id, rec_id) is not None:
                    from ...core.atomic import atomic_write_text
                    from ...core.library import library_data_dir
                    atomic_write_text(
                        library_data_dir(student_id) / f"{file_id}.txt", merged)
                    text = merged
        except Exception as exc:
            warnings.append(f"图表收割跳过：{str(exc)[:120]}")

    filename_hint = title or "教材"
    # 快速路径：小教材单次 generate_spec（成本最优）。
    if len(text) <= _FAST_PATH_CHARS:
        tb_store.update_textbook(student_id, rec_id,
                                 progress={"stage": "skeleton", "done": 0, "total": 1})
        spec = await _fast_path_spec(base_text, filename_hint, llm)
    else:
        spec = await _full_path_spec(student_id, rec_id, base_text, raw,
                                     filename_hint, llm, warnings)
    return text, spec


async def _build_inner(student_id: str, tb: dict[str, Any], llm: Any | None,
                       *, ocr_parallel: bool = False, skip_ocr: bool = False,
                       skip_harvest: bool = False,
                       force_full_ocr: bool = False) -> None:
    warnings: list[str] = []
    try:
        tb_store.update_textbook(student_id, tb["id"], status="building",
                                 progress={"stage": "index", "done": 0, "total": 1})
        text, spec = await _volume_spec(
            student_id, tb["id"], tb["file_id"], tb.get("title") or "教材",
            str(tb.get("level") or ""), llm, warnings, ocr_parallel=ocr_parallel,
            skip_ocr=skip_ocr, skip_harvest=skip_harvest,
            force_full_ocr=force_full_ocr)
        if not text.strip():
            tb_store.update_textbook(student_id, tb["id"], status="failed",
                                     error="教材文本为空且 OCR 无结果，无法构建",
                                     warnings=warnings,
                                     progress={"stage": "parse", "done": 0, "total": 1})
            return
        if spec is not None:
            from ...core.library import load_library
            meta = load_library(student_id).find_file(str(tb.get("file_id") or "")) or {}
            volume_title = str(meta.get("filename") or tb.get("title") or "")
            spec, norm_warnings = normalize_textbook_spec(
                spec, textbook_title=str(tb.get("title") or ""),
                volume_id=str(tb.get("file_id") or ""),
                volume_title=volume_title)
            spec["volumes"] = [{
                "file_id": str(tb.get("file_id") or ""),
                "volume_id": str(tb.get("file_id") or ""),
                "title": volume_title,
            }]
            warnings.extend(norm_warnings)
        if spec is None or not spec.get("chapters"):
            tb_store.update_textbook(student_id, tb["id"], status="graph_failed",
                                     error="LLM 未产出有效概念",
                                     warnings=warnings,
                                     progress={"stage": "merge", "done": 0, "total": 1})
            return

        subject = str(spec.get("subject") or "").strip()[:30]
        # 学段：用户上传时的选择优先；未选（遗留记录）才用骨架推断值。
        chosen_level = str(tb.get("level") or "").strip()
        level = chosen_level or str(spec.get("level") or "").strip()
        topic_key = tb["topic_key"]
        tb_store.update_textbook(student_id, tb["id"], subject=subject, level=level,
                                 progress={"stage": "merge", "done": 0, "total": 1})

        # DS 图谱设计阶段：标签统一/同义归并/跨章继承（失败自动降级）。
        if not _record_alive(student_id, tb["id"]):
            return
        try:
            design = await _graph_design_pass(spec, llm, tb.get("title") or "")
            applied = _apply_graph_design(spec, design)
            if applied:
                warnings.append("图谱设计：" + "；".join(applied))
        except Exception as exc:
            warnings.append(f"图谱设计跳过：{str(exc)[:120]}")

        # 确定性合并：spec_to_graph（DAG 守卫/锚定/上限，level 仅当合法学段生效）。
        base = get_knowledge_service().graph
        data, merge_warnings = cg.spec_to_graph(
            spec, topic_key=topic_key, source=f"textbook:{tb['file_id']}",
            base_graph=base,
            max_chapters=settings.textbook_graph_max_chapters,
            max_concepts=settings.textbook_graph_max_concepts,
            level=level)
        warnings.extend(merge_warnings)
        if not data["concept_count"]:
            tb_store.update_textbook(student_id, tb["id"], status="graph_failed",
                                     error="合并后概念数为 0",
                                     warnings=warnings,
                                     progress={"stage": "merge", "done": 1, "total": 1})
            return

        # 写入 M5.7 store：质量门通过后原子替换。重建不再产生隐藏历史归档。
        # 删除守卫：长 OCR/LLM 途中记录被删则中止，不复活孤儿图谱。
        if not _record_alive(student_id, tb["id"]):
            return
        payload = _build_payload(tb, spec, data, subject, level)
        quality = graph_quality(payload, textbook_title=str(tb.get("title") or ""))
        payload["quality"] = quality
        if not quality["ok"]:
            tb_store.update_textbook(student_id, tb["id"], status="graph_failed",
                                     error="；".join(quality["errors"][:3]),
                                     warnings=warnings + quality["errors"],
                                     progress={"stage": "merge", "done": 1, "total": 1})
            return
        _save_or_replace(student_id, topic_key, payload)
        # P6-C2：概念→chunks 预索引（确定性；失败不影响图谱）。
        try:
            _save_concept_index(student_id, tb, spec, payload)
        except Exception:
            pass

        chapter_count = sum(1 for n in data["nodes"] if n.get("kind") == "chapter")
        needs_reextract = bool(
            (spec.get("chapter_detection") or {}).get("degraded")
        )
        tb_store.update_textbook(
            student_id, tb["id"], status="ready",
            chapter_count=chapter_count, concept_count=data["concept_count"],
            warnings=warnings, error="", needs_reextract=needs_reextract,
            progress={"stage": "merge", "done": 1, "total": 1})
    except Exception as e:  # 任何异常 → graph_failed，绝不抛出
        from ...core.textbook_ocr import TextbookOCRDeferred, TextbookParseCancelled
        if isinstance(e, TextbookOCRDeferred):
            _settle_deferred_book_status(student_id, tb["id"], str(e.status or ""),
                                         warnings)
            return
        if isinstance(e, TextbookParseCancelled):
            try:
                tb_store.settle_cancelled_parse(student_id, tb["id"])
            except Exception:
                pass
            return
        logger.warning("textbook %s/%s build failed: %s",
                       student_id, tb["id"], e)
        try:
            tb_store.update_textbook(student_id, tb["id"], status="graph_failed",
                                     error=f"构建异常：{e}",
                                     warnings=warnings,
                                     progress={"stage": "merge", "done": 0, "total": 1})
        except Exception:
            pass


async def build_group_graph(student_id: str, group_id: str,
                            llm: Any | None = None,
                            *, ocr_parallel: bool = False,
                            force_reextract: bool = False,
                            skip_ocr: bool = False,
                            skip_harvest: bool = False,
                            force_full_ocr: bool = False) -> None:
    """教材组构建：逐卷 spec → 合并统一 spec → 单次 spec_to_graph → 组 topic_key。

    跨卷同名概念经 spec_to_graph 的全局 name_to_id 合并为一个节点，按名前置
    引用跨卷成边（确定性，无额外 LLM）。永不抛出；某卷失败跳过并 warning，
    全部失败才 graph_failed。
    """
    if not settings.textbook_graph_enabled:
        tb_store.update_textbook(student_id, group_id,
                                 status="ready", chapter_count=0, concept_count=0,
                                 progress={"stage": "merge", "done": 1, "total": 1})
        return
    grp = tb_store.find_textbook(student_id, group_id)
    if grp is None or grp.get("kind") != "group":
        return
    # per-book 构建锁：同一本书绝不并发构建（不同书可并行，见队列契约）。
    lock = await _lock_for(student_id, group_id)
    async with lock:
        await _build_group_inner(student_id, grp, llm, ocr_parallel=ocr_parallel,
                                 force_reextract=force_reextract, skip_ocr=skip_ocr,
                                 skip_harvest=skip_harvest,
                                 force_full_ocr=force_full_ocr)


_VOLUME_SPEC_SCHEMA = "3"
# 章节定位器版本：书签标题质检/卷名剥离/独立短行识别（v3）；前置页剔除（v4）；
# 节级定位（v5）——Tier 1 子书签 + Tier 2 压缩匹配定位章内二级条目（语文
# 课/篇目、理科小节）。缓存校验强制该版本一致——旧缓存无节层数据，定向失效
# 后由「复用 OCR 文本重建」补齐，不触发重新 OCR。
_CHAPTER_LOCATOR_VERSION = "5"
# 概念名统一长度上限（跨学段一致显示；真实长标题保留在章节层）
_MAX_CONCEPT_NAME_CHARS = 12


def _prompt_fingerprint() -> str:
    ids = ("knowledge_graph_build", "textbook_toc_extract", "textbook_skeleton",
           "textbook_chapter_concepts", "textbook_graph_design")
    return "+".join(f"{pid}@{get_prompt(pid).version}" for pid in ids)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_cached_spec(cache: dict[str, Any] | None, file_id: str, text: str) -> bool:
    normalized = cache.get("normalized_spec") if cache else None
    if not (cache and cache.get("file_id") == file_id
            and cache.get("text_sha256") == _text_hash(text)
            and cache.get("prompt_version") == _prompt_fingerprint()
            and cache.get("schema_version") == _VOLUME_SPEC_SCHEMA
            and cache.get("chapter_locator_version") == _CHAPTER_LOCATOR_VERSION
            and isinstance(normalized, dict)):
        return False
    detection = normalized.get("chapter_detection")
    if isinstance(detection, dict) and detection.get("degraded"):
        return False
    # 兼容保留既有正常 volume cache（例如大学物理学两卷），只定向淘汰旧版
    # locator 产出的“长教材 + 全书单章”错误缓存。这样算法升级不会触发无关教材
    # 重新 OCR/抽取，也不会破坏 policy 快速重合并。
    chapters = [c for c in (normalized.get("chapters") or []) if isinstance(c, dict)]
    if (_is_long_textbook(text) and len(chapters) == 1
            and str(chapters[0].get("name") or "").strip() in {"全书", "全册"}):
        return False
    # 定向失效：缓存里残留文件名/工单/卷包装伪章名（旧定位器产物）→ 重建。
    # 好书的章名永远不会命中垃圾判定，缓存零成本复用。
    if any(_garbage_outline_title(str(c.get("name") or ""), "") for c in chapters):
        return False
    return True


def _apply_volume_policy(spec: dict[str, Any], limits: dict[str, int | None]) \
        -> tuple[dict[str, Any], dict[str, Any]]:
    """Crop one complete normalized volume spec without sharing a group budget."""
    complete = [c for c in (spec.get("chapters") or []) if isinstance(c, dict)]
    max_chapters = limits.get("max_chapters")
    max_concepts = limits.get("max_concepts")
    selected = complete if max_chapters is None else complete[:max_chapters]
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    truncated = len(selected) < len(complete)
    for chapter in selected:
        item = copy.deepcopy(chapter)
        concepts: list[dict[str, Any]] = []
        for concept in chapter.get("concepts") or []:
            if not isinstance(concept, dict):
                continue
            name = str(concept.get("name") or "").strip()
            if not name:
                continue
            normalized = re.sub(r"\s+", "", name).casefold()
            if normalized not in seen:
                if max_concepts is not None and len(seen) >= max_concepts:
                    truncated = True
                    continue
                seen.add(normalized)
            concepts.append(copy.deepcopy(concept))
        item["concepts"] = concepts
        sections = [s for s in (item.get("sections") or []) if isinstance(s, dict)
                    and str(s.get("name") or "").strip()]
        if len(sections) > 40:
            sections = sections[:40]
            truncated = True
        if sections:
            item["sections"] = sections
        else:
            item.pop("sections", None)
        # 无概念的章也保留（覆盖每一章每一节）；只有概念才计入概念预算。
        kept.append(item)
    out = dict(spec)
    out["chapters"] = kept
    keys = {str(c.get("chapter_key") or c.get("name") or "") for c in kept}
    names = {str(c.get("name") or "") for c in kept}
    out["page_ranges"] = {k: v for k, v in (spec.get("page_ranges") or {}).items()
                          if k in keys or k in names}
    sec_keys = {str(s.get("section_key") or "") for c in kept
                for s in (c.get("sections") or []) if isinstance(s, dict)}
    sec_names = {str(s.get("name") or "") for c in kept
                 for s in (c.get("sections") or []) if isinstance(s, dict)}
    if "section_ranges" in spec:
        out["section_ranges"] = {k: v for k, v in (spec.get("section_ranges") or {}).items()
                                 if k in sec_keys or k in sec_names}
    included_names = {re.sub(r"\s+", "", str(x.get("name") or "")).casefold()
                      for c in kept for x in (c.get("concepts") or [])}
    extracted_names = {re.sub(r"\s+", "", str(x.get("name") or "")).casefold()
                       for c in complete for x in (c.get("concepts") or [])
                       if str(x.get("name") or "").strip()}
    coverage = {
        "extracted_chapter_count": len(complete),
        "extracted_concept_count": len(extracted_names),
        "included_chapter_count": len(kept),
        "included_concept_count": len(included_names),
        "empty_concept_chapters": sum(1 for c in kept
                                      if not (c.get("concepts") or [])),
        "truncated": truncated or len(included_names) < len(extracted_names),
        "effective_limits": dict(limits),
    }
    return out, coverage


async def _load_or_extract_group_volume(student_id: str, grp: dict[str, Any],
                                        file_id: str, title: str, llm: Any | None,
                                        warnings: list[str], *, ocr_parallel: bool,
                                        force_reextract: bool, skip_ocr: bool = False,
                                        skip_harvest: bool = False,
                                        force_full_ocr: bool = False) \
        -> tuple[dict[str, Any] | None, str]:
    """Return a complete normalized spec, preferring a valid per-volume cache."""
    text, _raw, _ext = _read_library_text(student_id, file_id)
    cache = kg_store.load_volume_spec(student_id, grp["topic_key"], file_id)
    if not force_reextract and _valid_cached_spec(cache, file_id, text):
        return copy.deepcopy(cache["normalized_spec"]), "cache"
    if llm is None:
        return None, "cache_missing"
    text, raw_spec = await _volume_spec(
        student_id, grp["id"], file_id, title, str(grp.get("level") or ""), llm,
        warnings, ocr_parallel=ocr_parallel, skip_ocr=skip_ocr,
        skip_harvest=skip_harvest, force_full_ocr=force_full_ocr)
    if not text.strip() or raw_spec is None:
        return None, "extract_failed"
    normalized, norm_warnings = normalize_textbook_spec(
        raw_spec, textbook_title=str(grp.get("title") or ""),
        volume_id=file_id, volume_title=title)
    warnings.extend(norm_warnings)
    kg_store.save_volume_spec(student_id, grp["topic_key"], file_id, {
        "file_id": file_id,
        "text_sha256": _text_hash(text),
        "prompt_version": _prompt_fingerprint(),
        "schema_version": _VOLUME_SPEC_SCHEMA,
        "chapter_locator_version": _CHAPTER_LOCATOR_VERSION,
        "chapter_excerpt_version": _CHAPTER_EXCERPT_VERSION,
        "raw_spec": raw_spec,
        "normalized_spec": normalized,
    })
    return normalized, "extracted"


async def _build_group_inner(student_id: str, grp: dict[str, Any],
                             llm: Any | None, *, ocr_parallel: bool = False,
                             force_reextract: bool = False, skip_ocr: bool = False,
                             skip_harvest: bool = False,
                             force_full_ocr: bool = False) -> None:
    from ...core.library import load_library
    warnings: list[str] = []
    gid = grp["id"]
    try:
        file_ids = list(grp.get("file_ids") or [])
        tb_store.update_textbook(student_id, gid, status="building",
                                 progress={"stage": "index", "done": 0, "total": 1},
                                 error="", warnings=[])
        lib = load_library(student_id)
        chapters: list[dict[str, Any]] = []
        page_ranges: dict[str, list[int]] = {}
        ch_volume: dict[str, str] = {}     # 前缀后章名 -> 卷 file_id（概念索引用）
        subject = str(grp.get("subject") or "").strip()
        spec_level = ""
        coverage: list[dict[str, Any]] = []
        failed_files: list[str] = []
        degraded_files: list[str] = []
        # 逐卷并行抽取（卷间独立：各卷有独立 .txt/OCR 状态/volume_spec 缓存）。
        # 并发由 textbook_pipeline.volume_concurrency() 控制；legacy=1 时信号量
        # 串行且任务按卷序创建/FIFO 准入，执行顺序与历史逐卷 for 循环一致。
        # OCR 延迟（TextbookOCRDeferred）捕获为哨兵：兄弟卷继续完成（spec 落
        # 缓存，重试轮零成本复用），随后在按序后处理时按原语义统一抛出。
        from ...core.textbook_ocr import TextbookOCRDeferred

        async def _extract_one(fid: str) -> dict[str, Any]:
            meta = lib.find_file(fid) or {}
            vol_title = (meta.get("filename") or fid)[:40]
            vol_warnings: list[str] = []
            try:
                spec, cache_status = await _load_or_extract_group_volume(
                    student_id, grp, fid, vol_title, llm, vol_warnings,
                    ocr_parallel=ocr_parallel, force_reextract=force_reextract,
                    skip_ocr=skip_ocr, skip_harvest=skip_harvest,
                    force_full_ocr=force_full_ocr)
            except TextbookOCRDeferred as exc:
                return {"fid": fid, "vol_title": vol_title,
                        "warnings": vol_warnings, "spec": None,
                        "cache_status": "", "deferred": exc}
            return {"fid": fid, "vol_title": vol_title, "warnings": vol_warnings,
                    "spec": spec, "cache_status": cache_status, "deferred": None}

        volume_sem = asyncio.Semaphore(textbook_pipeline.volume_concurrency())
        volumes_done = 0

        async def _volume_task(fid: str) -> dict[str, Any]:
            nonlocal volumes_done
            async with volume_sem:
                result = await _extract_one(fid)
            volumes_done += 1
            tb_store.update_textbook(
                student_id, gid,
                progress={"stage": "chapters", "done": volumes_done,
                          "total": len(file_ids)})
            return result

        if file_ids and not _record_alive(student_id, gid):
            return
        volume_tasks = [asyncio.ensure_future(_volume_task(fid))
                        for fid in file_ids]
        try:
            volume_results = list(await asyncio.gather(*volume_tasks))
        except BaseException:
            for t in volume_tasks:
                if not t.done():
                    t.cancel()
            try:
                await asyncio.gather(*volume_tasks, return_exceptions=True)
            except BaseException:
                pass  # 回收在途任务结果，避免未取回异常告警
            raise
        for done, result in enumerate(volume_results, 1):
            if not _record_alive(student_id, gid):
                return
            fid = result["fid"]
            vol_title = result["vol_title"]
            warnings.extend(result["warnings"])
            if result["deferred"] is not None:
                raise result["deferred"]
            spec, cache_status = result["spec"], result["cache_status"]
            if spec is None or not spec.get("chapters"):
                warnings.append(f"卷「{vol_title[:30]}」文本为空或概念抽取失败，已跳过")
                failed_files.append(fid)
                coverage.append({"file_id": fid, "name": vol_title, "status": "failed",
                                 "error": cache_status, "truncated": False,
                                 "effective_limits": tb_store.effective_graph_limits(grp, fid),
                                 "extracted_chapter_count": 0, "extracted_concept_count": 0,
                                 "included_chapter_count": 0, "included_concept_count": 0})
                continue
            chapter_detection = dict(spec.get("chapter_detection") or {})
            if chapter_detection.get("degraded"):
                degraded_files.append(fid)
            spec, vol_coverage = _apply_volume_policy(
                spec, tb_store.effective_graph_limits(grp, fid))
            vol_coverage.update({"file_id": fid, "name": vol_title, "status": "included",
                                 "cache_status": cache_status, "error": "",
                                 "chapter_detection": chapter_detection})
            if not spec.get("chapters"):
                vol_coverage["status"] = "failed"
                vol_coverage["error"] = "capacity_policy_excluded_all"
                failed_files.append(fid)
                coverage.append(vol_coverage)
                continue
            coverage.append(vol_coverage)
            if not subject:
                subject = str(spec.get("subject") or "").strip()[:30]
            if not spec_level:
                spec_level = str(spec.get("level") or "").strip()
            # 组级章序：跨卷统一排序键 = 卷序号*1000 + 卷内章序（同名 chapter_order
            # 在不同卷会冲突，导致前端同层排序乱序——大学物理两分册实测问题）。
            for ch in spec.get("chapters") or []:
                name = str(ch.get("name") or "").strip()
                if not name:
                    continue
                chapter_item = dict(ch)
                meta = dict(chapter_item.get("metadata") or {})
                try:
                    local_order = int(meta.get("chapter_order") or 1)
                except (TypeError, ValueError):
                    local_order = 1
                meta["chapter_order"] = done * 1000 + local_order
                chapter_item["metadata"] = meta
                chapters.append(chapter_item)
                chapter_key = str(ch.get("chapter_key") or name)
                rng = (spec.get("page_ranges") or {}).get(chapter_key) \
                    or (spec.get("page_ranges") or {}).get(name)
                if rng:
                    page_ranges[chapter_key] = rng
                # Display names stay clean; source-volume routing lives only in
                # metadata. Keep both keys for old callers and current indexer.
                ch_volume[str(ch.get("chapter_key") or name)] = fid
                ch_volume[name] = fid
        if not chapters:
            tb_store.update_textbook(student_id, gid, status="graph_failed",
                                     error="LLM 未产出有效概念",
                                     warnings=warnings,
                                     progress={"stage": "merge", "done": 0, "total": 1})
            return

        # 学段：组上传时的选择优先；未选才用卷骨架推断值。
        level = str(grp.get("level") or "").strip() or spec_level
        volumes = [{"file_id": fid, "volume_id": fid,
                    "title": str((lib.find_file(fid) or {}).get("filename") or fid)}
                   for fid in file_ids]
        merged_spec = {"subject": subject, "level": level,
                       "chapters": chapters, "page_ranges": page_ranges,
                       "volumes": volumes}
        topic_key = grp["topic_key"]
        tb_store.update_textbook(student_id, gid, subject=subject, level=level,
                                 progress={"stage": "merge", "done": 0, "total": 1})

        # DS 图谱设计阶段：标签统一/同义归并/跨章继承（失败自动降级）。
        if not _record_alive(student_id, gid):
            return
        try:
            design = await _graph_design_pass(merged_spec, llm, grp.get("title") or "")
            applied = _apply_graph_design(merged_spec, design)
            if applied:
                warnings.append("图谱设计：" + "；".join(applied))
        except Exception as exc:
            warnings.append(f"图谱设计跳过：{str(exc)[:120]}")

        # 单次确定性合并：跨卷同名概念/前置引用在 spec_to_graph 全局按名归并。
        base = get_knowledge_service().graph
        data, merge_warnings = cg.spec_to_graph(
            merged_spec, topic_key=topic_key, source=f"textbook:{gid}",
            base_graph=base,
            max_chapters=None, max_concepts=None, max_concepts_per_chapter=None,
            level=level)
        warnings.extend(merge_warnings)
        if not data["concept_count"]:
            tb_store.update_textbook(student_id, gid, status="graph_failed",
                                     error="合并后概念数为 0",
                                     warnings=warnings,
                                     progress={"stage": "merge", "done": 1, "total": 1})
            return

        # 写入 M5.7 store：质量门通过后原子替换。重建不再产生隐藏历史归档。
        # 删除守卫：多卷构建耗时长，途中记录被删则中止，不复活孤儿图谱。
        if not _record_alive(student_id, gid):
            return
        pseudo = {"title": grp.get("title"), "topic_key": topic_key, "file_id": gid}
        payload = _build_payload(pseudo, merged_spec, data, subject, level)
        quality = graph_quality(payload, textbook_title=str(grp.get("title") or ""))
        payload["quality"] = quality
        if not quality["ok"]:
            tb_store.update_textbook(student_id, gid, status="graph_failed",
                                     error="；".join(quality["errors"][:3]),
                                     warnings=warnings + quality["errors"],
                                     progress={"stage": "merge", "done": 1, "total": 1})
            return
        payload["coverage"] = coverage
        payload["build_status"] = "partial" if failed_files else "ready"
        previous = kg_store.load_custom_graph(student_id, topic_key)
        previous_covered = {str(v.get("file_id") or "") for v in (previous or {}).get("coverage", [])
                            if v.get("status") == "included"}
        if previous and not previous_covered:
            previous_covered = {
                str((node.get("metadata") or {}).get("file_id") or
                    (node.get("metadata") or {}).get("volume_id") or "")
                for node in (previous.get("nodes") or []) if node.get("kind") == "chapter"
            } - {""}
        new_covered = {str(v.get("file_id") or "") for v in coverage
                       if v.get("status") == "included"}
        degraded = bool(previous and previous_covered and not previous_covered.issubset(new_covered))
        if degraded:
            warnings.append("新构建因临时失败丢失既有卷，已保留上一版可用图谱")
        else:
            _save_or_replace(student_id, topic_key, payload)
        # P6-C2：概念→chunks 预索引（跨卷；确定性；失败不影响图谱）。
        try:
            _save_concept_index_group(student_id, grp, merged_spec, ch_volume, payload)
        except Exception:
            pass

        chapter_count = sum(1 for n in data["nodes"] if n.get("kind") == "chapter")
        tb_store.update_textbook(
            student_id, gid, status="partial" if failed_files else "ready",
            chapter_count=chapter_count, concept_count=data["concept_count"],
            volumes=coverage, warnings=warnings,
            error=("部分教材未进入知识谱系" if failed_files else ""),
            needs_reextract=bool(degraded_files),
            progress={"stage": "merge", "done": 1, "total": 1})
    except Exception as e:  # 任何异常 → graph_failed，绝不抛出
        from ...core.textbook_ocr import TextbookOCRDeferred, TextbookParseCancelled
        if isinstance(e, TextbookOCRDeferred):
            _settle_deferred_book_status(student_id, gid, str(e.status or ""),
                                         warnings)
            return
        if isinstance(e, TextbookParseCancelled):
            try:
                tb_store.settle_cancelled_parse(student_id, gid)
            except Exception:
                pass
            return
        logger.warning("textbook group %s/%s build failed: %s",
                       student_id, gid, e)
        try:
            tb_store.update_textbook(student_id, gid, status="graph_failed",
                                     error=f"构建异常：{e}",
                                     warnings=warnings,
                                     progress={"stage": "merge", "done": 0, "total": 1})
        except Exception:
            pass


async def _fast_path_spec(text: str, filename_hint: str, llm: Any | None) -> dict[str, Any] | None:
    """小教材（≤20000 字）：单次 generate_spec，复用既有 prompt。"""
    if llm is None:
        return None
    spec, err = await cg.generate_spec(filename_hint, text, llm, grade="未指定")
    if isinstance(spec, dict):
        for ch in spec.get("chapters") or []:
            if isinstance(ch, dict) and _garbage_outline_title(str(ch.get("name") or ""),
                                                               filename_hint):
                ch["name"] = _normalize_chapter_name(str(ch.get("name") or ""),
                                                     filename_hint) or "全册"
    return spec


async def _full_path_spec(student_id: str, tb_id: str, text: str, raw: bytes | None,
                           filename_hint: str, llm: Any | None,
                           warnings: list[str]) -> dict[str, Any] | None:
    """完整路径：章节切片 → 骨架 → 逐章概念抽取 → 合并 spec。

    逐章更新 progress（stage=chapters, done/total）让前端轮询可见进度。
    """
    if llm is None:
        # 无 LLM：无法抽取概念，直接失败（调用方落 graph_failed）。
        return None
    tb_store.update_textbook(student_id, tb_id,
                             progress={"stage": "skeleton", "done": 0, "total": 1})
    slices, toc_text = await extract_chapters(text, raw, llm, volume_hint=filename_hint)
    degraded_whole_book = bool(
        len(slices) == 1 and slices[0][0] == "全册" and _is_long_textbook(text)
    )
    if degraded_whole_book:
        warnings.append(
            "长教材未能定位到多个正文章节，当前知识谱系降级为“全册”单章；"
            "教材全文 RAG 不受影响，建议重建图谱"
        )
    # 骨架推断只补缺：用户上传时已选学段/已有学科时不覆盖（选择优先）。
    inferred = await _skeleton_call(toc_text, filename_hint, llm)
    cur = tb_store.find_textbook(student_id, tb_id) or {}
    eff_subject = str(cur.get("subject") or "").strip() or inferred["subject"]
    eff_level = str(cur.get("level") or "").strip() or inferred["level"]
    tb_store.update_textbook(student_id, tb_id,
                             subject=eff_subject, level=eff_level,
                             progress={"stage": "chapters", "done": 0, "total": len(slices)})

    chapters_out: list[dict[str, Any]] = []
    page_ranges: dict[str, list[int]] = {}
    # 逐章并行抽取：章间零依赖（每章 prompt 只依赖章名/章文本/书级
    # subject/level，后两者在循环前已定）。并发由全局 llm_gate 限流；legacy
    # 模式（limit=1）下 FIFO 准入顺序与历史串行 for 循环一致。结算严格按
    # 章序进行：chapters_out/warnings/progress/取消检查点语义与串行逐项相同。
    chapter_tasks = [asyncio.ensure_future(
        _extract_chapter_concepts(ch_name, ch_text, eff_subject, eff_level, llm))
        for (ch_name, ch_text, _rng, _secs) in slices]
    try:
        for i, ((ch_name, ch_text, ch_range, sections), task) in enumerate(
                zip(slices, chapter_tasks), 1):
            concepts = await task
            if not concepts:
                # 单次失败先重试一次（LLM 偶发空回包/解析失败很常见），
                # 重试仍失败也保留章结构——覆盖每一章每一节，绝不整章丢弃。
                concepts = await _extract_chapter_concepts(
                    ch_name, ch_text, eff_subject, eff_level, llm)
            clean_sections = [{"name": str(s.get("name") or ""),
                                "page_range": list(s.get("page_range") or [])}
                              for s in sections if str(s.get("name") or "").strip()]
            chapter_entry: dict[str, Any] = {"name": ch_name, "concepts": concepts}
            if clean_sections:
                chapter_entry["sections"] = clean_sections[:40]
            if concepts:
                _attach_concepts_to_sections(concepts, ch_text, sections)
            else:
                warnings.append(f"第 {i} 章「{ch_name[:20]}」概念抽取失败，已保留章节结构")
            chapters_out.append(chapter_entry)
            if ch_range:
                page_ranges[ch_name] = [ch_range[0], ch_range[1]]
            updated = tb_store.update_textbook(
                student_id, tb_id,
                progress={"stage": "chapters", "done": i, "total": len(slices)})
            if updated is not None and updated.get("parse_cancel_requested"):
                from ...core.textbook_ocr import TextbookParseCancelled
                raise TextbookParseCancelled(f"{ch_name[:20]} 抽取中终止")
    finally:
        # 终止/异常时停掉在途章任务，不再浪费 LLM 调用（正常走完时全部已 done）。
        for t in chapter_tasks:
            if not t.done():
                t.cancel()
    if not chapters_out:
        return None
    return {"subject": eff_subject, "level": eff_level, "chapters": chapters_out,
            "page_ranges": page_ranges,
            "chapter_detection": {
                "degraded": degraded_whole_book,
                "reason": "long_text_whole_book_fallback" if degraded_whole_book else "",
            }}


def _section_match_terms(name: str, aliases: list[str] | None = None) -> list[str]:
    """节名/别名的压缩匹配键（wordish + 剥课序前缀），≥2 字才有效。

    「沁园春·长沙」「1 沁园春·长沙」→「沁园春长沙」，OCR 标点差异不再漏配。
    """
    terms: list[str] = []
    for raw in [name, *(aliases or [])]:
        for key in (_section_anchor_key(raw), _wordish(raw)):
            if len(key) >= 2 and key not in terms:
                terms.append(key)
    return terms


def _pool_section_hits(pool: list, terms: list[str]) -> list[str]:
    """节检索域内按压缩键子串预过滤 chunk_id（噪声块剔除，≤50）。"""
    if not terms:
        return []
    noise = {"toc", "copyright", "preface", "header_footer"}
    hits: list[str] = []
    for c in pool:
        if set(c.metadata.get("noise_flags", [])) & noise:
            continue
        text = _wordish(c.text)
        if any(t in text for t in terms):
            hits.append(c.chunk_id)
            if len(hits) >= 50:
                break
    return hits


def _concept_index_maps(payload: dict[str, Any]) -> tuple[
        dict[str, str], dict[str, list[str]], dict[str, list[str]]]:
    """从图谱 payload 提取概念索引所需的映射：
    (concept_id→name, concept_id→aliases, chapter_name→[concept_id])。"""
    ch_name_by_id = {n["id"]: n.get("name", "") for n in payload.get("nodes", [])
                     if n.get("kind") == "chapter"}
    name_of: dict[str, str] = {}
    aliases_of: dict[str, list[str]] = {}
    for n in payload.get("nodes", []):
        if n.get("kind") == "concept":
            name_of[n["id"]] = n.get("name", "")
            aliases_of[n["id"]] = list(n.get("aliases") or [])
    concepts_of: dict[str, list[str]] = {}   # chapter name -> [concept_id]
    for e in payload.get("edges", []):
        if str(e.get("type", "")).upper() == "PART_OF":
            ch_name = ch_name_by_id.get(e.get("target"), "")
            if ch_name and e.get("source") in name_of:
                concepts_of.setdefault(ch_name, []).append(e["source"])
    return name_of, aliases_of, concepts_of


def _index_concepts_in_pools(payload: dict[str, Any],
                             chapter_pools: list[tuple[str, list, list]]) -> dict[str, Any]:
    """概念→chunks 子串预过滤核心。chapter_pools: [(ch_name, pool, rng)]。"""
    name_of, aliases_of, concepts_of = _concept_index_maps(payload)
    concepts: dict[str, Any] = {}
    for ch_name, pool, rng in chapter_pools:
        for cid in concepts_of.get(ch_name, []):
            terms = [t.strip() for t in ([name_of.get(cid, "")] + aliases_of.get(cid, []))
                     if t and len(t.strip()) >= 2]
            if not terms:
                continue
            hits = [c.chunk_id for c in pool
                    if not set(c.metadata.get("noise_flags", [])) & {"toc", "copyright", "preface", "header_footer"}
                    and any(t in c.text for t in terms)][:50]
            if hits:
                concepts[cid] = {"name": name_of[cid], "chapter": ch_name,
                                 "pages": rng or [], "chunk_ids": hits}
    return concepts


def _concept_chapter_pool(chunks: list, chapter_name: str, rng: list | None) -> list:
    noise = {"toc", "copyright", "preface", "header_footer"}
    if rng:
        return [c for c in chunks if c.page and rng[0] <= c.page <= rng[1]
                and not set(c.metadata.get("noise_flags", [])) & noise]
    # Structured V2: section_path is the preferred deterministic anchor.
    path_hits = [c for c in chunks
                 if any(chapter_name and chapter_name in str(part)
                        for part in c.metadata.get("section_path", []))
                 and not set(c.metadata.get("noise_flags", [])) & noise]
    if path_hits:
        return path_hits
    # Legacy fallback: start at a clean heading and stop at the next chapter heading.
    compact = re.sub(r"\s+", "", chapter_name).casefold()
    start = next((i for i, c in enumerate(chunks)
                  if compact and compact in re.sub(r"\s+", "", c.text[:140]).casefold()
                  and not set(c.metadata.get("noise_flags", [])) & noise), None)
    if start is None:
        return []  # never fall back to whole-book term matches
    end = min(len(chunks), start + 120)
    for i in range(start + 1, end):
        c = chunks[i]
        if "heading" in c.metadata.get("block_types", []) and re.search(
                r"第\s*[一二三四五六七八九十百0-9]+\s*章|chapter\s*\d+",
                c.text[:100], re.I):
            end = i
            break
    return [c for c in chunks[start:end]
            if not set(c.metadata.get("noise_flags", [])) & noise]


def _add_section_index_entry(concepts: dict[str, Any], section: dict[str, Any],
                             chapter: dict[str, Any], lib: Any,
                             ranges: dict[str, list[int]]) -> None:
    """教材组版节条目：检索域 = 节页码区间（缺失退化为章区间/节名锚定池），
    条目按节节点 id 键入 concepts（与概念条目同构，kind=section 供消费端
    区分展示）。确定性、零 LLM。"""
    metadata = chapter.get("metadata") or {}
    fid = str(metadata.get("file_id") or metadata.get("volume_id") or "")
    chunks = lib.chunks_for(fid) if fid else []
    if not chunks:
        return
    sec_name = str(section.get("name") or "")
    sec_meta = section.get("metadata") or {}
    sec_rng = sec_meta.get("page_range") or sec_meta.get("pages") or []
    chapter_key = str(metadata.get("chapter_key") or "")
    fallback_rng = ranges.get(chapter_key) or ranges.get(str(chapter.get("name") or ""))
    rng = None
    if isinstance(sec_rng, list) and len(sec_rng) >= 2:
        try:
            rng = [int(sec_rng[0]), int(sec_rng[1])]
        except (TypeError, ValueError):
            rng = None
    if rng is None and isinstance(fallback_rng, list) and len(fallback_rng) >= 2:
        try:
            rng = [int(fallback_rng[0]), int(fallback_rng[1])]
        except (TypeError, ValueError):
            rng = None
    pool = _concept_chapter_pool(chunks, sec_name, rng)
    terms = _section_match_terms(sec_name, section.get("aliases") or [])
    hits = _pool_section_hits(pool, terms)
    if not hits:
        return
    sid = str(section.get("id") or "")
    entry = concepts.setdefault(sid, {
        "name": sec_name, "chapter": str(chapter.get("name") or ""),
        "pages": rng or [], "chunk_ids": [], "file_ids": [],
        "chapter_ids": [], "chunk_ids_by_file": {}, "kind": "section"})
    entry["chunk_ids"] = list(dict.fromkeys(entry["chunk_ids"] + hits))[:50]
    if fid and fid not in entry["file_ids"]:
        entry["file_ids"].append(fid)
    chapter_id = str(chapter.get("id") or "")
    if chapter_id and chapter_id not in entry["chapter_ids"]:
        entry["chapter_ids"].append(chapter_id)
    entry["chunk_ids_by_file"][fid] = list(dict.fromkeys(
        entry["chunk_ids_by_file"].get(fid, []) + hits))[:50]


def _save_concept_index(student_id: str, tb: dict[str, Any],
                        spec: dict[str, Any], payload: dict[str, Any]) -> None:
    """P6-C2：构建概念→chunks 预索引（按知识点限定检索域，检索更快更准）。

    确定性、零 LLM：概念 name/aliases 在其**章节页码范围内**的 chunks 里做
    子串预过滤（无页码区间的 Tier 2/3 退化为全书范围），每概念 ≤50 个
    chunk_id。节（课/篇目）条目走压缩匹配（间隔号/编号不敏感）。与图谱同
    生命周期（store 的 .chunks.json，随删除联动）。失败只跳过，绝不影响
    图谱构建。
    """
    from ...core.library import load_library
    lib = load_library(student_id)
    chunks = lib.chunks_for(tb["file_id"])
    if not chunks:
        return
    ranges = spec.get("page_ranges") or {}
    pools: list[tuple[str, list, list]] = []
    for ch in spec.get("chapters") or []:
        ch_name = str(ch.get("name") or "")
        rng = ranges.get(ch_name)
        pool = _concept_chapter_pool(chunks, ch_name, rng)
        pools.append((ch_name, pool, rng or []))
    concepts = _index_concepts_in_pools(payload, pools)
    # 节条目：节名/别名压缩匹配其页码域内 chunks（无页码域退化为节名锚定池）。
    section_by_name = {str(n.get("name") or ""): n for n in payload.get("nodes", [])
                       if n.get("kind") == "section"}
    for ch in spec.get("chapters") or []:
        ch_name = str(ch.get("name") or "")
        ch_rng = ranges.get(ch_name) or []
        for sec in ch.get("sections") or []:
            sec_name = str(sec.get("name") or "")
            node = section_by_name.get(sec_name)
            if not node:
                continue
            sec_rng = [int(x) for x in (sec.get("page_range") or []) if str(x).isdigit()]
            pool = _concept_chapter_pool(
                chunks, sec_name, sec_rng or None) if sec_rng else \
                _concept_chapter_pool(chunks, ch_name, ranges.get(ch_name))
            hits = _pool_section_hits(
                pool, _section_match_terms(sec_name, node.get("aliases") or []))
            if hits:
                concepts[str(node.get("id") or "")] = {
                    "name": sec_name, "chapter": ch_name, "pages": sec_rng,
                    "chunk_ids": hits, "kind": "section"}
    if concepts:
        kg_store.save_concept_chunks(student_id, tb["topic_key"],
                                     {"file_id": tb["file_id"], "concepts": concepts})


def _save_concept_index_group(student_id: str, grp: dict[str, Any],
                              merged_spec: dict[str, Any],
                              ch_volume: dict[str, str],
                              payload: dict[str, Any]) -> None:
    """教材组版概念→chunks 预索引：检索域按**章所属卷**的 chunks 限定，
    条目 chunk_ids 跨卷混合（chunk_id 内嵌 file_id，消费端按 id 过滤天然
    支持）。与单教材同构：确定性、零 LLM，失败只跳过。
    """
    from ...core.library import load_library
    lib = load_library(student_id)
    ranges = merged_spec.get("page_ranges") or {}
    nodes = payload.get("nodes") or []
    chapter_by_id = {n.get("id"): n for n in nodes if n.get("kind") == "chapter"}
    concept_by_id = {n.get("id"): n for n in nodes if n.get("kind") == "concept"}
    section_by_id = {n.get("id"): n for n in nodes if n.get("kind") == "section"}
    concepts: dict[str, Any] = {}
    # Use chapter identity/metadata, not display name: two volumes may both have
    # “第一章”, and display labels must remain clean rather than gain a filename.
    for edge in payload.get("edges") or []:
        if str(edge.get("type") or "").upper() != "PART_OF":
            continue
        source_id = str(edge.get("source") or "")
        target_id = str(edge.get("target") or "")
        if source_id in section_by_id and target_id in chapter_by_id:
            _add_section_index_entry(
                concepts, section_by_id[source_id], chapter_by_id[target_id],
                lib, ranges)
            continue
        concept = concept_by_id.get(source_id)
        chapter = chapter_by_id.get(target_id)
        if not concept or not chapter:
            continue
        metadata = chapter.get("metadata") or {}
        fid = str(metadata.get("file_id") or metadata.get("volume_id") or "")
        chunks = lib.chunks_for(fid)
        if not chunks:
            continue
        ch_name = str(chapter.get("name") or "")
        chapter_key = str(metadata.get("chapter_key") or "")
        if not chapter_key:
            chapter_id = str(chapter.get("id") or "")
            chapter_key = chapter_id.rsplit(".ch.", 1)[-1] if ".ch." in chapter_id else ch_name
        rng = ranges.get(chapter_key) or ranges.get(ch_name)
        pool = _concept_chapter_pool(chunks, ch_name, rng)
        terms = [str(concept.get("name") or "").strip()] + [
            str(a).strip() for a in (concept.get("aliases") or [])]
        terms = [t for t in terms if len(t) >= 2]
        hits = [c.chunk_id for c in pool
                if not set(c.metadata.get("noise_flags", [])) & {"toc", "copyright", "preface", "header_footer"}
                and any(t in c.text for t in terms)][:50]
        if not hits:
            continue
        cid = str(concept.get("id") or "")
        entry = concepts.setdefault(cid, {
            "name": concept.get("name", ""), "chapter": ch_name,
            "pages": rng or [], "chunk_ids": [], "file_ids": [],
            "chapter_ids": [], "chunk_ids_by_file": {},
        })
        entry["chunk_ids"] = list(dict.fromkeys(entry["chunk_ids"] + hits))[:50]
        if fid and fid not in entry["file_ids"]:
            entry["file_ids"].append(fid)
        chapter_id = str(chapter.get("id") or "")
        if chapter_id and chapter_id not in entry["chapter_ids"]:
            entry["chapter_ids"].append(chapter_id)
        entry["chunk_ids_by_file"][fid] = list(dict.fromkeys(
            entry["chunk_ids_by_file"].get(fid, []) + hits))[:50]
    if concepts:
        # 顶层 file_id 置空（多卷无单一文件；消费端只读 concepts）。
        kg_store.save_concept_chunks(student_id, grp["topic_key"],
                                     {"file_id": "", "concepts": concepts})


def _build_payload(tb: dict[str, Any], spec: dict[str, Any], data: dict[str, Any],
                   subject: str, level: str) -> dict[str, Any]:
    """Assemble the M5.7 store payload (mirrors custom_graph build shape)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    return {
        "version": 1,
        "topic": tb.get("title") or "教材",
        "topic_key": tb["topic_key"],
        "subject": subject,
        "level": data.get("level") or cg.CUSTOM_LEVEL,
        "source": f"textbook:{tb['file_id']}",
        "is_textbook": True,  # 标记，前端据此加「教材」徽标
        "volumes": list(spec.get("volumes") or []),
        "created_at": now,
        "updated_at": now,
        "nodes": data["nodes"],
        "edges": data["edges"],
        "contents": data["contents"],
    }


def _save_or_replace(student_id: str, topic_key: str, payload: dict[str, Any]) -> None:
    """Publish an already validated graph by atomic active-file replacement."""
    previous = kg_store.load_custom_graph(student_id, topic_key)
    if previous:
        payload["created_at"] = previous.get("created_at") or payload["created_at"]
        payload["version"] = int(previous.get("version", 1) or 1) + 1
    if not kg_store.save_custom_graph(student_id, topic_key, payload):
        raise OSError("知识图谱原子写入失败")
    # 失效合并视图缓存：写后让 graph_for 重建。
    try:
        get_knowledge_service().invalidate_custom_cache(student_id)
    except Exception:
        pass


def textbook_outline(student_id: str, tb_id: str) -> list[dict[str, Any]] | None:
    """Derive a chapter outline from the graph payload (for GET /textbooks/{id}).

    Returns [{chapter, concept_count, concepts: [name...]}] or None when no graph.
    """
    tb = tb_store.find_textbook(student_id, tb_id)
    if tb is None:
        return None
    payload = kg_store.load_custom_graph(student_id, tb["topic_key"])
    if payload is None:
        return None
    nodes = payload.get("nodes", []) or []
    edges = payload.get("edges", []) or []
    # chapter nodes (kind=chapter) → sections (kind=section) + concepts via
    # PART_OF edges；概念可挂节（节→章）或直接挂章，两种形状都归组。
    chapters = [n for n in nodes if n.get("kind") == "chapter"]
    sections = [n for n in nodes if n.get("kind") == "section"]
    part_of: dict[str, list[str]] = {}
    for e in edges:
        if str(e.get("type") or "").upper() == "PART_OF":
            part_of.setdefault(e.get("target", ""), []).append(e.get("source", ""))
    name_of = {n.get("id"): n.get("name") for n in nodes}
    section_ids_by_chapter = {s.get("id"): (s.get("metadata") or {}).get("chapter_ids") or []
                              for s in sections}
    concepts_by_section: dict[str, list[str]] = {}
    for sec in sections:
        concepts_by_section[sec.get("id", "")] = [
            cid for cid in part_of.get(sec.get("id", ""), [])
            if cid in name_of and cid not in section_ids_by_chapter]
    out: list[dict[str, Any]] = []
    for ch in chapters:
        ch_id = ch.get("id", "")
        direct = [cid for cid in part_of.get(ch_id, [])
                  if cid in name_of and cid not in section_ids_by_chapter]
        via_sections = [cid for sid in part_of.get(ch_id, [])
                        if sid in section_ids_by_chapter
                        for cid in concepts_by_section.get(sid, [])]
        concepts = [name_of.get(cid, cid) for cid in direct + via_sections
                    if name_of.get(cid)]
        section_names = [name_of.get(sid, "") for sid in part_of.get(ch_id, [])
                         if sid in section_ids_by_chapter and name_of.get(sid)]
        out.append({
            "chapter": ch.get("name", ""),
            "concept_count": len(concepts),
            "concepts": concepts,
            "sections": section_names,
        })
    return out


def rebuild_concept_index_from_active(student_id: str, textbook: dict[str, Any]) -> bool:
    """Zero-LLM refresh of concept->chunk ids after deterministic rechunking.

    Uses the active graph's chapter metadata/page ranges, so an existing
    textbook can adopt Structured RAG V2 without re-running OCR or graph LLM.
    """
    payload = kg_store.load_custom_graph(student_id, str(textbook.get("topic_key") or ""))
    if not payload:
        return False
    nodes = payload.get("nodes") or []
    chapter_nodes = [n for n in nodes if n.get("kind") == "chapter"]
    section_nodes = [n for n in nodes if n.get("kind") == "section"]
    section_ids = {n.get("id") for n in section_nodes}
    page_ranges: dict[str, list[int]] = {}
    chapters: list[dict[str, Any]] = []
    for node in chapter_nodes:
        meta = node.get("metadata") or {}
        name = str(node.get("name") or "")
        key = str(meta.get("chapter_key") or name)
        rng = meta.get("page_range") or meta.get("pages")
        if isinstance(rng, list) and len(rng) >= 2:
            page_ranges[key] = [int(rng[0]), int(rng[1])]
            page_ranges.setdefault(name, [int(rng[0]), int(rng[1])])
        # 节随章重建：名称 + 节自身页码区间（供节条目索引）。
        sections = []
        for sec in section_nodes:
            sec_meta = sec.get("metadata") or {}
            if str(node.get("id") or "") not in (sec_meta.get("chapter_ids") or []):
                continue
            sec_rng = sec_meta.get("page_range") or []
            sections.append({"name": str(sec.get("name") or ""),
                             "page_range": list(sec_rng or [])})
        chapters.append({"name": name, "chapter_key": key,
                         "sections": sections[:40], "concepts": []})
    spec = {"chapters": chapters, "page_ranges": page_ranges}
    if textbook.get("kind") == "group":
        _save_concept_index_group(student_id, textbook, spec, {}, payload)
    else:
        _save_concept_index(student_id, textbook, spec, payload)
    return True
