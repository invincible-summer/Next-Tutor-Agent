"""Shadow identity keeps file profile and PostgreSQL authentication facts."""
from __future__ import annotations

import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

from tests.support.storage_sandbox import StorageSandboxTestCase
from tests.identity.test_enterprise_auth import _EnterpriseAuthTestCase


class ShadowProfileReadTest(StorageSandboxTestCase,
                            unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        from app.identity import store
        from app.identity.backend import EnterpriseIdentityBackend
        from app.identity.models import UserProfile
        from app.persistence.repositories.records import AccountRecord

        self.store = store
        self.user = store.create_user(
            "shadow@example.com", "shadow", "file-password",
            profile=UserProfile(name="Old name", grade="本科"))
        self.record = AccountRecord(
            user_id=self.user.id, email=self.user.email,
            username="database-name", role="admin", token_version=7,
            profile=self.user.profile.to_dict())
        self.repo = mock.Mock()
        self.repo.get_account_by_id = mock.AsyncMock(return_value=self.record)
        self.repo.get_account_by_email = mock.AsyncMock(return_value=self.record)
        self.repo.get_password_credential = mock.AsyncMock(
            return_value=SimpleNamespace(secret_hash="database-password"))
        self.backend = EnterpriseIdentityBackend(self.repo)

    async def test_profile_updates_survive_both_identity_read_paths(self):
        updated = self.store.update_profile_fields(
            self.user.id, {"name": "New name", "grade": "初中"},
            {"quiz_illustration_mode": "v3"})
        by_id = await self.backend.get_by_id(self.user.id)
        by_email = await self.backend.get_by_email(self.user.email)
        for user in (by_id, by_email):
            self.assertEqual(user.profile.to_dict(), updated.profile.to_dict())
            self.assertEqual(user.username, "database-name")
            self.assertEqual(user.role, "admin")
            self.assertEqual(user.token_version, 7)
            self.assertEqual(user.password_hash, "database-password")
        self.assertEqual(self.record.profile["name"], "Old name")

    async def test_avatar_updates_and_removal_use_file_profile(self):
        self.store.update_profile_fields(self.user.id,
                                         {"avatar": "avatar:synthetic"})
        user = await self.backend.get_by_id(self.user.id)
        self.assertEqual(user.profile.avatar, "avatar:synthetic")
        self.store.update_profile_fields(self.user.id, {"avatar": ""})
        self.record.profile["avatar"] = "avatar:stale-database"
        user = await self.backend.get_by_id(self.user.id)
        self.assertEqual(user.profile.avatar, "")

    async def test_database_only_accounts_retain_database_profile(self):
        self.store.delete_user(self.user.id)
        user = await self.backend.get_by_id(self.user.id)
        self.assertEqual(user.profile.name, "Old name")
        self.assertEqual(user.role, "admin")
        self.assertEqual(user.password_hash, "database-password")


class EnterpriseProfileReadTest(_EnterpriseAuthTestCase):
    def test_profile_and_avatar_edits_survive_new_authenticated_requests(self):
        from PIL import Image

        registered = self.client.post("/api/v1/auth/register", json={
            "email": "profile@example.com", "password": "password-123"}).json()
        headers = {"Authorization": f"Bearer {registered['access_token']}"}
        updated = self.client.put("/api/v1/user/profile", headers=headers,
                                  json={"name": "Updated name", "grade": "初中",
                                        "prefs": {"quiz_illustration_mode": "v3"}})
        self.assertEqual(updated.status_code, 200)
        expected = updated.json()["profile"]
        for token in (registered["access_token"], registered["token"]):
            with self.subTest(token_type="access" if token.startswith("ey") else "legacy"):
                read = self.client.get("/api/v1/user/profile", headers={
                    "Authorization": f"Bearer {token}"})
                self.assertEqual(read.status_code, 200)
                self.assertEqual(read.json()["profile"], expected)
        logged_in = self.client.post("/api/v1/auth/login", json={
            "email": "profile@example.com", "password": "password-123"})
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(logged_in.json()["user"]["profile"], expected)

        png = BytesIO()
        Image.new("RGB", (12, 12), "teal").save(png, format="PNG")
        uploaded = self.client.put("/api/v1/user/avatar", headers=headers,
                                   files={"file": ("synthetic.png", png.getvalue(),
                                                   "image/png")})
        self.assertEqual(uploaded.status_code, 200)
        avatar = uploaded.json()["profile"]["avatar"]
        self.assertTrue(avatar.startswith("avatar:"))
        self.assertEqual(self.client.get("/api/v1/user/profile", headers=headers)
                         .json()["profile"]["avatar"], avatar)
        removed = self.client.delete("/api/v1/user/avatar", headers=headers)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/user/profile", headers=headers)
                         .json()["profile"]["avatar"], "")
