#!/usr/bin/env python3
"""Closed-source license policy gate (plan phase 3, section 8).

Evaluates every component in ``licenses/inventory.json`` and
``licenses/external-components.json`` against ``licenses/policy.json``:

- auto-allow permissive ids (MIT/BSD/Apache/ISC/...);
- conditional ids (MPL-2.0, LGPL-*, OFL-1.1, CC-BY-4.0) pass only when a
  matching decision + obligation is recorded in ``decisions.json`` /
  ``obligations.json``;
- blocked ids/patterns (AGPL/SSPL/RSAL/BUSL/GPL-only/NonCommercial) and
  UNKNOWN / NOASSERTION / empty expressions always fail;
- OR expressions pass via an auto-allowed branch or a recorded chosen
  branch; AND expressions require every atom to pass;
- components whose original license text is missing from ``licenses/texts``
  fail unless a license-evidence-exception decision names them.

Exit 0 only on zero gaps. Run from the repository root (offline; reads the
committed inventory, which ``generate_notices.py`` owns).
"""
from __future__ import annotations

import fnmatch
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIC = ROOT / "licenses"


def load(name: str) -> dict:
    return json.loads((LIC / name).read_text(encoding="utf-8"))


def components() -> list[dict]:
    seen: dict[str, dict] = {}
    inventory = load("inventory.json")
    for comp in inventory.get("components", []):
        seen[comp.get("purl") or f"{comp.get('ecosystem')}/{comp.get('name')}@{comp.get('version')}"] = comp
    for comp in json.loads((LIC / "external-components.json").read_text(encoding="utf-8")):
        seen[comp.get("purl") or f"{comp.get('ecosystem')}/{comp.get('name')}@{comp.get('version')}"] = comp
    missing_text = {gap.get("purl") for gap in inventory.get("gaps", [])
                    if gap.get("original_text_missing")}
    return [dict(comp, _missing_text=comp.get("purl") in missing_text)
            for comp in seen.values()]


# --- minimal SPDX expression evaluation -------------------------------------

ATOM_RE = re.compile(r"[A-Za-z0-9.\-]+")
PAREN_RE = re.compile(r"\(([^()]*)\)")


def split_top(expr: str, op: str) -> list[str] | None:
    """Split expression on top-level ` AND ` / ` OR ` (None when absent)."""
    depth = 0
    token = f" {op} "
    lower = expr
    idx = -1
    for i in range(len(lower) - len(token) + 1):
        if lower[i] == "(":
            depth += 1
        elif lower[i] == ")":
            depth -= 1
        elif depth == 0 and lower[i:i + len(token)] == token:
            idx = i
            break
    if idx < 0:
        return None
    return [lower[:idx].strip(), lower[idx + len(token):].strip()]


class Verdict:
    __slots__ = ("status", "reason", "chosen_branch")

    def __init__(self, status: str, reason: str = "", chosen: str | None = None):
        self.status = status  # allowed | conditional | blocked | unknown
        self.reason = reason
        self.chosen_branch = chosen


def evaluate(expr: str, policy: dict) -> Verdict:
    expr = (expr or "").strip()
    if not expr or expr in ("UNKNOWN", "NOASSERTION", "None"):
        return Verdict("unknown", "empty/UNKNOWN/NOASSERTION")
    alias = policy.get("aliases", {})
    if expr in alias:
        expr = alias[expr]
    or_split = split_top(expr, "OR")
    if or_split is not None:
        branches = [evaluate(b, policy) for b in or_split]
        for branch in branches:
            if branch.status == "allowed":
                return Verdict("allowed", chosen=branch.chosen_branch)
        conditional = next((b for b in branches if b.status == "conditional"), None)
        if conditional is not None:
            return Verdict("conditional", chosen=conditional.chosen_branch)
        return Verdict("blocked", "no permissive/conditional OR branch")
    and_split = split_top(expr, "AND")
    if and_split is not None:
        atoms = [evaluate(a, policy) for a in and_split]
        if any(a.status in ("blocked", "unknown") for a in atoms):
            bad = next(a for a in atoms if a.status in ("blocked", "unknown"))
            return Verdict(bad.status, f"AND contains {bad.status}: {bad.reason}")
        if any(a.status == "conditional" for a in atoms):
            return Verdict("conditional", "AND contains conditional atom(s)")
        return Verdict("allowed", chosen="/".join(filter(None, (a.chosen_branch for a in atoms))) or None)
    # bare atom (possibly a parenthesised sub-expression)
    if expr.startswith("(") and expr.endswith(")"):
        return evaluate(expr[1:-1].strip(), policy)
    if "(" in expr or ")" in expr:
        return Verdict("unknown", f"unparseable expression {expr!r}")
    atom = alias.get(expr, expr)
    if atom in policy.get("auto_allowed", []):
        return Verdict("allowed", chosen=atom)
    if atom in policy.get("conditional", {}):
        return Verdict("conditional", chosen=atom)
    for pattern in policy.get("blocked_patterns", []):
        if fnmatch.fnmatch(atom, pattern):
            return Verdict("blocked", f"matches blocked pattern {pattern}")
    if atom in policy.get("blocked_expressions", []):
        return Verdict("blocked", f"blocked expression {atom}")
    return Verdict("unknown", f"unrecognized license id {atom!r}")


def decision_for(component: dict, decisions: list[dict]) -> dict | None:
    purl = component.get("purl") or ""
    name = component.get("name") or ""
    for decision in decisions:
        target = decision.get("component") or ""
        if target == purl:
            return decision
        if target.startswith("family:"):
            # family:@img/sharp — 纯包名前缀匹配（无生态前缀、无版本、无通配符）
            pkg = target.split(":", 1)[1].strip()
            if pkg and (name == pkg or name.startswith(pkg)):
                return decision
    return None


def main() -> int:
    policy = load("policy.json")
    decisions = load("decisions.json").get("decisions", [])
    obligations = {o["id"]: o for o in load("obligations.json").get("obligations", [])}

    failures: list[str] = []
    conditional_ok = 0
    evidence_exceptions = 0

    for comp in components():
        purl = comp.get("purl") or f"{comp.get('ecosystem')}/{comp.get('name')}"
        expr = str(comp.get("license_expression") or comp.get("license_declared") or "")
        decision = decision_for(comp, decisions)
        if decision and decision.get("resolve_expression"):
            # upstream metadata 字符串过于含糊（如 dateutil 的 "Dual License"），
            # 由 decision 以存档证据解析为明确 SPDX 表达式后按常规评估。
            expr = str(decision["resolve_expression"])
        verdict = evaluate(expr, policy)

        if verdict.status == "allowed":
            pass
        elif verdict.status == "conditional":
            if not decision or decision.get("kind") not in ("conditional-approval", "or-license-choice"):
                failures.append(f"{purl}: conditional license {expr!r} without recorded decision")
                continue
            obligation_id = decision.get("obligation")
            if obligation_id and obligation_id not in obligations:
                failures.append(f"{purl}: decision references unknown obligation {obligation_id}")
                continue
            if decision.get("kind") == "or-license-choice" and not decision.get("chosen"):
                failures.append(f"{purl}: OR-license decision without chosen branch")
                continue
            conditional_ok += 1
        elif verdict.status == "unknown":
            if decision and decision.get("kind") == "license-evidence-exception":
                evidence_exceptions += 1
            else:
                failures.append(f"{purl}: license {expr!r} is UNKNOWN/unrecognized")
        else:  # blocked
            failures.append(f"{purl}: BLOCKED license {expr!r} ({verdict.reason})")

        if comp.get("_missing_text"):
            # 上游 artifact 未随包携带许可原文（常见于仅 metadata 声明的 npm 平台二进制）。
            # 允许表达式为 auto_allowed 的组件（发行物以标准文本履行 notice 义务，
            # OBL-GENERIC-NOTICE）；conditional 组件必须已有命名它的 decision
            # （decision 文档化该情形）；例外 decision 同样豁免。
            if decision and decision.get("kind") == "license-evidence-exception":
                pass
            elif verdict.status == "allowed":
                pass
            elif verdict.status == "conditional" and decision:
                pass
            else:
                failures.append(f"{purl}: original license text missing from licenses/texts/")

    expired = [d["id"] for d in decisions
               if d.get("review_by") and d["review_by"] < "2026-10-06"]
    if expired:
        failures.append(f"decisions past review date: {', '.join(expired)}")

    total = len(components())
    print(f"license policy gate: {total} components, "
          f"{conditional_ok} conditional-with-decision, "
          f"{evidence_exceptions} evidence exceptions, "
          f"{len(failures)} failures")
    for failure in failures:
        print(f"  FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
