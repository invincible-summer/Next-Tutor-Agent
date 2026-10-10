# Chemistry simulation bench

The chemistry tool is a client-side 3D exploration toy. It is intentionally separate from the teaching, assessment, and chat pipelines: a scene is a collection of visual equipment, connections, contents, and transient effects. A user can move, duplicate, remove, connect, disconnect, pour, heat, pump, pulse, save, and reset without completing a procedure or receiving a score.

## Surface and routes

- `/tools/lab` renders the stage catalogue.
- `/tools/lab/chemistry?stage=<stage-id>` opens the immersive Three.js workbench.
- The workbench has no experiment mode selector, step rail, prediction form, result report, or correctness state. `AUTO` loads an authored arrangement as a starting point; it does not run the scene.

The retired server session API, former SVG session UI, and mirrored chemistry solver have been removed. New work must target the 3D surface and must not add a compatibility path back to a guided/evaluation flow.

## One document kernel

`packages/domain/src/chem-lab/` is the platform-neutral source of truth:

- `types.ts` defines poses, sockets, anchors, controls, contents, connections, cues, and limits.
- `equipment.ts` is the single equipment registry. Bounds (including whether a horizontal vessel is centered or rests on its base), ports, anchor points, controls, visual contents, and interaction capabilities live here. Three.js models, hit targets, bubbles, and future clients read this registry.
- `stages.ts` contains five authored scene themes plus a sixth open-bench stage. Each stage has a starter scene, an optional `assembledTemplate`, camera framing, available rack inventory, and visual cue colors. A stage has no objectives, requirements, answer, safety lock, grade, or completion flag.
- `document.ts` applies local actions immutably. It owns add/move/rotate/attach/detach/remove/connect/disconnect/control/content/auto/reset, bounded undo snapshots, finite-coordinate validation, and visual-content clamping. Failures are technical affordance hints only (missing, occupied, or incompatible ports).
- `graph.ts` traverses external tubes plus declared internal edges to decide where visual flow dots may travel. The graph is not a chemistry solver and does not judge a connection.
- `storage-schema.ts` validates the small local-save envelope. Runtime state stays in the browser and is never committed.

The domain layer does not import React, Three.js, the network, or Python. It is the shared kernel for web now and other clients later.

## Three.js scene

`apps/web/src/components/pages/tools/chem-lab-3d/` owns presentation only:

- `SceneController` owns one renderer, camera, OrbitControls, resize handling, context recovery, and an invalidate/activity frame loop.
- `Environment` creates the bright ceramic bench, rear equipment rack, cabinet, room lighting, and soft shadows. Theme changes adjust only local scene lighting and materials.
- `EquipmentFactory` builds original procedural glass, metal, rubber, hose, lamp, pump, condenser, and vessel meshes. `EquipmentModels` provides shared ref-counted geometry/material/texture caches. A model and its hit proxy use the same domain bounds and local anchors.
- `SceneSync` maps each document instance to one model group, resolves mounted world poses, builds tube geometry from world port anchors, and creates enlarged invisible hit shells for ports, controls, clamps, and equipment. The rear rack contains small real models; selecting a slot dispatches one `add` action into the document.
- `InteractionController` is the only pointer state machine. It handles short press versus drag, ray-plane movement with grab offset, rack take-out, port-to-port tube drag or two-click connection, clamp rail movement, pour targets, dial gestures, pointer capture, two-finger cancellation, Escape, blur, and context-loss cleanup. It never calls an API.
- `ObjectPopover` is a projected, object-local bubble. Its actions are derived from `equipmentCapabilities`, controls, and current document state. It follows its anchor while the camera or object moves and disappears when the anchor is behind the camera.
- `Effects` is a transient presentation runtime. Document diffs and explicit cues drive flame layers, bubbles, vapor, condensate drops, flow dots, sediment, liquid streams, and the short pulse burst. Effects never write to the document or history.

## Interaction contract

1. A short press selects an object and opens its nearby bubble. There is no permanent instrument sidebar.
2. Dragging a bench object moves it on a soft bounded plane. Dragging a rack slot places a fresh instance on the bench.
3. Dragging from a port previews a tube; release near a compatible free port commits one connection. Clicking two ports provides the touch-friendly equivalent.
4. Moving a clamped object along the retort stand changes its local pose. Dragging it away detaches it to the bench. Moving a stand carries its mounted children and tubes visually.
5. Adding a demo liquid, toggling a lamp/pump/valve, or pressing pulse updates document state or a transient cue. These actions are entertainment feedback, not experimental instructions.

The only persistent interface controls are Back, `AUTO`, Save/Open, Reset, and Reset camera. Save data is local to the current owner key and bounded by the domain limits. No request is sent for scene actions.

## Validation

Run the web type check after model or interaction changes:

```bash
pnpm --filter @next-tutor/web typecheck
```

The domain tests cover stage templates, port graph behavior, attach/detach, AUTO atomicity, and local storage. Run them with `pnpm --filter @next-tutor/domain test`; the repository registers `scripts/test/typescript-loader.mjs`, so the check does not depend on whether the local Node binary was built with native type stripping.
