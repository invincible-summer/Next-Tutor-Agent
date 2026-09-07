#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.public_vector_artifact import DEFAULT_OUTPUT_DIR, import_public_vector_pack


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify/import public textbook vectors into local Chroma")
    parser.add_argument("--pack", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    print(json.dumps(import_public_vector_pack(args.pack), ensure_ascii=False))


if __name__ == "__main__":
    main()
