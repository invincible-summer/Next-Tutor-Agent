# math-workbench (web)

Browser workbench UI for `/tools/geometry`: document state machine, 2D SVG
stage, lazily loaded Three.js 3D stage, expression/object/calculator panels,
explicit local-file persistence and the unsaved-changes coordinator.

- Canonical architecture doc: [`docs/architecture/math-workbench.md`](../../../../../../../docs/architecture/math-workbench.md)
- Domain math lives in `packages/domain/src/math-workbench/` (no Three/React here).
- Three.js is confined to `three/` + `Stage3D.tsx` and only loads in 3D modes.
