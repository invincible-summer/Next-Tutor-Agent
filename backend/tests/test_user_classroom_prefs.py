"""个人课堂 voice/界面偏好严格校验（plan.md §20.1、阶段 F05）。

prefs.classroom 只接受白名单类型字段；未知键、URL 形值、类型错误一律 422
——个人偏好不能浅合并任意供应商 endpoint/base URL。旧 prefs 键保持兼容。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.v1.user import UpdateProfileRequest  # noqa: E402
from app.main import create_app  # noqa: E402


class ClassroomPrefsModelTests(unittest.TestCase):
    def _validate(self, classroom):
        return UpdateProfileRequest(prefs={"classroom": classroom})\
            .prefs["classroom"]

    def test_valid_full_prefs_accepted(self):
        cleaned = self._validate({
            "theme_id": "academic_clear@1",
            "pedagogy_id": "concept_deep@1",
            "voice_policy": "cloud",
            "voice_id": "zh-CN-XiaoxiaoNeural",
            "allow_local_fallback": True,
            "captions": False,
            "auto_advance": True,
            "low_stimulus": False,
            "pause_on_hidden": True,
        })
        self.assertEqual(cleaned["voice_policy"], "cloud")
        self.assertTrue(cleaned["pause_on_hidden"])

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            self._validate({"azure_endpoint": "https://x.api.cognitiveservices.azure.com"})
        with self.assertRaises(ValueError):
            self._validate({"provider_base_url": "http://evil.example/tts"})

    def test_url_shaped_values_rejected(self):
        for bad in ("https://evil.example", "http://x", "evil.example/tts",
                    "a b", "zh-CN:Xiaoxiao"):
            with self.assertRaises(ValueError, msg=bad):
                self._validate({"voice_id": bad})
            with self.assertRaises(ValueError, msg=bad):
                self._validate({"theme_id": bad})

    def test_type_errors_rejected(self):
        with self.assertRaises(ValueError):
            self._validate({"captions": "yes"})          # 必须布尔
        with self.assertRaises(ValueError):
            self._validate({"voice_policy": "loud"})     # 枚举
        with self.assertRaises(ValueError):
            self._validate({"voice_id": 42})             # 必须字符串
        with self.assertRaises(ValueError):
            self._validate("not-a-dict")

    def test_legacy_prefs_keys_still_compatible(self):
        prefs = UpdateProfileRequest(prefs={
            "tts_speed": 1.1, "quiz_svg_enabled": True}).prefs
        self.assertEqual(prefs["tts_speed"], 1.1)
        self.assertNotIn("classroom", prefs)


class ClassroomPrefsApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        from app.identity import config as id_config
        from app.identity import store as id_store
        from tests.storage_sandbox import patch_all_storage_roots
        root = Path(self._tmp.name)
        (root / "users").mkdir()
        self._patches = patch_all_storage_roots(root)
        id_patches = [
            patch.object(id_config, "AUTH_JWT_SECRET", "sandbox-test-secret"),
            patch.object(id_store, "_ACCOUNTS_FILE",
                         root / "users" / "accounts.json"),
        ]
        for p in id_patches:
            p.start()
        self._patches += id_patches
        from app.core.ratelimit import reset_rate_limits
        reset_rate_limits()
        self.client = TestClient(create_app())
        from app.identity.security import create_token, hash_password
        self.user = id_store.create_user(
            email="cp@example.com", username="",
            password_hash=hash_password("secret123"))
        self.user_h = {"Authorization": f"Bearer {create_token(self.user.id)}"}

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._env_old
        from app.agents.student_model import manager as sm_manager
        from app.core import vector_store
        sm_manager._CACHE.clear()
        vector_store._reset()
        self._tmp.cleanup()

    def test_roundtrip_and_rejection(self):
        r = self.client.put("/api/v1/user/profile", json={
            "prefs": {"classroom": {
                "voice_policy": "auto",
                "voice_id": "zh-CN-XiaoxiaoNeural",
                "captions": True}}}, headers=self.user_h)
        self.assertEqual(r.status_code, 200)
        stored = r.json()["profile"]["prefs"]["classroom"]
        self.assertEqual(stored["voice_id"], "zh-CN-XiaoxiaoNeural")
        self.assertTrue(stored["captions"])

        # 未知键（含供应商 URL 形态）→ 422，不落库
        r2 = self.client.put("/api/v1/user/profile", json={
            "prefs": {"classroom": {
                "base_url": "https://evil.example/tts"}}}, headers=self.user_h)
        self.assertEqual(r2.status_code, 422)
        r3 = self.client.get("/api/v1/user/profile", headers=self.user_h)
        self.assertNotIn("base_url",
                         r3.json()["profile"]["prefs"]["classroom"])


if __name__ == "__main__":
    unittest.main()
