"""FastAPI application factory (adapted from Paper_Agent)."""
from __future__ import annotations

import asyncio
import logging
import threading
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import __version__

log = logging.getLogger(__name__)

# Dev-friendly default when CORS_ORIGINS is not set (local Next.js dev server).
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000", "http://127.0.0.1:3000", "http://0.0.0.0:3000",
    "http://localhost:3001", "http://127.0.0.1:3001", "http://0.0.0.0:3001",
    "http://localhost:3030", "http://127.0.0.1:3030", "http://0.0.0.0:3030",
]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # P1-C：结构化 bootstrap——每个启动维护步骤都有
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
    # （P1-B 顺序）。durable 模式（ADR-0013）下对账/续跑/重入队归 worker
    # 进程（worker.py 启动时执行）；API 进程只做一次性 legacy 迁移，
    # 绝不在两个进程重复驱动构建。
    async def _textbook_recovery() -> None:
        from app.core.textbook import migrate_legacy_single_to_groups
        migrate_legacy_single_to_groups()
        from app.workflows.config import temporal_configured
        if temporal_configured():
            return
        from app.core.textbook import reconcile_stale_builds
        from app.core.textbook_ocr import resume_pending_textbook_ocr
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

    # 企业持久化探活：DATABASE_URL 未配置（文件模式）恒 ok；企业模式下
    # 数据库不可达必须 fail-fast（critical），Redis 不可达仅记录（非事实源）。
    async def _persistence_probe() -> dict:
        from app.persistence.health import probe_persistence
        return await probe_persistence()

    from app.persistence import db as persistence_db
    await run_bootstrap_step(report, "persistence", _persistence_probe,
                             critical=persistence_db.enterprise_mode())

    # 可观测性：OTel lazy 装配（OTEL_TRACES_ENABLED=1 且观测 lane 已安装
    # 才生效；缺包/不可达一律降级 no-op，不影响服务）。
    async def _init_observability() -> None:
        from app.observability import tracing
        tracing.init_tracing(app)

    await run_bootstrap_step(report, "observability", _init_observability)

    # 保留启动报告步骤；学生模型仅在登录账号首次使用时加载。
    def _warm_default_student_model() -> None:
        # Guests have no persistent student model. Registered models load lazily.
        return

    def _trash_cleanup_once() -> None:
        from app.core.trash import cleanup_expired
        cleanup_expired()

    # 插图清洗器依赖（defusedxml 等）缺失时所有 SVG 都 fail-closed，症状只
    # 表现为远端配图失败；启动即跑一次最小规范化，让缺失直接变成 not_ready。
    def _sanitizer_dependency() -> None:
        from app.core.quiz_illustration import normalize_svg
        normalize_svg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200">'
                      '<line x1="10" y1="10" x2="300" y2="190"/></svg>')

    await run_bootstrap_step(report, "sanitizer_dependency",
                             _sanitizer_dependency, critical=True,
                             to_thread=True)
    await run_bootstrap_step(report, "legacy_graph_cleanup",
                             _legacy_graph_cleanup)
    await run_bootstrap_step(report, "textbook_recovery", _textbook_recovery)
    await run_bootstrap_step(report, "admin_bootstrap", _admin_bootstrap,
                             to_thread=True, critical=True)

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
        from app.workflows.config import temporal_configured
        if learner_runtime.evaluation_enabled():
            if temporal_configured():
                # durable lane（ADR-0013 C4）：API 进程不再持有评价 worker/
                # planner；只确保监督 workflow 存在。执行/恢复/outbox 都在
                # worker 进程的 EvaluationWorker 内（journal claimable 语义
                # 覆盖重启恢复），notify_evaluation_worker 在 API 进程 no-op，
                # 新作业由 worker 的空闲轮询认领。
                async def _ensure_evaluation_supervisor() -> None:
                    from app.workflows import evaluation as wf_evaluation
                    await asyncio.wait_for(
                        wf_evaluation.ensure_supervisor(), timeout=10)
                await run_bootstrap_step(report, "evaluation_worker",
                                         _ensure_evaluation_supervisor)
            else:
                async def _start_worker() -> None:
                    # run_bootstrap_step 会 await 步骤函数；asyncio.create_task
                    # 需在运行中的事件循环内执行（lifespan 线程即循环线程）
                    get_evaluation_worker().start()

                async def _start_planner() -> None:
                    from app.agents.student_model.evaluation.schedule import (
                        get_daily_planner)
                    get_daily_planner().start()
                await run_bootstrap_step(report, "evaluation_worker",
                                         _start_worker)
                await run_bootstrap_step(report, "evaluation_daily_planner",
                                         _start_planner)
    except Exception:
        log.warning("evaluation worker not started", exc_info=True)

    # 回收站过期清扫不依赖浏览器打开：启动时先扫一次，之后进程内定时扫。
    cleanup_task = None
    # 课堂生成 worker：恢复未终结 job + 受限调度。
    # classroom 关闭时不启动（无新 job；旧 job 留在磁盘等下次开启）。
    classroom_worker = None
    try:
        from app.core.config import settings
        if settings.classroom_enabled:
            from app.classroom import service as classroom_service
            from app.workflows.config import temporal_configured
            if temporal_configured():
                # durable lane（ADR-0013 C2）：API 进程不再持有 classroom
                # worker；只确保监督 workflow 存在。执行/恢复/收养都在
                # worker 进程的 ClassroomWorker（adopt 模式）内，API 的
                # enqueue 钩子保持 None（queued job 落盘后 ≤2s 被收养）。
                async def _ensure_classroom_supervisor() -> None:
                    from app.workflows import classroom as wf_classroom
                    classroom_service.enqueue_job = None
                    await asyncio.wait_for(wf_classroom.ensure_supervisor(),
                                           timeout=10)

                await run_bootstrap_step(report, "classroom_worker",
                                         _ensure_classroom_supervisor)
            else:
                from app.classroom.worker import get_worker

                async def _start_classroom_worker() -> None:
                    worker = get_worker()
                    await worker.start()
                    classroom_service.enqueue_job = worker.enqueue

                async def _stop_classroom_worker() -> None:
                    await get_worker().stop()
                    classroom_service.enqueue_job = None

                await run_bootstrap_step(report, "classroom_worker",
                                         _start_classroom_worker)
                classroom_worker = _stop_classroom_worker
    except Exception:
        log.warning("classroom worker not started", exc_info=True)

    # 课堂云端 TTS voices list 预热（阶段 F）：后台 best-effort 刷新缓存，
    # 失败只记 degraded；GET capability 永不发网络请求。
    voices_task = None
    try:
        from app.core.config import settings

        async def _refresh_classroom_voices() -> None:
            from app.voice.tts import service as tts_service
            await tts_service.refresh_voices(force=True)

        if settings.classroom_enabled and settings.azure_speech_key:
            voices_task = asyncio.create_task(_refresh_classroom_voices())
    except Exception:
        log.warning("classroom voices prefetch not started", exc_info=True)
        voices_task = None
    try:
        from app.core.trash import get_global_policy
        from app.workflows.config import temporal_configured

        async def _trash_cleanup_loop():
            while True:
                await asyncio.sleep(get_global_policy()["cleanup_interval_seconds"])
                try:
                    from app.core.trash import cleanup_expired
                    await asyncio.to_thread(cleanup_expired)
                except Exception:
                    log.warning("trash cleanup loop iteration failed",
                                exc_info=True)

        # durable lane（ADR-0013 C5）：定时清扫由 maintenance Schedule 持有
        # （worker 启动幂等注册）；API 进程不再起循环。
        cleanup_task = None if temporal_configured() else \
            asyncio.create_task(_trash_cleanup_loop())
    except Exception:
        log.warning("trash cleanup loop not started", exc_info=True)
        cleanup_task = None

    # 站内学习助手：开关关闭时不启动后台任务。
    assistant_runtime = None
    assistant_draft_task = None
    try:
        from app.core.config import settings

        if settings.site_assistant_enabled:
            from app.agents.site_assistant.runtime import get_runtime

            async def _start_assistant() -> None:
                await get_runtime().start()

            await run_bootstrap_step(report, "assistant_runtime",
                                     _start_assistant)
            assistant_runtime = get_runtime()

            async def _assistant_draft_purge_loop() -> None:
                # 启动清一次，此后每小时一次（§12.3-5）。
                from app.agents.site_assistant import store as asst_store
                while True:
                    try:
                        if asst_store._ASSISTANT_DIR.is_dir():
                            for d in asst_store._ASSISTANT_DIR.iterdir():
                                if d.is_dir():
                                    await asyncio.to_thread(
                                        asst_store.purge_expired_drafts,
                                        d.name)
                    except Exception:
                        log.debug("assistant draft purge iteration failed",
                                  exc_info=True)
                    await asyncio.sleep(3600.0)

            # durable lane（ADR-0013 C5）：草稿清扫由 maintenance Schedule
            # 持有；API 进程不再起循环（runtime 的 turns/恢复不受影响）。
            from app.workflows.config import temporal_configured
            assistant_draft_task = None if temporal_configured() else \
                asyncio.create_task(_assistant_draft_purge_loop())
    except Exception:
        log.warning("assistant runtime not started", exc_info=True)
        assistant_runtime = None
    from app.core.guest_runtime import sweep_loop, purge_all as purge_guests
    guest_sweep_task = asyncio.create_task(sweep_loop())
    try:
        yield
    finally:
        purge_guests()
        # OTel span flush（未启用时为 no-op）。
        try:
            from app.observability import tracing
            tracing.shutdown_tracing()
        except Exception:
            log.warning("shutdown: tracing flush failed", exc_info=True)
        # 企业持久化：关闭连接池（文件模式下为 no-op）。
        try:
            from app.persistence import db as persistence_db
            await persistence_db.dispose_engine_async()
        except Exception:
            log.warning("shutdown: persistence engine dispose failed",
                        exc_info=True)
        guest_sweep_task.cancel()
        try:
            await guest_sweep_task
        except asyncio.CancelledError:
            pass
        # shutdown 类失败只 warning，不再无痕。
        # 课堂 worker：先停调度（≤10s 检查点宽限），再走其余清理
        if classroom_worker is not None:
            try:
                await classroom_worker()
            except Exception:
                log.warning("shutdown: classroom worker stop failed",
                            exc_info=True)
        # R01：先停评价 worker（停止认领、等待在途租约、关闭共享 LLM 客户端）。
        # durable 模式下两者在 worker 进程，API 进程无实例可停。
        from app.workflows.config import temporal_configured
        if not temporal_configured():
            try:
                from app.agents.student_model.evaluation.worker import (
                    get_evaluation_worker)
                await get_evaluation_worker().stop()
            except Exception:
                log.warning("shutdown: evaluation worker stop failed",
                            exc_info=True)
            try:
                from app.agents.student_model.evaluation.schedule import (
                    get_daily_planner)
                await get_daily_planner().stop()
            except Exception:
                log.warning("shutdown: daily planner stop failed",
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
        # 站内助手：停机取消未完成模型请求，尽力写 interrupted（§12.2）。
        if assistant_draft_task is not None:
            assistant_draft_task.cancel()
            try:
                await assistant_draft_task
            except asyncio.CancelledError:
                pass
        if assistant_runtime is not None:
            try:
                await assistant_runtime.stop()
            except Exception:
                log.warning("shutdown: assistant runtime stop failed",
                            exc_info=True)
        if voices_task is not None:
            voices_task.cancel()
            try:
                await voices_task
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
    # P2-C：file-backed 业务状态 + 进程内锁只支持单 worker。
    # WEB_CONCURRENCY>1（uvicorn/gunicorn 常用扩展变量）会在多进程下产生
    # 并发写同一 JSON 的竞态——显式 fail-fast，而不是默默数据损坏。
    # PostgreSQL currently owns identity/session primitives; learning domains
    # still use files and process-local locks. DATABASE_URL cannot lift this gate.
    import os as _os
    try:
        _wc = int(_os.getenv("WEB_CONCURRENCY", "1") or "1")
    except ValueError as error:
        raise RuntimeError("WEB_CONCURRENCY must be the integer 1") from error
    if _wc != 1:
        raise RuntimeError(
            "WEB_CONCURRENCY must be 1: learning-domain persistence still uses "
            "files and process-local locks, including when DATABASE_URL is set. "
            "Run exactly one API instance with one uvicorn worker until all "
            "domain repositories and guest coordination have completed cutover.")
    # Fail fast on the insecure default JWT secret when login is enforced.
    from app.identity.config import ensure_secret_safety
    ensure_secret_safety()

    # 生产（AUTH_MODE=1）关闭交互文档：/docs、/redoc、/openapi.json 会向
    # 匿名访客完整暴露 API 面（含 admin/trace 端点描述）。开发模式保留。
    _production = _os.getenv("AUTH_MODE", "0") == "1"
    app = FastAPI(
        title="Next Tutor Agent API", version=__version__, lifespan=_lifespan,
        docs_url=None if _production else "/docs",
        redoc_url=None if _production else "/redoc",
        openapi_url=None if _production else "/openapi.json",
    )
    app.middleware("http")(_process_time_header)
    # X-Request-ID 进出贯通（§17.3 跨端关联）：客户端可带 id，服务端回显同值。
    from app.observability.request_id import RequestIdMiddleware
    app.add_middleware(RequestIdMiddleware)
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
    # 课堂域统一错误 envelope；存储强制写失败（磁盘满/
    # 权限）与损坏课程同样投影为可观察 envelope（J03，§16.2）
    from app.api.v1.classroom import (classroom_exception_handler,
                                      storage_exception_handler)
    from app.classroom.errors import ClassroomError
    from app.classroom.storage import (ClassroomStorageError,
                                          LessonDamagedError)
    app.add_exception_handler(ClassroomError, classroom_exception_handler)
    app.add_exception_handler(ClassroomStorageError,
                              storage_exception_handler)
    app.add_exception_handler(LessonDamagedError,
                              storage_exception_handler)
    return app


app = create_app()
