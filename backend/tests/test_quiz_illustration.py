"""SVG question illustration contract and policy regression tests."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.core.quiz_illustration import (
    IllustrationValidationError,
    normalize_illustration,
    normalize_question_illustration,
)
from app.core.quiz_illustration_policy import (
    IllustrationDisabled,
    explicit_illustration_request,
    resolve_illustration_policy,
)
from app.core import config


_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400">'
    '<line x1="40" y1="200" x2="600" y2="200" stroke="#000"/>'
    '<circle cx="320" cy="200" r="32" fill="none" stroke="#000"/>'
    '</svg>'
)


class IllustrationContractTest(unittest.TestCase):
    def test_normalize_adds_canonical_fields_and_hash(self):
        image = normalize_illustration({
            "kind": "svg", "alt": "水平线和圆形标记", "svg": _SVG,
        })
        self.assertEqual(image.kind, "svg")
        self.assertEqual((image.width, image.height), (640, 400))
        self.assertTrue(image.content_hash.startswith("sha256:"))
        self.assertEqual(image, normalize_illustration(image.model_dump()))

    def test_rejects_script_and_external_nodes(self):
        for payload in (
            _SVG.replace("</svg>", "<script>alert(1)</script></svg>"),
            _SVG.replace("</svg>", '<image href="https://x.invalid/a"/></svg>'),
        ):
            with self.assertRaises((IllustrationValidationError, ValueError)):
                normalize_illustration({"kind": "svg", "alt": "图", "svg": payload})

    def test_rejects_non_monochrome_and_bad_viewbox(self):
        for payload in (
            _SVG.replace('stroke="#000"', 'stroke="red"'),
            _SVG.replace('viewBox="0 0 640 400"', 'viewBox="1 0 640 400"'),
        ):
            with self.assertRaises((IllustrationValidationError, ValueError)):
                normalize_illustration({"kind": "svg", "alt": "图", "svg": payload})

    def test_question_policy_handles_required_and_missing_dependency(self):
        with self.assertRaises(IllustrationValidationError) as required:
            normalize_question_illustration({"stem": "画出装置", "illustration": None},
                                             policy="required")
        self.assertEqual(required.exception.code, "illustration_required_missing")
        with self.assertRaises(IllustrationValidationError) as dependency:
            normalize_question_illustration({"stem": "如图所示求长度", "illustration": None},
                                             policy="auto")
        self.assertEqual(dependency.exception.code, "illustration_missing_dependency")

    def test_off_policy_never_accepts_image(self):
        with self.assertRaises(IllustrationValidationError) as ctx:
            normalize_question_illustration({"stem": "题目", "illustration": {
                "kind": "svg", "alt": "图", "svg": _SVG,
            }}, policy="off")
        self.assertEqual(ctx.exception.code, "illustration_disabled")


class IllustrationPolicyTest(unittest.TestCase):
    def test_explicit_language_is_required_and_negation_wins(self):
        self.assertEqual(explicit_illustration_request("请出一道带插图的物理题"), "required")
        self.assertEqual(explicit_illustration_request("请不要配图，纯文字题即可"), "none")
        self.assertEqual(explicit_illustration_request("请解释这张已有的图"), "auto")

    def test_global_switch_is_fail_closed(self):
        with patch.object(config.settings, "quiz_svg_enabled", False):
            self.assertEqual(resolve_illustration_policy("usr_test", "auto"), "off")
            with self.assertRaises(IllustrationDisabled):
                resolve_illustration_policy("usr_test", "required")


if __name__ == "__main__":
    unittest.main()
