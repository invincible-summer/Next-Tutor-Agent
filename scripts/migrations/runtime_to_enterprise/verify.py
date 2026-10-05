#!/usr/bin/env python3
"""Cross-side verification: counts, hashes and referential integrity.

Compares the file runtime snapshot against the enterprise database:

- count parity (users) and per-user field parity (email/role/token_version/
  profile canonical hash) for every imported account;
- every user has a personal tenant + owner membership + password credential;
- every membership references an existing user and tenant;
- users.active_tenant_id points at a tenant the user holds membership in.

Exit code 0 = consistent; 1 = mismatches (listed, never silent). Read-only
on both sides; safe to run as often as needed (shadow-verification step).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402


async def _verify() -> tuple[dict, list[str]]:
    _common.require_enterprise_database()
    repo = _common.repository()
    snapshot = _common.snapshot_file_accounts()
    users: dict = snapshot.get("users") or {}

    problems: list[str] = []
    db_accounts = {a.user_id: a for a in await repo.list_accounts()}

    # count parity: every file user present in the database (extra database
    # users are allowed — post-import registrations live there too).
    for email, record in sorted(users.items()):
        user_id = str(record.get("id", ""))
        account = db_accounts.get(user_id)
        if account is None:
            problems.append(f"missing_db_user:{user_id}")
            continue
        if account.email != str(email).strip().lower():
            problems.append(f"email_mismatch:{user_id}")
        if account.role != str(record.get("role", "student")):
            problems.append(f"role_mismatch:{user_id}")
        if account.token_version != int(record.get("token_version", 0) or 0):
            problems.append(f"token_version_mismatch:{user_id}")
        if (_common.canonical_hash(account.profile)
                != _common.canonical_hash(record.get("profile") or {})):
            problems.append(f"profile_hash_mismatch:{user_id}")

        membership = await repo.get_membership(
            _common.stable_id("tnt", user_id), user_id)
        if membership is None:
            problems.append(f"missing_membership:{user_id}")
        elif membership.tenant_role != "owner":
            problems.append(f"membership_role_mismatch:{user_id}")
        if account.active_tenant_id != _common.stable_id("tnt", user_id):
            problems.append(f"active_tenant_mismatch:{user_id}")
        credential = await repo.get_password_credential(user_id)
        if credential is None and record.get("password_hash"):
            problems.append(f"missing_credential:{user_id}")

    # referential integrity across the whole database side
    tenant_ids: set[str] = set()
    for account in db_accounts.values():
        memberships = await repo.list_memberships_for_user(account.user_id)
        for membership in memberships:
            tenant_ids.add(membership.tenant_id)
            if membership.user_id not in db_accounts:
                problems.append(
                    f"membership_dangling_user:{membership.membership_id}")
        if account.active_tenant_id and account.active_tenant_id not in {
                m.tenant_id for m in memberships}:
            problems.append(
                f"active_tenant_not_member:{account.user_id}")
    for tenant_id in tenant_ids:
        if await repo.get_tenant(tenant_id) is None:
            problems.append(f"membership_dangling_tenant:{tenant_id}")

    summary = {
        "file_users": len(users),
        "db_users": len(db_accounts),
        "problems": problems,
        "ok": not problems,
    }
    return summary, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    summary, problems = asyncio.run(_verify())
    if args.as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"file users : {summary['file_users']}")
        print(f"db users   : {summary['db_users']}")
        print(f"status     : {'ok' if summary['ok'] else 'MISMATCH'}")
        for problem in problems:
            print(f"  ! {problem}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
