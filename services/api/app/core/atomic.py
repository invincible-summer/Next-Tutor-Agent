"""Atomic writes and reentrant advisory coordination for file-backed domains.

Writes use a unique same-directory temporary file, fsync and atomic replace.
Critical sections use a canonical-key thread mutex plus an OS file lock;
only the outermost reentrant entry opens/locks a descriptor. Coordination
files live under the runtime data root, contain no user data, and must not
be removed while any process is running. Process-local caches still need
explicit invalidation; this primitive is not a database migration.
"""
from __future__ import annotations

import os
import hashlib
import threading
import uuid
import weakref
from contextlib import contextmanager
from pathlib import Path

from app.core.paths import bind_storage_path
from typing import Iterator, Union

PathLike = Union[str, Path]

# Global opaque coordination metadata; no account data and no per-user root.
# Never unlink a live lock inode: replacement would split the lock between writers.
_LOCK_DIR = bind_storage_path(__name__, "_LOCK_DIR", "root", "coordination/locks")


class _FileMutex:
    def __init__(self) -> None:
        self.thread_lock = threading.RLock()
        self.depth = 0
        self.fd: int | None = None


_locks: weakref.WeakValueDictionary[str, _FileMutex] = weakref.WeakValueDictionary()
_locks_guard = threading.Lock()


def _lock_descriptor(fd: int, blocking: bool) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))


def _unlock_descriptor(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def _after_fork() -> None:
    global _locks_guard
    for inherited in list(_locks.values()):
        if inherited.fd is not None:
            os.close(inherited.fd)  # Close only; unlocking would release the parent's lock.
            inherited.fd = None
    _locks.clear()
    _locks_guard = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


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
    """原子写二进制（音频/ZIP 等）：同目录 tmp + fsync + replace。"""
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
def file_lock(key: PathLike, *, blocking: bool = True) -> Iterator[None]:
    """Serialize a critical section across threads/processes on one shared filesystem.

    Nesting in one thread reuses the outer descriptor. OS locks are advisory:
    all writers must use the same key, and their shared filesystem must support
    locking. This primitive does not make process-local business caches shared.
    """
    k = os.path.normcase(str(Path(key).expanduser().resolve()))
    with _locks_guard:
        lock = _locks.get(k)
        if lock is None:
            lock = _FileMutex()
            _locks[k] = lock
    acquired = lock.thread_lock.acquire(blocking=blocking)
    if not acquired:
        raise BlockingIOError("coordination lock already held")
    try:
        if lock.depth == 0:
            _LOCK_DIR.mkdir(parents=True, exist_ok=True)
            target = _LOCK_DIR / (hashlib.sha256(k.encode("utf-8")).hexdigest() + ".lock")
            fd = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                if os.name == "nt" and os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                _lock_descriptor(fd, blocking)
            except BaseException:
                os.close(fd)
                raise
            lock.fd = fd
        lock.depth += 1
        try:
            yield
        finally:
            lock.depth -= 1
            if lock.depth == 0 and lock.fd is not None:
                fd, lock.fd = lock.fd, None
                try:
                    _unlock_descriptor(fd)
                finally:
                    os.close(fd)
    finally:
        lock.thread_lock.release()


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
    """受测的 journal append 原语。

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
