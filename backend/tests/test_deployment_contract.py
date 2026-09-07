"""Regression checks for production templates consumed directly by systemd."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


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
        for path in (
            "/opt/edu-agent/students",
            "/opt/edu-agent/users",
            "/opt/edu-agent/notes",
            "/opt/edu-agent/chat_history",
            "/opt/edu-agent/knowledge",
            "/opt/edu-agent/backend/traces",
            "/opt/edu-agent/backend/uploads",
            "/var/lib/edu-agent",
        ):
            self.assertIn(path, unit)

    def test_frontend_unit_passes_next_arguments_without_separator(self) -> None:
        unit = (ROOT / "deploy" / "edu-frontend.service").read_text(encoding="utf-8")
        self.assertIn(
            "ExecStart=/usr/bin/env pnpm exec next start "
            "-H 127.0.0.1 -p 3030",
            unit,
        )
        self.assertIn("WorkingDirectory=/opt/edu-agent/frontend", unit)
        self.assertIn("ReadWritePaths=/opt/edu-agent/frontend/.next", unit)
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

    def test_manual_uses_paper_agent_account_layout(self) -> None:
        manual = (ROOT / "docs" / "The_Website_deployment_plan.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`edu-agent` / `edu-agent`", manual)
        self.assertIn("home `/var/lib/edu-agent`", manual)
        self.assertIn("--shell /usr/sbin/nologin edu-agent", manual)
        self.assertIn("-m 0755 /opt/edu-agent", manual)
        self.assertIn("NoNewPrivileges=true", manual)
        self.assertIn("ProtectSystem=strict", manual)
        migration_start = manual.index("### 4.1 将旧 `eduagent` 实例迁移")
        migration_end = manual.index("## 5. GitHub", migration_start)
        steady_state = manual[:migration_start] + manual[migration_end:]
        self.assertNotIn("/home/eduagent", steady_state)
        self.assertNotIn("sudo -u eduagent", steady_state)
        migration = manual[migration_start:migration_end]
        self.assertIn("usermod --login edu-agent eduagent", migration)
        self.assertIn(
            "usermod --home /var/lib/edu-agent --move-home edu-agent",
            migration,
        )
        self.assertIn("sudo -u edu-agent -H sed -i", migration)
        self.assertIn("pnpm exec next start -H 127.0.0.1 -p 3030", manual)
        self.assertNotIn("next start -- -H", manual)
        self.assertIn(
            "/var/www/edu-agent-acme/.well-known/acme-challenge/health-check",
            manual,
        )
        self.assertNotIn(
            "tee /var/www/edu-agent-acme/health-check",
            manual,
        )

    def test_manual_documents_safe_env_only_updates(self) -> None:
        manual = (ROOT / "docs" / "The_Website_deployment_plan.md").read_text(
            encoding="utf-8"
        )
        section_start = manual.index("### 13.2 只更新云服务器生产 `.env`")
        section_end = manual.index("### 13.3 服务器按 commit 精确更新", section_start)
        section = manual[section_start:section_end]
        self.assertIn("本地 `.env` 的修改**不会自动同步到生产**", section)
        self.assertIn("/var/lib/edu-agent/backup/env_", section)
        self.assertIn("sudoedit /opt/edu-agent/.env", section)
        self.assertIn("AUTH_JWT_SECRET is non-default", section)
        self.assertIn("systemctl restart edu-agent-backend.service", section)
        self.assertIn("using_default_secret=false", section)
        self.assertNotIn("systemctl restart edu-agent-frontend.service", section)
        self.assertNotIn("systemctl reload nginx", section)
        self.assertNotIn("cat /opt/edu-agent/.env", section)


if __name__ == "__main__":
    unittest.main()
