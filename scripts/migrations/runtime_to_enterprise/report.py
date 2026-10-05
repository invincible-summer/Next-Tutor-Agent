#!/usr/bin/env python3
"""Human/JSON summary of the migration state and both sides' counts.

Read-only: the state file, the file-runtime counts and (when DATABASE_URL is
configured) live enterprise counts. Designed as the pre-cutover review
artifact.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402


async def _db_counts() -> dict | None:
    from app.persistence import db

    if not db.enterprise_mode():
        return None
    repo = _common.repository()
    accounts = await repo.list_accounts()
    memberships = 0
    tenants = 0
    for account in accounts:
        tenant_ids = await repo.list_memberships_for_user(account.user_id)
        memberships += len(tenant_ids)
        tenants += len({m.tenant_id for m in tenant_ids})
    return {"users": len(accounts), "memberships": memberships,
            "tenants": tenants}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    state = _common.load_state()
    snapshot = _common.snapshot_file_accounts()
    db_counts = asyncio.run(_db_counts())
    report = {
        "state_path": str(_common.state_path()),
        "state": state,
        "file": {
            "users": len(snapshot.get("users") or {}),
            "source_hash": (None if snapshot.get("_missing")
                            else _common.file_hash(_common.accounts_file())),
        },
        "enterprise": db_counts,
    }
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"state        : {report['state_path']}")
        print(f"file users   : {report['file']['users']} "
              f"(hash {report['file']['source_hash']})")
        imports = state.get("imports") or []
        if imports:
            last = imports[-1]
            print(f"last import  : created={last.get('created')} "
                  f"skipped={last.get('skipped_existing')} "
                  f"conflicts={len(last.get('conflicts') or [])} "
                  f"at={last.get('imported_at')}")
        else:
            print("last import  : none")
        cutover = state.get("cutover")
        print(f"cutover      : "
              f"{'at ' + str(cutover['at']) if cutover else 'not recorded'}")
        print(f"enterprise   : {db_counts or 'not configured (file mode)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
