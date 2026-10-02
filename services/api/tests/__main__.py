"""Run unittest in a process-wide storage sandbox: ``python -m tests [names]``.

Individual fixtures still own their test isolation. The outer sandbox also
contains legacy fixtures and temporary files, so a full local run cannot write
to the application's runtime data or leave abandoned mkdtemp directories.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tests.storage_sandbox import patch_all_storage_roots, reset_shared_caches


def main() -> int:
    backend = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="tutor_tests_") as directory:
        root = Path(directory)
        scratch = root / "tmp"
        scratch.mkdir()
        previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(scratch)
        patches = []
        try:
            patches = patch_all_storage_roots(root)
            loader = unittest.TestLoader()
            suite = (loader.loadTestsFromNames(sys.argv[1:]) if sys.argv[1:]
                     else loader.discover(str(backend / "tests"),
                                          top_level_dir=str(backend)))
            if not suite.countTestCases():
                print("No tests found", file=sys.stderr)
                return 1
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            return 0 if result.wasSuccessful() else 1
        finally:
            try:
                reset_shared_caches()
            finally:
                for patcher in reversed(patches):
                    patcher.stop()
                tempfile.tempdir = previous_tempdir


if __name__ == "__main__":
    raise SystemExit(main())
