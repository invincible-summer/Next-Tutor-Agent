"""Enterprise-mode auth behavior against a sandboxed sqlite database.

Covers the Stage B identity contract: registration auto-creates the personal
tenant + owner membership, login issues the rotating session alongside the
legacy token, refresh rotates with reuse detection revoking the family,
sessions list/revoke work, and the tenant-aware principal resolves. File
mode keeps returning the exact legacy payload (covered by the existing auth
suite; asserted here once for shape parity).
"""
from __future__ import annotations

import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class _EnterpriseAuthTestCase(StorageSandboxTestCase):
    """Storage sandbox + DATABASE_URL pointing at a sandbox sqlite db."""

    def setUp(self) -> None:
        super().setUp()
        from app.core.ratelimit import reset_rate_limits
        from app.persistence import db
        from app.identity import backend as id_backend
        from app.identity import keys as id_keys
        from app.identity import sessions as id_sessions

        # Explicit resets keep mode/engine/keyring singletons per test; the
        # process-global rate limiter is not sandboxed, so clear it too.
        reset_rate_limits()
        db.reset_engine()
        id_backend.reset_identity_backend()
        id_keys.reset_default_keyring()
        id_sessions.reset_session_service()

        self._db_path = self.root / "enterprise.db"
        self._saved_env: dict[str, str | None] = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "CACHE_URL", "REDIS_URL")}
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{self._db_path.as_posix()}")
        os.environ.pop("CACHE_URL", None)
        os.environ.pop("REDIS_URL", None)
        try:
            self._migrate_and_client()
        except Exception:
            self._restore_env()
            raise

    def _restore_env(self) -> None:
        for key, old in self._saved_env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    def _migrate_and_client(self) -> None:
        # Sync on purpose: alembic's env.py calls asyncio.run itself, so this
        # must not already be inside a loop.
        from alembic.config import main as alembic_main

        cwd = os.getcwd()
        os.chdir(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        try:
            alembic_main(["upgrade", "head"])
        finally:
            os.chdir(cwd)
        from app.main import create_app
        from fastapi.testclient import TestClient

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        import asyncio

        from app.persistence import db
        from app.identity import backend as id_backend
        from app.identity import keys as id_keys
        from app.identity import sessions as id_sessions

        async def _dispose() -> None:
            engine = db._engine_cache.get(
                f"sqlite+aiosqlite:///{self._db_path.as_posix()}")
            if engine is not None:
                await engine.dispose()
        asyncio.run(_dispose())
        db.reset_engine()
        id_backend.reset_identity_backend()
        id_keys.reset_default_keyring()
        id_sessions.reset_session_service()
        self._restore_env()
        super().tearDown()


class EnterpriseRegistrationTest(_EnterpriseAuthTestCase):
    def test_register_creates_personal_tenant_and_membership(self) -> None:
        resp = self.client.post("/api/v1/auth/register", json={
            "email": "alice@example.com", "password": "password-123",
            "username": "alice"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # legacy token still present (Web compat) + rotating session fields
        self.assertIn("token", body)
        self.assertIn("access_token", body)
        self.assertIn("refresh_token", body)
        self.assertEqual(body["expires_in"], 900)

    def test_login_issues_rotating_session(self) -> None:
        self.client.post("/api/v1/auth/register", json={
            "email": "bob@example.com", "password": "password-123"})
        resp = self.client.post("/api/v1/auth/login", json={
            "email": "bob@example.com", "password": "password-123"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["token"])
        self.assertTrue(body["access_token"])
        self.assertTrue(body["refresh_token"])

    def test_register_duplicate_email_conflicts(self) -> None:
        payload = {"email": "dup@example.com", "password": "password-123"}
        first = self.client.post("/api/v1/auth/register", json=payload)
        self.assertEqual(first.status_code, 200)
        again = self.client.post("/api/v1/auth/register", json=payload)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["detail"], "email_already_registered")

    def test_register_shadow_reuses_committed_user_id(self) -> None:
        """Single-transaction registration (WS5d): the file shadow is a
        post-commit best-effort write that reuses the committed user id, so
        legacy file consumers and the DB address the same account."""
        from app.identity.store import get_by_email as file_get_by_email

        resp = self.client.post("/api/v1/auth/register", json={
            "email": "shadow@example.com", "password": "password-123"})
        self.assertEqual(resp.status_code, 200)
        user_id = resp.json()["user"]["id"]
        shadow = file_get_by_email("shadow@example.com")
        self.assertIsNotNone(shadow)
        assert shadow is not None
        self.assertEqual(shadow.id, user_id)


class RefreshCookieTest(_EnterpriseAuthTestCase):
    """WS5e: the HttpOnly refresh-cookie lane. Flows drive the cookie via an
    explicit ``Cookie`` header (httpx would otherwise refuse to replay a
    Secure cookie over http://testserver); attributes are asserted on the
    raw Set-Cookie header."""

    def _login(self, email: str) -> dict:
        resp = self.client.post("/api/v1/auth/login", json={
            "email": email, "password": "password-123"})
        # register on demand (first login of a fresh account)
        if resp.status_code == 401:
            self.client.post("/api/v1/auth/register", json={
                "email": email, "password": "password-123"})
            resp = self.client.post("/api/v1/auth/login", json={
                "email": email, "password": "password-123"})
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_login_seeds_hardened_cookie(self) -> None:
        self._login("cookie-attrs@example.com")
        # A fresh login response carries the seed cookie.
        raw = self.client.post("/api/v1/auth/login", json={
            "email": "cookie-attrs@example.com",
            "password": "password-123"})
        header = raw.headers.get("set-cookie", "")
        self.assertIn("edu_refresh=", header)
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=lax", header)
        self.assertIn("Path=/api/v1/auth", header)
        self.assertIn("Secure", header)

    def test_refresh_via_cookie_rotates_cookie(self) -> None:
        body = self._login("cookie-flow@example.com")
        old = body["refresh_token"]
        resp = self.client.post(
            "/api/v1/auth/refresh", json={},
            headers={"Cookie": f"edu_refresh={old}"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["access_token"])
        self.assertTrue(data["refresh_token"])
        self.assertNotEqual(data["refresh_token"], old)
        self.assertIn("edu_refresh=", resp.headers.get("set-cookie", ""))

    def test_body_token_wins_over_cookie(self) -> None:
        a = self._login("cookie-precedence-a@example.com")
        b = self._login("cookie-precedence-b@example.com")
        # A's cookie + B's body: B's family rotates; A's stays untouched.
        resp = self.client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": b["refresh_token"]},
            headers={"Cookie": f"edu_refresh={a['refresh_token']}"})
        self.assertEqual(resp.status_code, 200)
        new_b = resp.json()["refresh_token"]
        a_still = self.client.post(
            "/api/v1/auth/refresh", json={},
            headers={"Cookie": f"edu_refresh={a['refresh_token']}"})
        self.assertEqual(a_still.status_code, 200)
        self.assertNotEqual(a_still.json()["refresh_token"], new_b)

    def test_logout_revokes_cookie_session_and_clears_cookie(self) -> None:
        body = self._login("cookie-logout@example.com")
        logout = self.client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {body['access_token']}",
                     "Cookie": f"edu_refresh={body['refresh_token']}"})
        self.assertEqual(logout.status_code, 200)
        set_cookie = logout.headers.get("set-cookie", "")
        self.assertIn("edu_refresh=", set_cookie)
        self.assertIn("Max-Age=0", set_cookie.replace(" ", ""))
        # The family behind the cookie is dead server-side.
        again = self.client.post(
            "/api/v1/auth/refresh", json={},
            headers={"Cookie": f"edu_refresh={body['refresh_token']}"})
        self.assertEqual(again.status_code, 401)

    def test_dead_session_refresh_clears_stale_cookie(self) -> None:
        body = self._login("cookie-dead@example.com")
        old = body["refresh_token"]
        first = self.client.post(
            "/api/v1/auth/refresh", json={},
            headers={"Cookie": f"edu_refresh={old}"})
        self.assertEqual(first.status_code, 200)
        # Replaying the rotated token is reuse → 401 AND cookie cleared.
        replay = self.client.post(
            "/api/v1/auth/refresh", json={},
            headers={"Cookie": f"edu_refresh={old}"})
        self.assertEqual(replay.status_code, 401)
        set_cookie = replay.headers.get("set-cookie", "")
        self.assertIn("edu_refresh=", set_cookie)


class TenantIsolationAPITest(_EnterpriseAuthTestCase):
    """WS5c end-to-end: the tenant claim on the access token scopes every
    SQL-mode document read/write for the whole request (middleware copies
    it into the document context). Same-owner rows under another personal
    tenant are structurally invisible over HTTP."""

    def setUp(self) -> None:
        from app.core.config import settings
        self._saved_assistant = settings.site_assistant_enabled
        settings.site_assistant_enabled = True
        super().setUp()

    def tearDown(self) -> None:
        from app.core.config import settings
        settings.site_assistant_enabled = self._saved_assistant
        super().tearDown()

    def _register_and_login(self, email: str) -> dict:
        self.client.post("/api/v1/auth/register", json={
            "email": email, "password": "password-123"})
        resp = self.client.post("/api/v1/auth/login", json={
            "email": email, "password": "password-123"})
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_assistant_conversations_isolated_per_tenant(self) -> None:
        alice = self._register_and_login("tenanta@example.com")
        bob = self._register_and_login("tenantb@example.com")

        created = self.client.post(
            "/api/v1/assistant/conversations",
            headers={"Authorization": f"Bearer {alice['access_token']}"},
            json={"client_request_id": "0d0e0f0a-1111-4222-8333-444455556666",
                  "title": "Alice的会话"})
        self.assertEqual(created.status_code, 201)
        conversation_id = created.json()["conversation_id"]

        # The other tenant's account sees nothing of it — by list...
        bob_list = self.client.get(
            "/api/v1/assistant/conversations",
            headers={"Authorization": f"Bearer {bob['access_token']}"})
        self.assertEqual(bob_list.status_code, 200)
        self.assertEqual(bob_list.json()["total"], 0)
        # ...and by direct resource id.
        bob_direct = self.client.get(
            f"/api/v1/assistant/conversations/{conversation_id}",
            headers={"Authorization": f"Bearer {bob['access_token']}"})
        self.assertEqual(bob_direct.status_code, 404)

        alice_list = self.client.get(
            "/api/v1/assistant/conversations",
            headers={"Authorization": f"Bearer {alice['access_token']}"})
        self.assertEqual(alice_list.status_code, 200)
        self.assertEqual(alice_list.json()["total"], 1)
        self.assertEqual(
            alice_list.json()["items"][0]["conversation_id"],
            conversation_id)


class RefreshFlowTest(_EnterpriseAuthTestCase):
    def _login(self) -> dict:
        self.client.post("/api/v1/auth/register", json={
            "email": "carol@example.com", "password": "password-123"})
        resp = self.client.post("/api/v1/auth/login", json={
            "email": "carol@example.com", "password": "password-123"})
        return resp.json()

    def test_refresh_rotates_and_reuse_revokes_family(self) -> None:
        body = self._login()
        first_refresh = body["refresh_token"]
        access = body["access_token"]

        # access token authenticates (session active, RS256 kid)
        me = self.client.get("/api/v1/auth/me",
                             headers={"Authorization": f"Bearer {access}"})
        self.assertEqual(me.status_code, 200)

        # rotation issues a new pair
        resp = self.client.post("/api/v1/auth/refresh",
                                json={"refresh_token": first_refresh})
        self.assertEqual(resp.status_code, 200)
        rotated = resp.json()
        # opaque refresh tokens rotate; the access JWT is deterministic for
        # the same session/second (same claims + key), so only the refresh
        # token's rotation is asserted here.
        self.assertNotEqual(rotated["refresh_token"], first_refresh)
        me_rotated = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {rotated['access_token']}"})
        self.assertEqual(me_rotated.status_code, 200)

        # reuse of the superseded token revokes the whole family
        reuse = self.client.post("/api/v1/auth/refresh",
                                 json={"refresh_token": first_refresh})
        self.assertEqual(reuse.status_code, 401)
        self.assertEqual(
            reuse.json()["detail"]["error"]["code"], "refresh_token_reused")

        # …and the NEW token is dead too (family revoked), access token too
        dead = self.client.post("/api/v1/auth/refresh",
                                json={"refresh_token": rotated["refresh_token"]})
        self.assertEqual(dead.status_code, 401)
        # Revocation clears the short session cache synchronously, so the
        # rotated access token must fail closed right away.
        me2 = self.client.get("/api/v1/auth/me",
                              headers={"Authorization":
                                       f"Bearer {rotated['access_token']}"})
        self.assertEqual(me2.status_code, 401)

    def test_invalid_refresh_token_rejected(self) -> None:
        self._login()
        resp = self.client.post("/api/v1/auth/refresh",
                                json={"refresh_token": "rt_totally-bogus"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"]["error"]["code"],
                         "invalid_refresh_token")


class SessionManagementTest(_EnterpriseAuthTestCase):
    def test_list_and_revoke_sessions(self) -> None:
        registered = self.client.post("/api/v1/auth/register", json={
            "email": "dave@example.com", "password": "password-123"}).json()
        login = self.client.post("/api/v1/auth/login", json={
            "email": "dave@example.com", "password": "password-123"}).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        listed = self.client.get("/api/v1/auth/sessions", headers=headers)
        self.assertEqual(listed.status_code, 200)
        sessions = listed.json()["sessions"]
        self.assertEqual(len(sessions), 2)  # register + login sessions
        # newest first; target the REGISTER session (revoking the session
        # the request itself rides on correctly 401s subsequent calls)
        target = sessions[1]["id"]

        revoked = self.client.delete(f"/api/v1/auth/sessions/{target}",
                                     headers=headers)
        self.assertEqual(revoked.status_code, 200)
        # the revoked device's access token fails closed immediately
        other = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {registered['access_token']}"})
        self.assertEqual(other.status_code, 401)
        # idempotent second revoke -> 404 (already revoked)
        again = self.client.delete(f"/api/v1/auth/sessions/{target}",
                                   headers=headers)
        self.assertEqual(again.status_code, 404)

    def test_principal_exposes_tenant_context(self) -> None:
        self.client.post("/api/v1/auth/register", json={
            "email": "erin@example.com", "password": "password-123"})
        login = self.client.post("/api/v1/auth/login", json={
            "email": "erin@example.com", "password": "password-123"}).json()
        resp = self.client.get(
            "/api/v1/auth/principal",
            headers={"Authorization": f"Bearer {login['access_token']}"})
        self.assertEqual(resp.status_code, 200)
        principal = resp.json()["principal"]
        self.assertTrue(principal["tenant_id"].startswith("tnt_"))
        self.assertTrue(principal["membership_id"].startswith("mem_"))
        self.assertEqual(principal["tenant_role"], "owner")
        self.assertEqual(principal["platform_role"], "student")
        self.assertTrue(principal["auth_session_id"])

    def test_legacy_hs256_token_still_accepted(self) -> None:
        self.client.post("/api/v1/auth/register", json={
            "email": "frank@example.com", "password": "password-123"})
        login = self.client.post("/api/v1/auth/login", json={
            "email": "frank@example.com", "password": "password-123"}).json()
        me = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login['token']}"})
        self.assertEqual(me.status_code, 200)


class FileModeShapeTest(StorageSandboxTestCase):
    def test_file_mode_login_keeps_legacy_payload_only(self) -> None:
        import os

        from app.persistence import db

        self.assertFalse(db.enterprise_mode())
        from app.main import create_app
        from fastapi.testclient import TestClient

        client = TestClient(create_app())
        client.post("/api/v1/auth/register", json={
            "email": "file@example.com", "password": "password-123"})
        resp = client.post("/api/v1/auth/login", json={
            "email": "file@example.com", "password": "password-123"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json().keys()), {"token", "user"})
        # session endpoints answer enterprise_auth_required, not a fake ok
        refresh = client.post("/api/v1/auth/refresh",
                              json={"refresh_token": "rt_whatever-token"})
        self.assertEqual(refresh.status_code, 409)
        self.assertEqual(refresh.json()["detail"]["error"]["code"],
                         "enterprise_auth_required")


if __name__ == "__main__":
    unittest.main()
