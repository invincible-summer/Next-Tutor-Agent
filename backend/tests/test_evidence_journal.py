"""G1 回归：学习证据 journal（plan §6.4/§6.5 / §18.2 test_evidence_journal）。

覆盖：原子受理、append fsync（完整行+换行）、尾损坏恢复（不完整行/坏
校验和的最后一行）、中部损坏保护、缓存删除后重放相同、generation 切换
重写、同来源版本唯一当前解释。
"""
from __future__ import annotations

import json

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation import store as st

SID = "usr_journal_a"


def _receipt(source_id: str = "src_1", revision: int = 1,
             text: str = "学生原始作答") -> S.SourceReceipt:
    return S.SourceReceipt(
        source_id=source_id, source_revision=revision,
        kind=S.SourceKind.ASSESSMENT, observed_at="2026-09-13T08:00:00Z",
        workspace_id_at_observation="ws_1", canonical_text=text,
        source_session_ref="chat_1", scope_revision="scope_1")


def _job(job_id: str = "job_1", source_id: str = "src_1") -> S.EvaluationJob:
    return S.EvaluationJob(
        job_id=job_id, kind=S.JobKind.ASSESSMENT_EVALUATION,
        source_id=source_id, workspace_id="ws_1", scope_revision="scope_1",
        created_at=S.utc_now_iso(), updated_at=S.utc_now_iso())


class TestEvidenceJournal(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        st.reset_journal_cache()
        self.journal = st.get_journal(SID)

    def _lines(self) -> list[str]:
        raw = st.journal_path(SID).read_text(encoding="utf-8")
        return [l for l in raw.split("\n") if l]

    # -- 原子受理 -------------------------------------------------------
    def test_source_and_job_single_transaction(self):
        tx = self.journal.register_source(_receipt(), job=_job())
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(st.journal_path(SID).read_bytes().endswith(b"\n"))
        parsed = S.JournalTransaction.model_validate_json(lines[0])
        self.assertTrue(parsed.verify_checksum())
        self.assertEqual(parsed.seq, tx.seq)
        state = self.journal.state()
        self.assertIn("src_1", state.sources)
        self.assertIn("job_1", state.jobs)
        self.assertEqual(state.watermark, f"{state.generation}:{tx.seq}")

    def test_empty_transaction_rejected(self):
        with self.assertRaises(st.JournalError):
            self.journal.append([])

    # -- 重放一致 -------------------------------------------------------
    def test_replay_after_cache_invalidation_identical(self):
        self.journal.register_source(_receipt())
        self.journal.append([S.OpConsumerAck(event_id="ev1", consumer="m9")])
        before = self.journal.state()
        watermark = before.watermark
        st.reset_journal_cache()
        after = st.get_journal(SID).state()
        self.assertEqual(after.watermark, watermark)
        self.assertEqual(sorted(after.sources), sorted(before.sources))
        self.assertEqual(after.generation, before.generation)

    def test_duplicate_result_commit_replaces_current_interpretation(self):
        """同一来源版本重复投递只有一次效果（§6.5）。"""
        self.journal.register_source(_receipt())
        interp = dict(applicable=True, observation_claims=[],
                      concept_updates=[], feedback="v1")
        self.journal.append([S.OpResultCommitted(
            job_id="job_1", source_id="src_1", source_revision=1,
            scope_revision="scope_1", interpretation_id="itp_1",
            interpretation=interp)])
        interp2 = dict(interp, feedback="v2")
        self.journal.append([S.OpResultCommitted(
            job_id="job_2", source_id="src_1", source_revision=1,
            scope_revision="scope_1", interpretation_id="itp_2",
            interpretation=interp2)])
        src = self.journal.state().sources["src_1"]
        self.assertEqual(src.current_interpretation_id, "itp_2")
        self.assertEqual(len(src.interpretations), 2)  # 历史保留可审计

    # -- 尾损坏恢复 -----------------------------------------------------
    def test_incomplete_tail_line_truncated_and_recovered(self):
        self.journal.register_source(_receipt())
        # 模拟崩溃：半行写入（无换行、非法 JSON）
        with st.journal_path(SID).open("a", encoding="utf-8") as f:
            f.write('{"schema_version": 1, "seq": 2, "operations": [ truncated')
        st.reset_journal_cache()
        journal = st.get_journal(SID)
        state = journal.state()
        self.assertEqual(len(state.sources), 1)   # 半行被隔离
        self.assertEqual(state.last_seq, 1)
        # 后续写入正常继续（seq 不回退）
        tx = journal.append([S.OpConsumerAck(event_id="ev1", consumer="m9")])
        self.assertEqual(tx.seq, 2)

    def test_bad_checksum_tail_line_truncated(self):
        self.journal.register_source(_receipt())
        # 完整换行的最后一行但校验和被篡改 → 尾事务隔离截去
        tx = self.journal.register_source(_receipt("src_2"))
        lines = self._lines()
        tampered = json.loads(lines[-1])
        tampered["seq"] = 999
        lines[-1] = json.dumps(tampered, ensure_ascii=False)
        st.journal_path(SID).write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        st.reset_journal_cache()
        state = st.get_journal(SID).state()
        self.assertNotIn("src_2", state.sources)
        self.assertEqual(state.last_seq, 1)

    # -- 中部损坏保护 ---------------------------------------------------
    def test_midfile_corruption_blocks_writes(self):
        self.journal.register_source(_receipt("src_1"))
        self.journal.register_source(_receipt("src_2"))
        self.journal.register_source(_receipt("src_3"))
        lines = self._lines()
        # 篡改第 2 行（中部），保留前后完整行
        lines[1] = '{"broken": true}'
        st.journal_path(SID).write_text("\n".join(lines) + "\n",
                                        encoding="utf-8")
        st.reset_journal_cache()
        journal = st.get_journal(SID)
        with self.assertRaises(st.JournalCorruptError):
            journal.state()
        with self.assertRaises(st.JournalCorruptError):
            journal.append([S.OpConsumerAck(event_id="x", consumer="y")])

    # -- generation -----------------------------------------------------
    def test_generation_conflict_rejected(self):
        self.journal.register_source(_receipt())
        gen = self.journal.state().generation
        with self.assertRaises(st.GenerationConflictError):
            self.journal.append(
                [S.OpConsumerAck(event_id="e", consumer="c")],
                expected_generation="gen_other")
        # 正确 generation 通过
        self.journal.append(
            [S.OpConsumerAck(event_id="e", consumer="c")],
            expected_generation=gen)

    def test_rewrite_switches_generation_and_drops_content(self):
        self.journal.register_source(_receipt("src_keep", text="保留的答案"))
        self.journal.register_source(_receipt("src_secret", text="秘密答案内容"))
        old_gen = self.journal.state().generation
        new_gen = self.journal.rewrite(
            keep=lambda tx: all(
                not isinstance(op, S.OpSourceRegistered)
                or op.source.source_id != "src_secret"
                for op in tx.operations),
            reason="permanent_delete")
        self.assertNotEqual(old_gen, new_gen)
        state = self.journal.state()
        self.assertNotIn("src_secret", state.sources)
        self.assertIn("src_keep", state.sources)
        # 物理去除：journal 文本不再含被删来源的原文（§5.3）
        raw = st.journal_path(SID).read_text(encoding="utf-8")
        self.assertNotIn("秘密答案内容", raw)
        self.assertIn("保留的答案", raw)
        # 新 generation 上可继续写入
        last_seq_before = state.last_seq
        tx = self.journal.append(
            [S.OpConsumerAck(event_id="e", consumer="c")],
            expected_generation=new_gen)
        self.assertEqual(tx.seq, last_seq_before + 1)

    def test_purge_removes_all_files(self):
        self.journal.register_source(_receipt())
        from app.agents.student_model.evaluation import projections
        projections.rebuild_index(SID)
        self.assertTrue(st.journal_path(SID).exists())
        st.purge_journal_files(SID)
        self.assertFalse(st.journal_path(SID).exists())
        self.assertFalse(self.journal.index_path().exists())

    # -- 索引投影 -------------------------------------------------------
    def test_index_rebuild_after_deletion_identical(self):
        from app.agents.student_model.evaluation import projections
        self.journal.register_source(_receipt())
        first = projections.rebuild_index(SID)
        path = self.journal.index_path()
        path.unlink()
        second = projections.load_index(SID)
        self.assertEqual(first, second)

    def test_views_left_join_not_observed(self):
        from app.agents.student_model.evaluation import projections
        concept = S.ConceptRef(
            graph_owner_namespace="public", textbook_id="tb_1",
            file_ids=["f1"], concept_id="c_x", concept_revision="cr_1",
            display_name="未观察概念")
        scope = S.EvaluationScope(
            workspace_id="ws_1", scope_revision="scope_1",
            selected_volumes=[], allowed_concepts=[concept],
            graph_revisions=[])
        views = projections.concept_views(SID, scope)
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].judgment_id, "")
        self.assertEqual(views[0].state, S.ConceptState.NOT_OBSERVED)
        summary = projections.workspace_summary(SID, scope)
        self.assertEqual(summary.coverage.not_observed_concepts, 1)
        self.assertEqual(summary.coverage.observed_concepts, 0)


if __name__ == "__main__":
    unittest.main()
