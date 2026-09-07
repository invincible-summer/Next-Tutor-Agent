#!/usr/bin/env python3
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.public_vector_artifact import DEFAULT_OUTPUT_DIR, build_public_vector_pack


def main() -> None:
    parser = argparse.ArgumentParser(description="Build verified public textbook vectors with the configured embedding model")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--shard-size", type=int, default=8192)
    args = parser.parse_args()
    manifest = asyncio.run(build_public_vector_pack(args.output, shard_size=args.shard_size))
    print(json.dumps({"status": "built", "output": str(args.output),
                      "manifest_sha256": manifest["manifest_sha256"],
                      "chunk_count": manifest["chunk_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
