"""Regression checks for production templates consumed directly by systemd."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]

class DeploymentContractTests(unittest.TestCase):
    def test_units_follow_paper_agent_system_account_model(self) -> None:
        backend = (ROOT / "deploy" / "edu-backend.service").read_text(
            encoding="utf-8"
        )
        frontend = (ROOT / "deploy" / "edu-frontend.service").read_text(
            encoding="utf-8"
        )
        for unit in (backend, frontend):
            self.assertIn("User=edu-agent", unit)
            self.assertIn("Group=edu-agent", unit)
            self.assertIn("Environment=HOME=/var/lib/edu-agent", unit)
            self.assertIn("NoNewPrivileges=true", unit)
            self.assertIn("PrivateTmp=true", unit)
            self.assertIn("ProtectSystem=strict", unit)
            self.assertIn("ProtectHome=true", unit)
            self.assertIn("Restart=always", unit)
            self.assertNotIn("User=eduagent", unit)
            self.assertNotIn("/home/eduagent", unit)

    def test_backend_unit_whitelists_all_runtime_storage_roots(self) -> None:
        unit = (ROOT / "deploy" / "edu-backend.service").read_text(
            encoding="utf-8"
        )
        # 单一运行数据根：全部存储目录由 NEXT_TUTOR_DATA_DIR 派生。
        self.assertIn("Environment=NEXT_TUTOR_DATA_DIR=/var/lib/edu-agent/data", unit)
        self.assertIn("ReadWritePaths=/var/lib/edu-agent", unit)

    def test_frontend_unit_passes_next_arguments_without_separator(self) -> None:
        unit = (ROOT / "deploy" / "edu-frontend.service").read_text(encoding="utf-8")
        self.assertIn(
            "ExecStart=/usr/bin/env pnpm exec next start "
            "-H 127.0.0.1 -p 3030",
            unit,
        )
        self.assertIn("WorkingDirectory=/opt/edu-agent/apps/web", unit)
        self.assertIn("ReadWritePaths=/opt/edu-agent/apps/web/.next", unit)
        self.assertNotIn("next start -- -H", unit)

    def test_env_example_has_no_inline_assignment_comments(self) -> None:
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        offenders = [
            line.split("=", 1)[0]
            for line in env_example.splitlines()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*\s+#", line)
        ]
        self.assertEqual([], offenders)

    def test_renewal_hook_is_scoped_to_edu_certificate(self) -> None:
        hook = (ROOT / "deploy" / "edu-agent-nginx-reload").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "/etc/letsencrypt/live/edu-agent.invincible-summer.xyz",
            hook,
        )
        self.assertIn("/usr/sbin/nginx -t", hook)
        self.assertIn("/usr/bin/systemctl reload nginx.service", hook)
        self.assertIn("${RENEWED_LINEAGE:-}", hook)




if __name__ == "__main__":
    unittest.main()
