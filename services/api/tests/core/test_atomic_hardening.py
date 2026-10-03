"""安全加固回归（批次2）：atomic 原语、notes 写竞态、trace 反向索引。

  1. atomic_write_text 并发写同一目标：最终内容是完整的二者之一，
     绝无交叉损坏（tmp 唯一命名）。
  2. file_lock 弱引用回收：无人使用后锁表不驻留；并发下互斥仍成立。
  3. notes._write_content 持锁：并发保存同一笔记不损坏内容。
  4. trace_owner_index：run_id→属主索引正确，会话文件变更后缓存失效。

均运行在 storage sandbox 内，不触碰生产存储根。
"""
import json
import sys
import threading
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from tests.support.storage_sandbox import StorageSandboxTestCase  # noqa: E402
from app.core.atomic import atomic_write_text, file_lock, _locks  # noqa: E402


class TestAtomicWriteConcurrency(unittest.TestCase):
    def test_concurrent_writes_never_interleave(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "shared.json"
            payload_a = json.dumps({"who": "a", "pad": "A" * 4096})
            payload_b = json.dumps({"who": "b", "pad": "B" * 4096})
            barrier = threading.Barrier(8)
            errors: list[Exception] = []

            def writer(payload: str) -> None:
                try:
                    barrier.wait()
                    for _ in range(25):
                        atomic_write_text(target, payload)
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            threads = [threading.Thread(target=writer,
                                        args=(payload_a if i % 2 else payload_b,))
                       for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            # 最终内容必须是完整的二者之一（可解析且 pad 段无交叉）。
            final = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn(final["who"], ("a", "b"))
            expected_pad = "A" * 4096 if final["who"] == "a" else "B" * 4096
            self.assertEqual(final["pad"], expected_pad)
            # 不留 tmp 残片。
            leftovers = list(target.parent.glob(target.name + ".tmp*"))
            self.assertEqual(leftovers, [])

    def test_failed_write_leaves_no_tmp_and_old_content(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "state.json"
            atomic_write_text(target, '{"v": 1}')
            with self.assertRaises(TypeError):
                atomic_write_text(target, {"not": "a string"})  # type: ignore[arg-type]
            self.assertEqual(target.read_text(encoding="utf-8"), '{"v": 1}')
            self.assertEqual(list(target.parent.glob("*.tmp*")), [])


class TestFileLockLifecycle(unittest.TestCase):
    def test_lock_table_does_not_leak(self):
        import gc
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            keys = [str(Path(d) / f"file{i}.json") for i in range(64)]
            for k in keys:
                with file_lock(k):
                    pass  # 退出后局部强引用消失
            gc.collect()
            # 全部条目应已被弱引用回收（未驻留）。
            self.assertEqual(len(_locks), 0)

    def test_lock_still_mutually_excludes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            key = str(Path(d) / "counter")
            counter = {"n": 0}
            barrier = threading.Barrier(8)

            def worker() -> None:
                barrier.wait()
                for _ in range(200):
                    with file_lock(key):
                        n = counter["n"]
                        # 没有互斥的话，read-modify-write 会丢失更新。
                        counter["n"] = n + 1

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(counter["n"], 8 * 200)

    def test_reentrant_within_same_thread(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            key = str(Path(d) / "nested")
            with file_lock(key):
                with file_lock(key):  # RLock：同线程重入不死锁
                    pass


class TestNotesConcurrentWrite(StorageSandboxTestCase):
    def test_concurrent_write_content_intact(self):
        from app.notes import NoteVault
        vault = NoteVault("usr_atomic_notes")
        meta = vault.create_note(title="并发笔记", content="初始内容")
        note_id = meta["id"]
        barrier = threading.Barrier(4)
        errors: list[Exception] = []

        def saver(i: int) -> None:
            try:
                barrier.wait()
                for turn in range(15):
                    vault._write_content(note_id, f"写入方{i}第{turn}轮\n" + "x" * 2048)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=saver, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        final = vault._read_content(note_id)
        # 内容是某一写入方的完整输出（无交叉/截断损坏）。
        self.assertIn("写入方", final)
        self.assertTrue(final.endswith("x" * 2048), "content corrupted")


class TestTraceOwnerIndex(StorageSandboxTestCase):
    def _write_session(self, sid: str, owner: str, trace_ids: list[str]):
        from app.core import session as session_mod
        d = {"session_id": sid, "title": sid, "messages": [],
             "student_id": owner, "trace_ids": trace_ids}
        (session_mod._SESSIONS_DIR / f"{sid}.json").write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def test_index_and_invalidation(self):
        from app.core import session as session_mod
        from app.core.session import trace_owner_index
        self._write_session("s_idx_a", "usr_owner1", ["run_1", "run_2"])
        self._write_session("s_idx_b", "", ["run_3"])  # 无戳 → default
        idx = trace_owner_index("student_default")
        self.assertEqual(idx.get("run_1"), "usr_owner1")
        self.assertEqual(idx.get("run_2"), "usr_owner1")
        self.assertEqual(idx.get("run_3"), "student_default")
        self.assertIsNone(idx.get("run_missing"))
        # 会话文件变更（会话转写他人名下）后缓存失效、归属更新。
        self._write_session("s_idx_a", "usr_owner2", ["run_1", "run_2"])
        idx = trace_owner_index("student_default")
        self.assertEqual(idx.get("run_1"), "usr_owner2")
        # 删除会话后条目消失。
        (session_mod._SESSIONS_DIR / "s_idx_a.json").unlink()
        idx = trace_owner_index("student_default")
        self.assertIsNone(idx.get("run_1"))

    def test_authorize_trace_semantics_unchanged(self):
        """授权语义回归：属主可见、他人 404、未认领 trace 仅 admin。"""
        from app.identity import store as id_store
        from app.identity.security import hash_password
        from app.api.v1.trace import _authorize_trace
        from fastapi import HTTPException
        victim = id_store.create_user("trvictim@example.com", "",
                                      hash_password("secret123"),
                                      user_id="usr_tr_victim")
        other = id_store.create_user("trother@example.com", "",
                                     hash_password("secret123"))
        admin = id_store.create_user("tradmin@example.com", "",
                                     hash_password("secret123"),
                                     role="admin")
        self._write_session("s_tr_own", victim.id, ["run_tr_1"])
        _authorize_trace("run_tr_1", victim.id)  # 属主放行
        with self.assertRaises(HTTPException) as cm:
            _authorize_trace("run_tr_1", other.id)
        self.assertEqual(cm.exception.status_code, 404)
        _authorize_trace("run_unclaimed", admin.id)  # admin 兜底
        with self.assertRaises(HTTPException):
            _authorize_trace("run_unclaimed", other.id)


if __name__ == "__main__":
    unittest.main()
