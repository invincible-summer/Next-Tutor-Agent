"""原子写 + 进程内文件锁（持久化加固）。

全库 JSON 持久化此前的两个弱点：
  - `path.write_text(...)` 直接覆盖：崩溃/断电会留下半截 JSON，下次 load
    只能按「损坏即空」降级，等于丢状态。
  - load-modify-write 无并发保护：asyncio 线程池里两个并发请求可能交错
    读-改-写互相覆盖，JSONL append 也可能交错出半行。

这里提供两个最小原语（不引新依赖，uvicorn 单进程足够）：
  - atomic_write_text: 同目录 tmp 文件 + flush + os.fsync + os.replace。
    与 knowledge/custom store 既有的 tmp+replace 写法同款，补上了 fsync。
  - file_lock: 按路径字符串分键的 threading.RLock 字典（RLock 允许同线程
    重入，避免外层锁内调用内层加锁函数时自锁）。
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

PathLike = Union[str, Path]

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def atomic_write_text(path: PathLike, text: str, encoding: str = "utf-8") -> None:
    """原子写文本：同目录 tmp + flush + fsync + os.replace。

    replace 是单步原子操作，读者只会看到旧文件或新文件，不会看到半截。
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding=encoding) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextmanager
def file_lock(key: PathLike) -> Iterator[None]:
    """按路径字符串分键的进程内锁，保护 load-modify-write / append 临界区。"""
    k = str(key)
    with _locks_guard:
        lock = _locks.setdefault(k, threading.RLock())
    with lock:
        yield
