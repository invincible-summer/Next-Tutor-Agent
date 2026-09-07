"""Runtime administrator policy for textbook background OCR.

Only textbook background OCR uses this module.  Chat/workspace uploads keep the
existing short multimodal retry -> local tesseract contract.

The process-global limiter is generation based: an active generation keeps its
page cap; a changed cap becomes effective after those jobs drain.  Retry/failure
settings are read at each retry round so an administrator can repair a provider
outage without restarting the service.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

from .atomic import atomic_write_text, file_lock
from .config import settings

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_POLICY_FILE = _PROJECT_ROOT / "chat_history" / "settings" / "ocr_policy.json"
_MIN = 1
_MAX = 100
_MIN_ATTEMPTS = 1
_MAX_ATTEMPTS = 100
_MIN_INTERVAL = 0
_MAX_INTERVAL = 3600
_MIN_TIMEOUT = 10
_MAX_TIMEOUT = 300
_MODES = {"persistent_api", "bounded_then_local", "bounded_api_only"}
_DEFAULT_MODE = "persistent_api"
_DEFAULT_ATTEMPTS = 3
# 重试间隔默认 10s：等待视觉模型响应可以很慢（timeout 单独可调），但两轮
# 重试之间不必等太久；管理员可按实例在 0–3600s 内覆盖（0=到点即重试，
# 实际节奏受构建队列轮询间隔限位）。
_DEFAULT_INTERVAL = 10
_DEFAULT_TIMEOUT = 60
T = TypeVar("T")


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _bootstrap_policy() -> dict[str, Any]:
    return {
        "concurrency": _clamp(getattr(settings, "pdf_ocr_concurrency", 20), _MIN, _MAX, 20),
        "failure_mode": _DEFAULT_MODE,
        "max_attempts": _DEFAULT_ATTEMPTS,
        "retry_interval_seconds": _DEFAULT_INTERVAL,
        "request_timeout_seconds": _DEFAULT_TIMEOUT,
        "updated_at": 0.0,
        "version": 2,
    }


def _normalize_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    base = _bootstrap_policy()
    data = raw if isinstance(raw, dict) else {}
    mode = str(data.get("failure_mode") or base["failure_mode"]).strip().lower()
    if mode not in _MODES:
        mode = _DEFAULT_MODE
    return {
        "concurrency": _clamp(data.get("concurrency"), _MIN, _MAX, base["concurrency"]),
        "failure_mode": mode,
        "max_attempts": _clamp(data.get("max_attempts"), _MIN_ATTEMPTS, _MAX_ATTEMPTS,
                                _DEFAULT_ATTEMPTS),
        "retry_interval_seconds": _clamp(
            data.get("retry_interval_seconds"), _MIN_INTERVAL, _MAX_INTERVAL,
            _DEFAULT_INTERVAL),
        "request_timeout_seconds": _clamp(
            data.get("request_timeout_seconds"), _MIN_TIMEOUT, _MAX_TIMEOUT,
            _DEFAULT_TIMEOUT),
        "updated_at": float(data.get("updated_at") or 0.0),
        "version": 2,
    }


def _read_policy() -> dict[str, Any]:
    try:
        return _normalize_policy(json.loads(_POLICY_FILE.read_text(encoding="utf-8")))
    except Exception:
        return _bootstrap_policy()


def _write_policy(policy: dict[str, Any]) -> None:
    _POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = _normalize_policy(policy)
    payload["updated_at"] = time.time()
    with file_lock(_POLICY_FILE):
        atomic_write_text(_POLICY_FILE, json.dumps(payload, ensure_ascii=False, indent=2))


@dataclass(frozen=True)
class OCRJob:
    generation: int
    limit: int


class _Runtime:
    def __init__(self) -> None:
        policy = _read_policy()
        self.policy = policy
        self.configured = int(policy["concurrency"])
        self.effective = self.configured
        self.pending: int | None = None
        self.generation = 1
        self.active_jobs: dict[int, int] = {}
        self.active_pages: dict[int, int] = {}
        self.waiting_pages = 0
        self.retry_waits: dict[str, tuple[int, float | None]] = {}
        self.condition: asyncio.Condition | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def _condition(self) -> asyncio.Condition:
        loop = asyncio.get_running_loop()
        if self.condition is None or self.loop is not loop:
            self.loop = loop
            self.condition = asyncio.Condition()
            self.active_jobs = {}
            self.active_pages = {}
            if self.pending is not None:
                self.effective = self.pending
                self.pending = None
                self.generation += 1
        return self.condition

    def retry_policy(self) -> dict[str, Any]:
        return {
            "failure_mode": self.policy["failure_mode"],
            "max_attempts": int(self.policy["max_attempts"]),
            "retry_interval_seconds": int(self.policy["retry_interval_seconds"]),
            "request_timeout_seconds": int(self.policy["request_timeout_seconds"]),
            "policy_version": int(self.policy["version"]),
            "policy_generation": self.generation,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "configured_concurrency": self.configured,
            "effective_concurrency": self.effective,
            "pending_concurrency": self.pending,
            **self.retry_policy(),
            "active_ocr_jobs": sum(self.active_jobs.values()),
            "active_ocr_pages": sum(self.active_pages.values()),
            "waiting_ocr_pages": self.waiting_pages,
            "retry_waiting_pages": sum(v[0] for v in self.retry_waits.values()),
            "next_retry_at": min((v[1] for v in self.retry_waits.values() if v[1]),
                                 default=None),
            "min_concurrency": _MIN,
            "max_concurrency": _MAX,
            "min_attempts": _MIN_ATTEMPTS,
            "max_attempts_limit": _MAX_ATTEMPTS,
            "min_retry_interval_seconds": _MIN_INTERVAL,
            "max_retry_interval_seconds": _MAX_INTERVAL,
            "min_request_timeout_seconds": _MIN_TIMEOUT,
            "max_request_timeout_seconds": _MAX_TIMEOUT,
            "generation": self.generation,
            "scope": "textbook_background_ocr_only",
        }

    async def set_policy(self, *, concurrency: int, failure_mode: str,
                         max_attempts: int, retry_interval_seconds: int,
                         request_timeout_seconds: int) -> dict[str, Any]:
        if concurrency < _MIN or concurrency > _MAX:
            raise ValueError(f"OCR concurrency must be between {_MIN} and {_MAX}")
        if failure_mode not in _MODES:
            raise ValueError(f"Unsupported OCR failure mode: {failure_mode}")
        if max_attempts < _MIN_ATTEMPTS or max_attempts > _MAX_ATTEMPTS:
            raise ValueError("OCR max_attempts out of range")
        if retry_interval_seconds < _MIN_INTERVAL or retry_interval_seconds > _MAX_INTERVAL:
            raise ValueError("OCR retry_interval_seconds out of range")
        if request_timeout_seconds < _MIN_TIMEOUT or request_timeout_seconds > _MAX_TIMEOUT:
            raise ValueError("OCR request_timeout_seconds out of range")
        new_policy = {
            "concurrency": concurrency,
            "failure_mode": failure_mode,
            "max_attempts": max_attempts,
            "retry_interval_seconds": retry_interval_seconds,
            "request_timeout_seconds": request_timeout_seconds,
            "updated_at": time.time(),
            "version": 2,
        }
        _write_policy(new_policy)
        self.policy = _normalize_policy(new_policy)
        self.configured = concurrency
        condition = self._condition()
        async with condition:
            if sum(self.active_jobs.values()) == 0:
                self.effective = concurrency
                self.pending = None
                self.generation += 1
            else:
                self.pending = concurrency
            condition.notify_all()
        return self.snapshot()

    async def begin_job(self) -> OCRJob:
        condition = self._condition()
        async with condition:
            while self.pending is not None and sum(self.active_jobs.values()) > 0:
                await condition.wait()
            if self.pending is not None:
                self.effective = self.pending
                self.pending = None
                self.generation += 1
            job = OCRJob(self.generation, self.effective)
            self.active_jobs[job.generation] = self.active_jobs.get(job.generation, 0) + 1
            return job

    async def end_job(self, job: OCRJob) -> None:
        condition = self._condition()
        async with condition:
            self.active_jobs[job.generation] = max(0, self.active_jobs.get(job.generation, 1) - 1)
            if not self.active_jobs[job.generation]:
                self.active_jobs.pop(job.generation, None)
            if sum(self.active_jobs.values()) == 0 and self.pending is not None:
                self.effective = self.pending
                self.pending = None
                self.generation += 1
            condition.notify_all()

    async def run_page(self, job: OCRJob, fn: Callable[[], Awaitable[T]]) -> T:
        condition = self._condition()
        async with condition:
            self.waiting_pages += 1
            try:
                while self.active_pages.get(job.generation, 0) >= job.limit:
                    await condition.wait()
                self.active_pages[job.generation] = self.active_pages.get(job.generation, 0) + 1
            finally:
                self.waiting_pages = max(0, self.waiting_pages - 1)
        try:
            return await fn()
        finally:
            async with condition:
                self.active_pages[job.generation] = max(
                    0, self.active_pages.get(job.generation, 1) - 1)
                if not self.active_pages[job.generation]:
                    self.active_pages.pop(job.generation, None)
                condition.notify_all()

    def set_retry_waiting(self, pages: int, next_retry_at: float | None,
                          key: str = "global") -> None:
        count = max(0, int(pages))
        if count <= 0:
            self.retry_waits.pop(key, None)
        else:
            self.retry_waits[key] = (
                count, float(next_retry_at) if next_retry_at else None)

    def clear_retry_waiting_prefix(self, prefix: str) -> None:
        for key in [k for k in self.retry_waits if k.startswith(prefix)]:
            self.retry_waits.pop(key, None)


_RUNTIME = _Runtime()


def get_policy() -> dict[str, Any]:
    return _RUNTIME.snapshot()


def get_retry_policy() -> dict[str, Any]:
    return _RUNTIME.retry_policy()


async def set_policy(concurrency: int, failure_mode: str = _DEFAULT_MODE,
                     max_attempts: int = _DEFAULT_ATTEMPTS,
                     retry_interval_seconds: int = _DEFAULT_INTERVAL,
                     request_timeout_seconds: int = _DEFAULT_TIMEOUT) -> dict[str, Any]:
    return await _RUNTIME.set_policy(
        concurrency=int(concurrency), failure_mode=str(failure_mode),
        max_attempts=int(max_attempts), retry_interval_seconds=int(retry_interval_seconds),
        request_timeout_seconds=int(request_timeout_seconds))


def set_retry_waiting(pages: int, next_retry_at: float | None,
                      key: str = "global") -> None:
    _RUNTIME.set_retry_waiting(pages, next_retry_at, key)


def clear_retry_waiting_prefix(prefix: str) -> None:
    _RUNTIME.clear_retry_waiting_prefix(prefix)


@asynccontextmanager
async def textbook_ocr_job() -> AsyncIterator[OCRJob]:
    job = await _RUNTIME.begin_job()
    try:
        yield job
    finally:
        await _RUNTIME.end_job(job)


async def run_page(job: OCRJob, fn: Callable[[], Awaitable[T]]) -> T:
    return await _RUNTIME.run_page(job, fn)
