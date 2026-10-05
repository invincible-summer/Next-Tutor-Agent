#!/usr/bin/env python3
"""Read-only enumeration of the file runtime's migratable entities.

Prints counts and per-entity identifiers (emails stay hashed unless
--verbose, following the no-private-data-in-logs rule). Never touches the
database and never writes state — pure reconnaissance for import planning.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true",
                        help="show raw emails instead of hashes")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="machine-readable output")
    args = parser.parse_args(argv)

    snapshot = _common.snapshot_file_accounts()
    users: dict = snapshot.get("users") or {}
    by_id: dict = snapshot.get("by_id") or {}

    def _label(email: str) -> str:
        return email if args.verbose else hashlib.sha256(
            email.encode("utf-8")).hexdigest()[:12]

    entities = []
    for email, record in sorted(users.items()):
        entities.append({
            "user_id": record.get("id", ""),
            "email_label": _label(str(email)),
            "role": record.get("role", "student"),
            "has_password": bool(record.get("password_hash")),
            "token_version": record.get("token_version", 0),
            "created_at": record.get("created_at", 0.0),
        })

    result = {
        "accounts_file": str(_common.accounts_file()),
        "missing_source": bool(snapshot.get("_missing")),
        "source_hash": (None if snapshot.get("_missing")
                        else _common.file_hash(_common.accounts_file())),
        "user_count": len(users),
        "id_index_count": len(by_id),
        "users": entities,
    }
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"accounts file : {result['accounts_file']}")
        print(f"source hash  : {result['source_hash']}")
        print(f"users        : {result['user_count']} "
              f"(id index: {result['id_index_count']})")
        for entity in entities:
            print(f"  {entity['user_id']:<24} {entity['email_label']:<14} "
                  f"role={entity['role']:<8} "
                  f"pwd={'y' if entity['has_password'] else 'n'} "
                  f"ver={entity['token_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
