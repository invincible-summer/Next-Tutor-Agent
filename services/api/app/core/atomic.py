"""原子写 + 进程内文件锁（持久化加固）。

全库 JSON 持久化此前的两个弱点：
  - `path.write_text(...)` 直接覆盖：崩溃/断电会留下半截 JSON，下次 load
    只能按「损坏即空」降级，等于丢状态。
  - load-modify-write 无并发保护：asyncio 线程池里两个并发请求可能交错
    读-改-写互相覆盖，JSONL append 也可能交错出半行。

这里提供两个最小原语（不引新依赖，uvicorn 单进程足够）：
  - atomic_write_text: 同目录唯一命名 tmp 文件 + flush + os.fsync +
    os.replace + fsync 目录。tmp 唯一命名保证即使两个调用方并发写同一
    目标（上层锁缺失/失效时）也不会交叉写同一个 tmp 损坏内容。
  - file_lock: 按路径字符串分键的 threading.RLock（RLock 允许同线程
    重入，避免外层锁内调用内层加锁函数时自锁）。锁对象以弱引用登记：
    没有等待者/持有者时条目自动回收，长寿命进程不再无界增长。
"""
from __future__ import annotations

import os
import threading
import uuid
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

PathLike = Union[str, Path]

# WeakValueDictionary：file_lock 在临界区内始终持局部强引用，条目在
# 最后一个使用者退出后自动消失。并发安全性：任意线程从 get 到 acquire
# 之间都持着同一对象的强引用，字典条目不会在使用中途消失，也不会出现
# 两个线程各拿一把"同路径不同对象"的锁。
_locks: "weakref.WeakValueDictionary[str, threading.RLock]" = (
    weakref.WeakValueDictionary())
_locks_guard = threading.Lock()


def _tmp_path(path: Path) -> Path:
    """同目录且全局唯一的临时名：并发写同一目标也不会互相打开同一文件。"""
    return path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}"
        f".{uuid.uuid4().hex[:8]}")


def atomic_write_text(path: PathLike, text: str, encoding: str = "utf-8") -> None:
    """原子写文本：同目录 tmp + flush + fsync + os.replace + fsync 目录。

    replace 是单步原子操作，读者只会看到旧文件或新文件，不会看到半截。
    """
    path = Path(path)
    tmp = _tmp_path(path)
    try:
        with tmp.open("w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        # 正常路径下 replace 已把 tmp 挪走；异常路径不留残片。
        tmp.unlink(missing_ok=True)
    fsync_dir(path.parent)


def atomic_write_bytes(path: PathLike, data: bytes) -> None:
    """原子写二进制（音频/ZIP 等，plan.md §16.2）：同目录 tmp + fsync + replace。"""
    path = Path(path)
    tmp = _tmp_path(path)
    try:
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    fsync_dir(path.parent)


@contextmanager
def file_lock(key: PathLike) -> Iterator[None]:
    """按路径字符串分键的进程内锁，保护 load-modify-write / append 临界区。"""
    k = str(key)
    with _locks_guard:
        lock = _locks.get(k)
        if lock is None:
            lock = threading.RLock()
            _locks[k] = lock
    with lock:
        yield


def fsync_dir(path: PathLike) -> None:
    """同步父目录条目（创建/替换文件的崩溃恢复承诺，§6.5）。"""
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        pass  # 某些文件系统不支持目录 fsync
    finally:
        os.close(fd)


def append_line_sync(path: PathLike, line: str, encoding: str = "utf-8") -> bool:
    """受测的 journal append 原语（plan §6.5/§15.1）。

    在调用方持有的 file_lock 内：追加完整一行（含换行）、flush、fsync 文件；
    文件本次新建时同步父目录。短临界区、无 await；写失败向上抛 OSError，
    由 service 报可见错误（禁止 except-pass，§17.1）。
    """
    path = Path(path)
    created = not path.exists()
    if created:
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding=encoding, newline="") as f:
        f.write(line)
        if not line.endswith("\n"):
            f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    if created:
        fsync_dir(path.parent)
    return created
