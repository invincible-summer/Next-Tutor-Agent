"""FastAPI application factory (adapted from Paper_Agent)."""
from __future__ import annotations

import asyncio
import logging
import threading
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

log = logging.getLogger(__name__)

# Dev-friendly default when CORS_ORIGINS is not set (local Next.js dev server).
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000", "http://127.0.0.1:3000", "http://0.0.0.0:3000",
    "http://localhost:3001", "http://127.0.0.1:3001", "http://0.0.0.0:3001",
    "http://localhost:3030", "http://127.0.0.1:3030", "http://0.0.0.0:3030",
]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # P1-C（plan.md §15-§19）：结构化 bootstrap——每个启动维护步骤都有
    # 命名 check 与状态，失败 log.exception（不再静默吞掉），critical 失败
    # fail-fast；可降级功能失败不阻止服务（/ready 里可见但不 503）。
    from app.core.bootstrap import run_bootstrap_step
    from app.core.bootstrap import reset_bootstrap_report
    report = reset_bootstrap_report()

    # 一次性清理旧式知识图谱 archive/*.json。该格式只有孤立图谱快照，
    # 无法恢复教材源文件；统一回收站上线后不再继续保留。
    async def _legacy_graph_cleanup() -> None:
        from app.agents.knowledge.store import cleanup_legacy_graph_archives
        cleanup_legacy_graph_archives()

    # 教材记录迁移 + 重启对账 + OCR 续跑 + 中断图谱构建重入队
    # （P1-B plan.md §11.3 顺序）。
    async def _textbook_recovery() -> None:
        from app.core.textbook import (migrate_legacy_single_to_groups,
                                       reconcile_stale_builds)
        from app.core.textbook_ocr import resume_pending_textbook_ocr
        migrate_legacy_single_to_groups()
        reconcile_stale_builds()
        resume_pending_textbook_ocr()
        from app.agents.knowledge.textbook_builder import (
            resume_interrupted_textbook_builds)
        resumed = await resume_interrupted_textbook_builds()
        if resumed:
            print(f"[startup] resumed {resumed} interrupted textbook build(s)",
                  flush=True)

    # 管理员引导（P6-B1）：配置了 ADMIN_EMAIL/ADMIN_PASSWORD 时确保管理员存在。
    def _admin_bootstrap() -> None:
        from app.identity.store import ensure_admin_account
        ensure_admin_account()

    # 预热默认学生模型（性能）：M5 合并公共教材图谱进 SkillGraph，放到
    # 后台线程避免冻结事件循环；预热失败只是首请求变慢，不阻止服务。
    def _warm_default_student_model() -> None:
        from app.agents.student_model import get_student_model, is_enabled
        if is_enabled():
            get_student_model()

    def _trash_cleanup_once() -> None:
        from app.core.trash import cleanup_expired
        cleanup_expired()

    await run_bootstrap_step(report, "legacy_graph_cleanup",
                             _legacy_graph_cleanup)
    await run_bootstrap_step(report, "textbook_recovery", _textbook_recovery)
    await run_bootstrap_step(report, "admin_bootstrap", _admin_bootstrap,
                             to_thread=True)

    async def _warm_async() -> None:
        await asyncio.to_thread(_warm_default_student_model)

    await run_bootstrap_step(report, "student_model_warm", _warm_async)
    await run_bootstrap_step(report, "trash_cleanup", _trash_cleanup_once,
                             to_thread=True)

    # R01：评价作业后台 worker——受理/对话 hook 只入队，执行/重试/恢复/
    # outbox 消费全部在此闭环。评价功能停用时不启动。
    evaluation_worker_task = None
    try:
        from app.core import learner_runtime
        from app.agents.student_model.evaluation.worker import (
            EvaluationWorker, get_evaluation_worker)
        if learner_runtime.evaluation_enabled():
            def _start_worker() -> None:
                get_evaluation_worker().start()
            await run_bootstrap_step(report, "evaluation_worker",
                                     _start_worker)
    except Exception:
        log.warning("evaluation worker not started", exc_info=True)

    # 回收站过期清扫不依赖浏览器打开：启动时先扫一次，之后进程内定时扫。
    cleanup_task = None
    try:
        from app.core.trash import get_global_policy

        async def _trash_cleanup_loop():
            while True:
                await asyncio.sleep(get_global_policy()["cleanup_interval_seconds"])
                try:
                    from app.core.trash import cleanup_expired
                    await asyncio.to_thread(cleanup_expired)
                except Exception:
                    log.warning("trash cleanup loop iteration failed",
                                exc_info=True)

        cleanup_task = asyncio.create_task(_trash_cleanup_loop())
    except Exception:
        log.warning("trash cleanup loop not started", exc_info=True)
        cleanup_task = None
    try:
        yield
    finally:
        # shutdown 类失败只 warning（plan.md §19），不再无痕。
        # R01：先停评价 worker（停止认领、等待在途租约、关闭共享 LLM 客户端）
        try:
            from app.agents.student_model.evaluation.worker import (
                get_evaluation_worker)
            await get_evaluation_worker().stop()
        except Exception:
            log.warning("shutdown: evaluation worker stop failed",
                        exc_info=True)
        try:
            from app.core.textbook_ocr import cancel_all_textbook_ocr
            cancel_all_textbook_ocr()
        except Exception:
            log.warning("shutdown: cancel_all_textbook_ocr failed", exc_info=True)
        try:
            from app.core.textbook import cancel_all_refresh_tasks
            cancel_all_refresh_tasks()
        except Exception:
            log.warning("shutdown: cancel_all_refresh_tasks failed",
                        exc_info=True)
        if cleanup_task is not None:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass


def _cors_origins() -> list[str]:
    """CORS allow-list from the CORS_ORIGINS env var (comma-separated)."""
    raw = os.environ.get("CORS_ORIGINS", "").strip()
    if not raw:
        return list(_DEFAULT_CORS_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip()]


async def _process_time_header(request: Request, call_next):
    """P0 可观测：每个响应带 X-Process-Time（毫秒）；>1s 的请求终端告警。

    用于定位"页面卡但不知道哪个端点慢"——浏览器 Network 面板与 start.sh
    终端都能直接看到慢端点，处理时间不含网络传输。"""
    import time as _time
    t0 = _time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (_time.perf_counter() - t0) * 1000
    response.headers["X-Process-Time"] = f"{elapsed_ms:.1f}"
    if elapsed_ms > 1000:
        print(f"[slow] {request.method} {request.url.path} -> "
              f"{response.status_code} {elapsed_ms:.0f}ms", flush=True)
    return response


def create_app() -> FastAPI:
    # P2-C（plan.md §36）：file-backed 业务状态 + 进程内锁只支持单 worker。
    # WEB_CONCURRENCY>1（uvicorn/gunicorn 常用扩展变量）会在多进程下产生
    # 并发写同一 JSON 的竞态——显式 fail-fast，而不是默默数据损坏。
    import os as _os
    try:
        _wc = int(_os.getenv("WEB_CONCURRENCY", "1") or "1")
    except ValueError:
        _wc = 1
    if _wc > 1:
        raise RuntimeError(
            "WEB_CONCURRENCY>1 is unsupported: file-backed persistence uses "
            "process-local locks — run exactly one uvicorn worker "
            "(deploy/edu-backend.service pins --workers 1).")
    # Fail fast on the insecure default JWT secret when login is enforced.
    from app.identity.config import ensure_secret_safety
    ensure_secret_safety()

    app = FastAPI(title="Next Tutor Agent API", version="0.2.0", lifespan=_lifespan)
    app.middleware("http")(_process_time_header)
    origins = _cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        # Browsers reject credentials with a "*" origin -- never combine them.
        allow_credentials=all(o != "*" for o in origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    from app.api.v1.router import api_router
    app.include_router(api_router)
    return app


app = create_app()
