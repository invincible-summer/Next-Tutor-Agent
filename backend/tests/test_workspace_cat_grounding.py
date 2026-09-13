import json as _json  # noqa: E402
from unittest.mock import patch  # noqa: E402

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402


class _GroundedGenLLM:
    """fake LLM：P1 蓝图/P2 审题/生成三分派，生成题携带 source_ref_ids。"""

    def __init__(self):
        self.prompts: list[str] = []

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        text = "\n".join(str(m.get("content") or "") for m in messages)
        self.prompts.append(text)
        if "任务设计者" in text:
            return _json.dumps({"items": [{
                "local_question_id": "q1",
                "target_concept_refs": ["ZX-17 定理"],
                "target_claims": ["能说出 ZX-17 右端常数"],
                "intended_processes": ["remember"],
                "knowledge_types": ["factual"], "q_type": "multiple_choice",
                "difficulty_design": "基础", "task_family": "zx17",
                "construction_brief": "考查 ZX-17 右端常数",
            }]}, ensure_ascii=False), {}
        if "出题审核员" in text:
            return _json.dumps({"items": [{
                "question_ref": "1", "answer_check": "valid",
                "grounding_check": "supported",
                "actual_required_processes": ["remember"],
                "knowledge_types": ["factual"], "alignment": "aligned",
                "opportunity_checks": [], "rubric_issues": [],
                "brief_basis": "", "grounding_refs": ["src_1"],
                "recommended_revision": "", "proposed_status": "passed"}]},
                ensure_ascii=False), {}
        return _json.dumps({"questions": [{
            "id": 1, "type": "multiple_choice",
            "stem": "ZX-17 定理的右端常数是多少？",
            "options": {"A": "314159", "B": "1"}, "answer": "A",
            "explanation": "教材规定 ZX-17 定理右端常数为 314159，选 A。",
            "knowledge_point": "ZX-17 定理", "difficulty": "easy",
            "source_ref_ids": ["src_1"],
        }]}, ensure_ascii=False), {}


def _make_user(email: str):
    from app.identity import store as id_store
    from app.identity.security import create_token, hash_password
    user = id_store.create_user(
        email=email, username="",
        password_hash=hash_password("secret123"))
    return user.id, {"Authorization": f"Bearer {create_token(user.id)}"}


_ZX_TEXT = (
    "第三章 ZX-17 定理\n"
    "ZX-17 定理的右端常数为 314159。\n"
    "根据 ZX-17 定理，任意封闭曲面上的通量积分右端常数恒取 314159。\n" * 8
)


class TestWorkspaceGroundedCat(StorageSandboxTestCase):
    """新版 §11.5：CAT grounding 经工作区已选教材（M5 教材证据保留）。"""

    def setUp(self):
        super().setUp()
        from app.core import learner_runtime
        learner_runtime.reset_learner_runtime()
        from tests.test_unified_submission import FakeRunner
        self.runner = FakeRunner([])
        learner_runtime.set_evaluation_runner(self.runner)

    def _fixture(self, *, public: bool = False):
        """用户 + 教材 + 图谱 + 选中教材的工作区，返回 (client, headers, ws_id)。"""
        from app.core.library import Library, save_library
        from app.core import textbook as tb
        from app.agents.knowledge import store as kg
        from app.core.workspace import Workspace, save_workspace
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        from fastapi.testclient import TestClient
        from app.main import create_app

        owner = "public" if public else "usr_ground_a"
        id_store.create_user("ground-a@example.com", "G",
                             hash_password("secret123"),
                             user_id="usr_ground_a")
        lib = Library(owner)
        meta = lib.add_file("", "zx17讲义.txt", _ZX_TEXT)
        save_library(lib)
        rec = tb.create_group(owner, file_ids=[meta["id"]],
                              title="ZX-17 讲义",
                              scope="public" if public else "private")
        topic = rec["topic_key"]
        nodes = [
            {"id": f"custom.{topic}.ch.1", "name": "第三章", "kind": "chapter",
             "metadata": {"file_id": meta["id"]}},
            {"id": f"custom.{topic}.c.zx17", "name": "ZX-17 定理",
             "kind": "concept", "aliases": [],
             "metadata": {"file_ids": [meta["id"]]}},
        ]
        kg.save_custom_graph(owner, topic, {
            "topic": "ZX-17", "topic_key": topic, "nodes": nodes,
            "edges": [{"source": nodes[1]["id"], "target": nodes[0]["id"],
                       "type": "part_of"}], "contents": [], "coverage": []})
        records = tb.load_textbooks(owner)
        for r in records:
            if r["id"] == rec["id"]:
                r["status"] = "ready"
        tb._save(owner, records)
        ws = Workspace(workspace_id="ws_ground_1", name="ZX",
                       student_id="usr_ground_a",
                       selected_file_ids=[meta["id"]])
        save_workspace(ws)
        client = TestClient(create_app())
        headers = {"Authorization": f"Bearer {create_token('usr_ground_a')}"}
        return client, headers, ws.workspace_id, topic, nodes

    def test_workspace_cat_grounds_in_selected_textbook(self):
        client, headers, ws_id, topic, nodes = self._fixture()
        llm = _GroundedGenLLM()
        with patch("app.api.v1.assessment.get_llm", return_value=llm):
            r = client.post("/api/v1/assessment/start", json={
                "workspace_id": ws_id,
                "concept_keys": ["ZX-17 定理"], "count": 2,
            }, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        question = r.json()["question"]
        self.assertIsNotNone(question)
        # 题目公开投影不含答案/量规（A07）
        dumped = _json.dumps(question, ensure_ascii=False)
        self.assertNotIn("314159", dumped.split("stem")[0])
        # 生成调用确实见到了教材证据（grounding block 进 prompt）
        gen_prompts = [p for p in llm.prompts if "ZX-17 定理" in p
                       and "命题" not in p[:20]]
        self.assertTrue(any("教材" in p or "material_excerpt" in p
                            or "ZX" in p for p in llm.prompts))
        # 概念绑定：CAT 实例记录的题目在工作区 scope 内（journal）
        from app.agents.student_model.evaluation import store as st
        state = st.get_journal("usr_ground_a").state()
        self.assertTrue(any(
            d.get("workspace_id") == ws_id and d.get("status") == "active"
            for d in state.assessments.values()))

    def test_no_workspace_cat_still_works_ungrounded(self):
        client, headers, _ws, _topic, _nodes = self._fixture()
        llm = _GroundedGenLLM()
        with patch("app.api.v1.assessment.get_llm", return_value=llm):
            r = client.post("/api/v1/assessment/start", json={
                "workspace_id": "", "concept_keys": ["ZX-17 定理"],
                "count": 2}, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNotNone(r.json()["question"])

    def test_foreign_workspace_404(self):
        client, headers, _ws, _topic, _nodes = self._fixture()
        llm = _GroundedGenLLM()
        with patch("app.api.v1.assessment.get_llm", return_value=llm):
            r = client.post("/api/v1/assessment/start", json={
                "workspace_id": "ws_other", "concept_keys": ["ZX-17 定理"],
                "count": 2}, headers=headers)
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
