#!/usr/bin/env python3
"""Idempotent identity import: file accounts → enterprise PostgreSQL.

For every file account (users/accounts.json) this creates, when missing:

- ``users`` row (same user_id/email/role/token_version/profile)
- a personal ``tenants`` row + owner ``memberships`` row (deterministic ids
  hashed from the user_id, so re-runs converge)
- a ``credentials`` row (kind=password, the existing bcrypt hash)

Existing matching rows are skipped, never overwritten: the import fills the
enterprise side, it does not reconcile later file-side drift (re-running
after new logins converges again). The file runtime is never mutated or
deleted here — cutover.py owns that decision explicitly.

State (source hash, counts, conflicts) is recorded under the data root.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402


async def _import_all(dry_run: bool) -> dict:
    from app.persistence.repositories.records import (AccountRecord,
                                                      CredentialRecord,
                                                      MembershipRecord,
                                                      TenantRecord)
    from app.persistence.repositories.protocols import DuplicateEmailError

    _common.require_enterprise_database()
    repo = _common.repository()
    snapshot = _common.snapshot_file_accounts()
    users: dict = snapshot.get("users") or {}
    source_hash = (None if snapshot.get("_missing")
                   else _common.file_hash(_common.accounts_file()))

    created = skipped = 0
    conflicts: list[dict] = []
    for email, record in sorted(users.items()):
        user_id = str(record.get("id", ""))
        email_key = str(email)
        if not user_id:
            conflicts.append({"email_label": email_key, "reason": "no_user_id"})
            continue
        existing_by_id = await repo.get_account_by_id(user_id)
        existing_by_email = await repo.get_account_by_email(email_key)
        if existing_by_id is not None:
            skipped += 1
            continue
        if existing_by_email is not None:
            conflicts.append({
                "user_id": user_id, "reason": "email_owned_by_other_user",
                "existing_user_id": existing_by_email.user_id})
            continue
        if dry_run:
            created += 1
            continue

        now = time.time()
        tenant_id = _common.stable_id("tnt", user_id)
        account = AccountRecord(
            user_id=user_id, email=email_key,
            username=str(record.get("username", "")),
            role=str(record.get("role", "student")),
            token_version=int(record.get("token_version", 0) or 0),
            created_at=float(record.get("created_at", 0.0) or 0.0),
            last_login_at=float(record.get("last_login_at", 0.0) or 0.0),
            profile=dict(record.get("profile") or {}),
            active_tenant_id=tenant_id)
        try:
            await repo.create_account(account)
        except DuplicateEmailError:
            conflicts.append({"user_id": user_id,
                              "reason": "duplicate_email"})
            continue
        await repo.create_tenant(TenantRecord(
            tenant_id=tenant_id, kind="personal",
            name=f"personal:{email_key}",
            display_name=str(record.get("username", "")),
            owner_user_id=user_id, created_at=now))
        await repo.create_membership(MembershipRecord(
            membership_id=_common.stable_id("mem", user_id),
            tenant_id=tenant_id, user_id=user_id, tenant_role="owner",
            created_at=now))
        if record.get("password_hash"):
            await repo.create_credential(CredentialRecord(
                id=_common.stable_id("crd", user_id), user_id=user_id,
                kind="password",
                secret_hash=str(record.get("password_hash", "")),
                created_at=now, updated_at=now))
        created += 1

    return {
        "source_hash": source_hash,
        "file_users": len(users),
        "created": created,
        "skipped_existing": skipped,
        "conflicts": conflicts,
        "dry_run": dry_run,
        "imported_at": time.time(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be imported, change nothing")
    args = parser.parse_args(argv)

    result = asyncio.run(_import_all(args.dry_run))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.dry_run:
        state = _common.load_state()
        state.setdefault("imports", []).append(result)
        _common.save_state(state)
    return 1 if result["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
