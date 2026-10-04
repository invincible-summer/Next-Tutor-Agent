"""runtime_to_enterprise toolkit contract (sandboxed sqlite target).

Drives the real CLIs as subprocesses against a sandbox data root with two
synthetic file accounts: scan is read-only, import is idempotent (re-run
creates nothing new), verify passes, cutover refuses without confirmation /
with a changed source, and the file runtime is never mutated.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.storage_sandbox import StorageSandboxTestCase

REPO = Path(__file__).resolve().parents[4]
TOOLKIT = REPO / "scripts" / "migrations" / "runtime_to_enterprise"


class RuntimeImportToolkitTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        from app.identity import store as identity_store

        identity_store.create_user(
            email="one@example.com", username="one",
            password_hash="$2b$12$examplehash1", user_id="usr_importone")
        identity_store.create_user(
            email="two@example.com", username="two",
            password_hash="$2b$12$examplehash2", user_id="usr_importtwo",
            role="teacher")
        self._accounts_snapshot = (self.root / "users" /
                                   "accounts.json").read_bytes()

        self._db_path = self.root / "import_target.db"
        self._saved: dict[str, str | None] = {
            key: os.environ.get(key)
            for key in ("DATABASE_URL", "NEXT_TUTOR_DATA_DIR")}
        os.environ["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{self._db_path.as_posix()}")
        os.environ["NEXT_TUTOR_DATA_DIR"] = str(self.root)

    def tearDown(self) -> None:
        for key, old in self._saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        super().tearDown()

    def _run_cli(self, script: Path, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, cwd=str(REPO))

    def _migrate_target(self) -> None:
        from alembic.config import main as alembic_main

        cwd = os.getcwd()
        os.chdir(REPO / "services" / "api")
        try:
            alembic_main(["upgrade", "head"])
        finally:
            os.chdir(cwd)

    def test_scan_import_verify_cutover_flow(self) -> None:
        scan = self._run_cli(TOOLKIT / "scan.py", ["--json"])
        self.assertEqual(scan.returncode, 0, scan.stderr)
        scan_body = json.loads(scan.stdout)
        self.assertEqual(scan_body["user_count"], 2)
        # privacy: emails hashed by default
        self.assertNotIn("one@example.com", scan.stdout)

        self._migrate_target()

        first = self._run_cli(TOOLKIT / "import.py", [])
        self.assertEqual(first.returncode, 0, first.stderr)
        first_body = json.loads(first.stdout)
        self.assertEqual(first_body["created"], 2)
        self.assertEqual(first_body["skipped_existing"], 0)

        # idempotent: second run creates nothing
        second = self._run_cli(TOOLKIT / "import.py", [])
        self.assertEqual(second.returncode, 0, second.stderr)
        second_body = json.loads(second.stdout)
        self.assertEqual(second_body["created"], 0)
        self.assertEqual(second_body["skipped_existing"], 2)

        verify = self._run_cli(TOOLKIT / "verify.py", ["--json"])
        self.assertEqual(verify.returncode, 0, verify.stderr)
        verify_body = json.loads(verify.stdout)
        self.assertTrue(verify_body["ok"], verify_body["problems"])
        self.assertEqual(verify_body["db_users"], 2)

        # cutover refuses without confirmation
        refused = self._run_cli(TOOLKIT / "cutover.py", [])
        self.assertEqual(refused.returncode, 2)
        # and records with it
        armed = self._run_cli(TOOLKIT / "cutover.py",
                              ["--confirm", "enterprise-cutover"])
        self.assertEqual(armed.returncode, 0, armed.stderr)

        report = self._run_cli(TOOLKIT / "report.py", ["--json"])
        self.assertEqual(report.returncode, 0, report.stderr)
        report_body = json.loads(report.stdout)
        self.assertEqual(report_body["file"]["users"], 2)
        self.assertEqual(report_body["enterprise"]["users"], 2)
        self.assertIn("cutover", report_body["state"])

        # the file runtime is byte-identical after the whole flow
        self.assertEqual(
            (self.root / "users" / "accounts.json").read_bytes(),
            self._accounts_snapshot)

    def test_cutover_refuses_when_source_changed_after_import(self) -> None:
        self._migrate_target()
        self.assertEqual(
            self._run_cli(TOOLKIT / "import.py", []).returncode, 0)

        from app.identity import store as identity_store

        identity_store.create_user(
            email="three@example.com", username="three",
            password_hash="$2b$12$examplehash3", user_id="usr_importthree")
        changed = self._run_cli(TOOLKIT / "cutover.py",
                                ["--confirm", "enterprise-cutover"])
        self.assertEqual(changed.returncode, 2)
        self.assertIn("changed since the last import", changed.stdout)


if __name__ == "__main__":
    unittest.main()
