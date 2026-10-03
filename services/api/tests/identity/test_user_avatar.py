"""Authenticated avatars, normalization and account lifecycle in the storage sandbox."""
from io import BytesIO
from unittest.mock import patch
import threading

from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from tests.support.storage_sandbox import StorageSandboxTestCase


class UserAvatarTests(StorageSandboxTestCase):
    def setUp(self):
        super().setUp()
        from app.identity import avatars, store
        from app.identity.security import create_token
        from app.main import create_app
        self.avatars = avatars
        self.store = store
        self.client = TestClient(create_app())
        self.addCleanup(self.client.close)
        self.a = store.create_user("a@example.com", "A", "test-hash")
        self.b = store.create_user("b@example.com", "B", "test-hash")
        self.a_headers = {"Authorization": f"Bearer {create_token(self.a.id)}"}
        self.b_headers = {"Authorization": f"Bearer {create_token(self.b.id)}"}

    def image(self, color="red", size=(600, 300), fmt="PNG"):
        output = BytesIO()
        info = PngImagePlugin.PngInfo()
        info.add_text("private_metadata", "must-not-survive")
        Image.new("RGB", size, color).save(output, format=fmt, pnginfo=info)
        return output.getvalue()

    def upload(self, data=None, headers=None, filename="avatar.png"):
        return self.client.put("/api/v1/user/avatar", headers=headers or self.a_headers,
                               files={"file": (filename, self.image() if data is None else data,
                                               "image/png")})

    def test_upload_normalizes_size_strips_metadata_and_reads_privately(self):
        response = self.upload(filename="../../another-user.png")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["profile"]["avatar"].startswith("avatar:"))
        read = self.client.get("/api/v1/user/avatar", headers=self.a_headers)
        self.assertEqual(read.headers["cache-control"], "private, no-store")
        self.assertEqual(read.headers["content-type"], "image/png")
        with Image.open(BytesIO(read.content)) as image:
            self.assertEqual(image.size, (256, 256))
            self.assertNotIn("private_metadata", image.info)
        self.assertEqual(list(self.avatars.owner_dir(self.a.id).iterdir()),
                         [self.avatars.owner_dir(self.a.id) / "avatar.png"])

    def test_no_guest_or_foreign_reads_and_client_cannot_select_owner(self):
        self.upload()
        for method in ("get", "delete"):
            self.assertEqual(getattr(self.client, method)("/api/v1/user/avatar").status_code, 401)
        self.assertEqual(self.client.put("/api/v1/user/avatar",
                         files={"file": ("a.png", self.image(), "image/png")}).status_code, 401)
        self.assertEqual(self.client.get(f"/api/v1/user/avatar?owner={self.a.id}",
                         headers=self.b_headers).status_code, 404)
        self.upload(self.image("blue"), self.b_headers)
        response = self.client.get(f"/api/v1/user/avatar?owner={self.a.id}", headers=self.b_headers)
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.getpixel((128, 128)), (0, 0, 255, 255))

    def test_rejects_fake_images_svg_gif_oversize_and_profile_injection(self):
        for data in (b"not-a-png", b"<svg xmlns='http://www.w3.org/2000/svg'/>", self.image(fmt="GIF")):
            self.assertEqual(self.upload(data).status_code, 422)
        self.assertEqual(self.upload(b"x" * (self.avatars.MAX_UPLOAD_BYTES + 1)).status_code, 413)
        for avatar in ("https://example.com/a.png", f"../{self.b.id}/avatar.png", "avatar:other"):
            response = self.client.put("/api/v1/user/profile", json={"avatar": avatar}, headers=self.a_headers)
            self.assertEqual(response.status_code, 422)
        self.assertFalse(self.avatars.owner_dir(self.a.id).exists())
        with patch.object(self.avatars, "MAX_PIXELS", 100):
            self.assertEqual(self.upload().status_code, 413)

    def test_removal_and_account_deletion_remove_directory_preserve_other_owner(self):
        self.upload()
        self.upload(self.image("blue"), self.b_headers)
        response = self.client.delete("/api/v1/user/avatar", headers=self.a_headers)
        self.assertEqual(response.json()["profile"]["avatar"], "")
        self.assertFalse(self.avatars.owner_dir(self.a.id).exists())
        self.upload()
        from app.core.account_data import purge_account, scan_storage
        self.assertGreater(scan_storage([self.a.id])[self.a.id]["avatar_bytes"], 0)
        purge_account(self.a.id)
        self.assertFalse(self.avatars.owner_dir(self.a.id).exists())
        self.assertTrue(self.avatars.owner_dir(self.b.id).exists())
        self.assertEqual(self.client.get("/api/v1/user/avatar", headers=self.a_headers).status_code, 401)

    def test_orphan_scan_and_late_upload_after_deletion(self):
        self.upload()
        orphan = self.avatars.owner_dir("orphan_test")
        orphan.mkdir(parents=True)
        (orphan / "avatar.png").write_bytes(self.image())
        from app.core.orphan_cleanup import scan_orphans, purge_orphans
        protected = [self.a.id, self.b.id]
        self.assertEqual(scan_orphans(protected)["categories"]["avatars"]["items"], 1)
        purge_orphans(protected, categories=["avatars"])
        self.assertFalse(orphan.exists())
        self.assertTrue(self.avatars.owner_dir(self.a.id).exists())
        normalized = self.avatars.normalize(self.image())
        from app.core.account_data import purge_account
        purge_account(self.a.id)
        with patch.object(self.avatars, "normalize", return_value=normalized):
            with self.assertRaisesRegex(ValueError, "account_not_found"):
                self.avatars.save(self.a.id, self.image())
        self.assertFalse(self.avatars.owner_dir(self.a.id).exists())

    def test_inflight_upload_waits_for_deletion_and_cannot_recreate_avatar(self):
        from app.core import account_data
        deleting, decoded, release_delete, upload_done = (threading.Event() for _ in range(4))
        failures = []
        normalized = self.avatars.normalize(self.image())

        def delete_owner(owner):
            deleting.set()
            if not release_delete.wait(3):
                raise TimeoutError("test did not release deletion")
            self.avatars.purge(owner)
            self.store.delete_user(owner)
            return {}

        def decode(data):
            decoded.set()
            return normalized

        def upload():
            try:
                self.avatars.save(self.a.id, self.image())
            except ValueError as exc:
                failures.append(str(exc))
            finally:
                upload_done.set()

        with patch.object(account_data, "_purge_account", side_effect=delete_owner), \
             patch.object(self.avatars, "normalize", side_effect=decode):
            deletion = threading.Thread(target=account_data.purge_account, args=(self.a.id,))
            deletion.start()
            self.assertTrue(deleting.wait(3))
            uploading = threading.Thread(target=upload)
            uploading.start()
            try:
                self.assertTrue(decoded.wait(3))
                self.assertFalse(upload_done.wait(0.1))
            finally:
                release_delete.set()
                deletion.join(3)
                uploading.join(3)
        self.assertFalse(deletion.is_alive())
        self.assertFalse(uploading.is_alive())
        self.assertEqual(failures, ["account_not_found"])
        self.assertFalse(self.avatars.owner_dir(self.a.id).exists())

    def test_settings_grade_updates_identity_and_student_model(self):
        from app.agents.student_model import get_student_model
        response = self.client.put("/api/v1/user/profile", json={"grade": "初中"}, headers=self.a_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.get_by_id(self.a.id).profile.grade, "初中")
        self.assertEqual(get_student_model(self.a.id).snapshot()["grade"], "初中")
        self.assertEqual(self.client.put("/api/v1/user/profile", json={"grade": "nonsense"}, headers=self.a_headers).status_code, 422)
