"""进程内滑动窗口限流（plan.md §11.3）。

单 worker 运行模型下进程内计数即可；不引入外部服务。
访客 guide 每 IP 每分钟 10 次；登录用户每分钟 20 次 turn 受理
（后者由 runtime 使用同一实现）。
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class SlidingWindowRateLimiter:
    def __init__(self, max_events: int, window_seconds: float) -> None:
        self._max_events = max_events
        self._window = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, now: float | None = None) -> tuple[bool, float]:
        """返回 (是否放行, 建议重试等待秒数)。"""
        current = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._events[key]
            cutoff = current - self._window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max_events:
                retry_after = self._window - (current - bucket[0])
                return False, max(retry_after, 0.1)
            bucket.append(current)
            return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
