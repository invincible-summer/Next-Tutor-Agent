# math-workbench (domain)

Pure TypeScript math kernel for the geometry workbench (`/tools/geometry`).
No React, DOM, Three.js, Worker or storage APIs belong here (ADR-0021).

- Canonical architecture doc: [`docs/architecture/math-workbench.md`](../../../../docs/architecture/math-workbench.md)
- Expression engine: tokenizer → Pratt parser → symbol validation → bounded evaluator (`expression.ts`)
- Document schema v1, command reducer, allowlist import validation, semantic fingerprint (`model.ts`, `document.ts`)
- Calculus, equations, plane geometry, 2D samplers, 3D mesh generators and the math↔Three space adapter live in sibling files; budgets and tolerances are centralized in `diagnostics.ts`.
- Tests: `tests/math-workbench-*.test.ts` run via the package `test` script.
