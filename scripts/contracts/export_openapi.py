#!/usr/bin/env python3
"""离线导出 FastAPI OpenAPI 快照（见 scripts/contracts/README.md）。

即使生产环境关闭 `/openapi.json`，共享契约也以仓库内确定性快照
packages/contracts/openapi/next-tutor.openapi.json 为准。导出时对
JSON 键排序，保证同一代码总是生成同一文件；CI 用 `--check` 防漂移。

用法：
  python scripts/contracts/export_openapi.py [--check]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = ROOT / "packages" / "contracts" / "openapi" / "next-tutor.openapi.json"


def build_snapshot() -> str:
    # Import the app the same way the offline test suite does: never read
    # developer .env secrets or require live provider keys; a fixed offline
    # secret prevents identity from trying to generate/persist a real one.
    os.environ.setdefault("EDU_TEST_KEYLESS", "1")
    os.environ.setdefault("AUTH_MODE", "0")
    os.environ.setdefault("AUTH_JWT_SECRET", "offline-contract-export-secret")
    sys.path.insert(0, str(ROOT / "services" / "api"))
    from app.main import app  # noqa: E402

    schema = app.openapi()
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True,
                      default=str) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="只比较快照与现有文件，不写入")
    args = parser.parse_args()

    content = build_snapshot()
    if args.check:
        if not OUT_PATH.exists() or OUT_PATH.read_text(encoding="utf-8") != content:
            print(f"[openapi] 快照与 {OUT_PATH} 不一致，请运行 export_openapi.py 重新导出",
                  file=sys.stderr)
            return 1
        print(f"[openapi] OK: {OUT_PATH} 与应用一致")
        return 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(content, encoding="utf-8")
    print(f"[openapi] 已导出 {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
