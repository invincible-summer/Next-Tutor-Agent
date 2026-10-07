"""Offline contract tests for the owner-scoped worksheet compiler."""
from __future__ import annotations

import asyncio
import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schemas.worksheet import (  # noqa: E402
    WorksheetCreateRequest,
    WorksheetPatchRequest,
    WorksheetQuestionPatchRequest,
)
from app.api.v1 import worksheet as worksheet_api  # noqa: E402
from app.worksheets import service  # noqa: E402
from app.core.workspace import Workspace, save_workspace  # noqa: E402
from app.core.quiz_grounding import QuizGroundingBundle, QuizSourceRef  # noqa: E402
from tests.support.storage_sandbox import StorageSandboxTestCase  # noqa: E402


class WorksheetServiceContractTests(StorageSandboxTestCase):
    def _scope(self, owner: str = "student_a") -> str:
        workspace = Workspace(workspace_id=f"ws_{owner}", name="数学学习区", student_id=owner)
        save_workspace(workspace)
        return workspace.workspace_id

    def test_guidance_is_not_question_background_and_exports_are_split(self):
        workspace_id = self._scope()
        document = service.create(
            "student_a",
            WorksheetCreateRequest(
                title="三角形测验", subject="数学", grade="八年级",
                workspace_id=workspace_id, learning_area="数学学习区",
                guidance_prompt="请加入生活化背景，但不要把提示词写入题干。",
            ),
        )
        self.assertEqual(document["total_score"], 0)
        question = {
            "stem": "命题背景：内部备注\n请加入生活化背景，但不要把提示词写入题干。\n求三角形面积，$S=\\frac{ah}{2}$。",
            "options": {"A": "6", "B": "12"},
            "answer": "A",
            "explanation": "底乘高除以二。",
            "score": 5,
            "difficulty": 2,
            "knowledge_points": ["面积"],
        }
        # Add through the same public patch path as an editor after a small
        # generated draft is present.
        raw = service._load("student_a", document["id"])
        raw["questions"] = [{
            "id": "q_test", "number": 1, "type": "multiple_choice",
            "stem": "求面积。", "options": {}, "answer": "", "explanation": "",
            "score": 5, "difficulty": 2, "knowledge_points": [], "image": None,
            "version": 1, "updated_at": 0,
        }]
        service._recalculate(raw)
        service.storage.write("student_a", document["id"], raw)
        patched = service.patch_question(
            "student_a", document["id"], "q_test",
            WorksheetQuestionPatchRequest(**question, etag=raw["etag"]),
        )
        self.assertNotIn("命题背景", patched["questions"][0]["stem"])
        self.assertNotIn("生活化背景", patched["questions"][0]["stem"])
        student = service.export("student_a", document["id"], "student", "markdown")
        teacher = service.export("student_a", document["id"], "teacher", "markdown")
        html_export = service.export("student_a", document["id"], "student", "html")
        self.assertNotIn("答案：", student.content)
        self.assertIn("答案：", teacher.content)
        self.assertIn("姓名：", student.content)
        self.assertIn("<h1>", html_export.content)
        self.assertNotIn("###", html_export.content)
        self.assertIn('data-katex="S=\\frac{ah}{2}"', html_export.content)

    def test_attachment_etag_and_owner_isolation(self):
        workspace_id = self._scope()
        document = service.create("student_a", WorksheetCreateRequest(title="配图卷", workspace_id=workspace_id))
        raw = service._load("student_a", document["id"])
        raw["questions"] = [{
            "id": "q_image", "number": 1, "type": "short_answer",
            "stem": "说明步骤。", "options": {}, "answer": "答案", "explanation": "解析",
            "score": 5, "difficulty": 3, "knowledge_points": [], "image": None,
            "version": 1, "updated_at": 0,
        }]
        service._recalculate(raw)
        service.storage.write("student_a", document["id"], raw)
        with self.assertRaisesRegex(service.WorksheetError, "worksheet_revision_conflict"):
            service.patch_question(
                "student_a", document["id"], "q_image",
                WorksheetQuestionPatchRequest(stem="新题干", etag="stale"),
            )
        png = "data:image/png;base64," + base64.b64encode(b"png-bytes").decode()
        saved = service.attach_image("student_a", document["id"], "q_image", data_url=png, alt="示意图")
        first_asset = saved["questions"][0]["image"]["asset_id"]
        self.assertTrue(first_asset.startswith("wsa_"))
        replaced = service.attach_image("student_a", document["id"], "q_image", data_url=png, alt="新示意图")
        second_asset = replaced["questions"][0]["image"]["asset_id"]
        self.assertNotEqual(first_asset, second_asset)
        self.assertFalse(list((service.storage.owner_dir("student_a") / "assets" / document["id"]).glob(f"{first_asset}.*")))
        html_export = service.export("student_a", document["id"], "student", "html")
        self.assertIn("question-image", html_export.content)
        with self.assertRaisesRegex(service.WorksheetError, "worksheet_revision_conflict"):
            service.attach_image("student_a", document["id"], "q_image", data_url=png, etag="stale")
        with self.assertRaisesRegex(service.WorksheetError, "worksheet_not_found"):
            service.get("student_b", document["id"])

    def test_learning_area_is_required_and_formula_delimiters_survive_html(self):
        with self.assertRaisesRegex(Exception, "workspace_id"):
            WorksheetCreateRequest(title="没有范围")
        self.assertIn('data-katex="x+y"', service._html({
            "title": "公式", "subject": "数学", "grade": "七年级", "unit": "代数",
            "duration_minutes": 45, "total_score": 5,
            "instructions": r"计算 \(x+y\)。",
            "questions": [{"number": 1, "score": 5, "type": "short_answer",
                           "stem": r"求 $$x=\\frac{1}{2}$$。", "options": {},
                           "answer": r"\[x=1\]", "explanation": ""}],
        }, "teacher"))

    def test_learning_area_owner_and_clear_points_disable_textbook_reference(self):
        foreign_workspace_id = self._scope("student_b")
        with self.assertRaisesRegex(service.WorksheetError, "worksheet_learning_area_invalid"):
            service.create("student_a", WorksheetCreateRequest(workspace_id=foreign_workspace_id))
        with self.assertRaises(HTTPException) as route_error:
            worksheet_api.create_worksheet(
                WorksheetCreateRequest(workspace_id=foreign_workspace_id), owner="student_a",
            )
        self.assertEqual(route_error.exception.status_code, 422)
        self.assertEqual(route_error.exception.detail, {"code": "worksheet_learning_area_invalid"})
        document = service.create("student_a", WorksheetCreateRequest(
            workspace_id=self._scope(), learning_area="伪造的显示名",
            knowledge_points=["三角形面积"], reference_textbook=True,
        ))
        self.assertEqual(document["learning_area"], "数学学习区")
        self.assertTrue(document["reference_textbook"])
        with self.assertRaisesRegex(service.WorksheetError, "worksheet_learning_area_required"):
            service.patch("student_a", document["id"], WorksheetPatchRequest(
                workspace_id="", etag=document["etag"],
            ))
        saved = service.patch("student_a", document["id"], WorksheetPatchRequest(
            knowledge_points=[], reference_textbook=True, etag=document["etag"],
        ))
        self.assertFalse(saved["reference_textbook"])

    def test_textbook_retrieval_is_optional_and_requires_selected_scope(self):
        with patch.object(service, "_workspace_textbook_ids", return_value=[]) as resolve:
            disabled = asyncio.run(service._worksheet_grounding(
                "student_a", {"reference_textbook": False}, ["面积"],
            ))
            no_points = asyncio.run(service._worksheet_grounding(
                "student_a", {"reference_textbook": True}, [],
            ))
            self.assertEqual(disabled, {})
            self.assertEqual(no_points, {})
            resolve.assert_not_called()
            with self.assertRaisesRegex(service.WorksheetError, "worksheet_textbook_scope_empty"):
                asyncio.run(service._worksheet_grounding(
                    "student_a", {"reference_textbook": True, "workspace_id": "ws_math"}, ["面积"],
                ))

    def test_textbook_retrieval_preserves_scoped_evidence_and_rejects_no_match(self):
        bundle = QuizGroundingBundle(
            query="面积", mode="textbook", tier="found", required=True,
            reason="assessment_textbook_scope",
            source_refs=[QuizSourceRef(file_id="file_math", chunk_id="chunk_area", excerpt="项目原创测试材料")],
        )
        resolver = AsyncMock(return_value=bundle)
        with patch.object(service, "_workspace_textbook_ids", return_value=["tb_math"]) as scope, \
                patch("app.api.v1.assessment_grounding.build_assessment_grounding", resolver):
            fields = asyncio.run(service._worksheet_grounding(
                "student_a", {"reference_textbook": True, "workspace_id": "ws_math"}, ["面积"],
            ))
            scope.assert_called_once_with("student_a", "ws_math")
            resolver.assert_awaited_once_with(
                student_id="student_a", concept="面积", textbook_ids=["tb_math"], strict_textbook=True,
            )
            self.assertTrue(fields["grounding_required"])
            self.assertEqual(fields["grounding_sources"][0]["chunk_id"], "chunk_area")
            resolver.return_value = QuizGroundingBundle(
                query="面积", mode="textbook", tier="not_found", required=True,
                reason="assessment_textbook_scope",
            )
            with self.assertRaisesRegex(service.WorksheetError, "worksheet_textbook_no_match"):
                asyncio.run(service._worksheet_grounding(
                    "student_a", {"reference_textbook": True, "workspace_id": "ws_math"}, ["面积"],
                ))


if __name__ == "__main__":
    unittest.main()
