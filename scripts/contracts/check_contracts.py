#!/usr/bin/env python3
"""Public API 契约 lint：扫描缺失 response schema 的 endpoint（见 scripts/contracts/README.md）。

规则：每个 public REST endpoint 必须声明稳定的 Pydantic response model
（OpenAPI 中 2xx 响应带 content schema），或显式 204 无正文。存量缺口登记
在 contract_gaps.json 豁免基线中，不阻断；但基线是“只能收缩”的棘轮：

  - 新 endpoint 缺 response schema -> 失败；
  - 已修好的 endpoint 仍留在基线 -> 失败（提醒删除豁免项）。

用法：
  python scripts/contracts/check_contracts.py                # 检查（CI）
  python scripts/contracts/check_contracts.py --update-baseline  # 重新生成基线
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).resolve().parent / "contract_gaps.json"


def collect_openapi() -> dict:
    os.environ.setdefault("EDU_TEST_KEYLESS", "1")
    os.environ.setdefault("AUTH_MODE", "0")
    os.environ.setdefault("AUTH_JWT_SECRET", "offline-contract-export-secret")
    sys.path.insert(0, str(ROOT / "services" / "api"))
    from app.main import app  # noqa: E402
    return app.openapi()


def has_response_schema(operation: dict) -> bool:
    for code, response in (operation.get("responses") or {}).items():
        if not code.startswith("2"):
            continue
        if str(code) == "204" or "no content" in str(response.get("description", "")).lower():
            return True
        content = response.get("content") or {}
        for media in content.values():
            if "schema" in media:
                return True
    return False


def current_gaps(schema: dict) -> set[str]:
    gaps: set[str] = set()
    for path, methods in sorted(schema.get("paths", {}).items()):
        for method, operation in sorted(methods.items()):
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            endpoint_id = f"{method.upper()} {path}"
            if not has_response_schema(operation):
                gaps.add(endpoint_id)
    return gaps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-baseline", action="store_true",
                        help="用当前扫描结果重写豁免基线")
    args = parser.parse_args()

    gaps = current_gaps(collect_openapi())
    if args.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps({"missing_response_schema": sorted(gaps)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"[contracts] 基线已更新: {len(gaps)} 个豁免 endpoint")
        return 0

    baseline: set[str] = set()
    if BASELINE_PATH.exists():
        baseline = set(json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
                       .get("missing_response_schema", []))

    new_gaps = gaps - baseline
    stale = baseline - gaps
    failed = False
    if new_gaps:
        print("[contracts] 新 endpoint 缺少 response schema（声明 response_model 或 204）:",
              file=sys.stderr)
        for endpoint in sorted(new_gaps):
            print(f"  + {endpoint}", file=sys.stderr)
        failed = True
    if stale:
        print("[contracts] 以下 endpoint 已有 response schema，请从 contract_gaps.json 删除豁免:",
              file=sys.stderr)
        for endpoint in sorted(stale):
            print(f"  - {endpoint}", file=sys.stderr)
        failed = True
    if not failed:
        print(f"[contracts] OK: 契约 lint 通过（豁免基线 {len(baseline)} 项，无新增缺口）")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
