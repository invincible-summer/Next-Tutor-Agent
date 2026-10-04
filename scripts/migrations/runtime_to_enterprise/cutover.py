#!/usr/bin/env python3
"""Explicit cutover marker — never deletes the file runtime.

Cutover is an operator decision with a paper trail, not a side effect of
importing: this records ``cutover_at`` (plus the verified source hash) in
the migration state and prints the rollback-window guidance. Old file data
is retained read-only until an explicit, separate cleanup after the window —
this tool refuses to do that cleanup.

Preconditions: a recorded import whose source hash matches the current file
runtime, and a passing cross-side verification.
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
import verify as verify_mod  # noqa: E402


async def _cutover(confirmed: str) -> int:
    _common.require_enterprise_database()
    if confirmed != "enterprise-cutover":
        print("Refusing to cut over: pass --confirm enterprise-cutover "
              "after review (see report.py first).")
        return 2

    snapshot = _common.snapshot_file_accounts()
    current_hash = (None if snapshot.get("_missing")
                    else _common.file_hash(_common.accounts_file()))
    state = _common.load_state()
    imports = state.get("imports") or []
    if not imports:
        print("No import recorded for this runtime: run import.py first.")
        return 2
    last = imports[-1]
    if last.get("source_hash") != current_hash:
        print("File runtime changed since the last import "
              f"(imported {last.get('source_hash')}, now {current_hash}): "
              "re-run import.py + verify.py before cutover.")
        return 2
    if last.get("conflicts"):
        print("Last import recorded conflicts: resolve them before cutover.")
        return 2

    summary, _problems = await verify_mod._verify()
    if not summary["ok"]:
        print("Verification failed; cutover refused. See verify.py output.")
        return 1

    state["cutover"] = {
        "at": time.time(),
        "source_hash": current_hash,
        "file_users": summary["file_users"],
        "db_users": summary["db_users"],
    }
    _common.save_state(state)
    print("Cutover recorded. The file runtime stays in place, read-only, "
          "for the rollback window; removing it is a separate, explicit "
          "maintenance decision outside this toolkit.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", default="",
                        help="literal 'enterprise-cutover' to arm the switch")
    args = parser.parse_args(argv)
    return asyncio.run(_cutover(args.confirm))


if __name__ == "__main__":
    raise SystemExit(main())
