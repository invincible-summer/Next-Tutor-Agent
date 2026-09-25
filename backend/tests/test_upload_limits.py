"""Contract tests: 统一 Upload 限流读取（plan.md §32-§35, Phase 1）。

当前行为（必须先失败证明 gap）：
  各上传端点 `raw = await f.read()` 之后才判断大小——超限文件会先被完整
  读进内存。

目标行为：
  core/uploads.read_upload_limited 按 chunk 读取，超过 max_bytes 即刻抛
  UploadTooLarge，总读取量不超过 limit + chunk_size。

Phase 6 实现后全绿。
"""
from __future__ import annotations

import asyncio
import unittest

from tests.storage_sandbox import StorageSandboxTestCase


class FakeUploadFile:
    """最小 UploadFile 替身：记录读取次数与累计读取字节。"""

    def __init__(self, data: bytes, chunk_size: int | None = None):
        self._data = data
        self._pos = 0
        self.read_calls = 0
        self.bytes_read = 0
        self._chunk_size = chunk_size  # None = 模拟无参 read()

    async def read(self, size: int = -1):
        self.read_calls += 1
        if size is None or size < 0:
            chunk = self._data[self._pos:]
        else:
            chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        self.bytes_read += len(chunk)
        return chunk


class TestUploadLimitedReaderContract(StorageSandboxTestCase):

    _CHUNK = 1024

    def test_module_exports(self):
        from app.core.uploads import UploadTooLarge, read_upload_limited
        self.assertTrue(callable(read_upload_limited))

    def test_small_file_reads_fully(self):
        from app.core.uploads import read_upload_limited
        data = b"x" * (512 * 1024)
        upload = FakeUploadFile(data)
        out = asyncio.run(read_upload_limited(upload, 1024 * 1024,
                                              chunk_size=self._CHUNK))
        self.assertEqual(out, data)

    def test_exactly_limit_accepted(self):
        from app.core.uploads import read_upload_limited
        limit = 1024 * 1024
        data = b"y" * limit
        upload = FakeUploadFile(data)
        out = asyncio.run(read_upload_limited(upload, limit,
                                              chunk_size=self._CHUNK))
        self.assertEqual(len(out), limit)

    def test_over_limit_stops_early(self):
        """超限文件在 limit + chunk_size 左右就终止，不读完整攻击文件。"""
        from app.core.uploads import UploadTooLarge, read_upload_limited
        limit = 1024 * 1024
        data = b"z" * (limit * 4)  # 4 倍超限
        upload = FakeUploadFile(data)
        with self.assertRaises(UploadTooLarge):
            asyncio.run(read_upload_limited(upload, limit,
                                            chunk_size=self._CHUNK))
        self.assertLessEqual(upload.bytes_read, limit + self._CHUNK,
                             "超限后必须提前停止读取")
        self.assertLess(upload.bytes_read, len(data),
                        "绝不能把整个超限文件读完")
        self.assertGreaterEqual(upload.read_calls, 2, "必须是分块读取")

    def test_limit_plus_one_rejected(self):
        from app.core.uploads import UploadTooLarge, read_upload_limited
        limit = 1024
        upload = FakeUploadFile(b"a" * (limit + 1))
        with self.assertRaises(UploadTooLarge):
            asyncio.run(read_upload_limited(upload, limit,
                                            chunk_size=self._CHUNK))

    def test_upload_too_large_carries_limit(self):
        from app.core.uploads import UploadTooLarge
        exc = UploadTooLarge(123)
        self.assertEqual(exc.limit, 123)
        self.assertIn("123", str(exc))


if __name__ == "__main__":
    unittest.main()
