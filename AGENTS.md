# Repository Guidelines

暂时不做手机端适配，Github Page 上的展示页面在无要求时不必时时更新；

在没有明确要求且不需要 commit push 时，对于小的修复和更新不需要运行全量测试.

## 1. Repository map

Next-Tutor-Agent is a textbook-driven AI teaching workspace (monorepo):

- `apps/web/` — Next.js frontend: pages in `src/app/`, reusable UI in `src/components/`, API/state/i18n/types in `src/lib/`.
- `services/api/` — FastAPI backend: routes in `app/api/v1/`, identity in `app/identity/`, orchestration layers M1–M10 in `app/agents/`, shared infrastructure in `app/core/`, tests in `tests/`.
- `services/voice/` — optional local MeloTTS sidecar.
- `fixtures/demo/` — the only demo content source: project-authored synthetic data.
- `scripts/` — repo hygiene, demo export, and dev tooling (one README per domain).
- `deploy/` — systemd/nginx deployment templates.

All runtime state lives under a single data root (`NEXT_TUTOR_DATA_DIR`, default `.runtime/data`; see `services/api/app/core/paths.py`). Runtime data, private uploads, conversations, traces, and `.env` files must never be committed. The repository ships no textbook or derived data assets: public textbook namespaces, parsed text, chunks, knowledge graphs, and demo runtime data are deployment-local state.

Start from the docs entry point [`docs/README.md`](docs/README.md); each module directory has a README for local navigation.

## 2. Module maintenance map

When touching an area, open its module README (in-place navigation) and, for anything beyond mechanical edits, the canonical architecture doc:

| Area | Code | Canonical docs |
| :--- | :--- | :--- |
| Frontend | `apps/web` | `docs/architecture/frontend.md` |
| Identity / accounts | `services/api/app/identity` | `docs/architecture/identity.md` |
| Chat / supervisor pipeline | `services/api/app/agents` (root files) + `app/api/v1/chat.py` | `docs/architecture/conversation.md` |
| Student model / evidence | `services/api/app/agents/student_model` | `docs/architecture/student-model.md` |
| Teaching engine | `services/api/app/agents/teaching_engine` | `docs/architecture/teaching-engine.md` |
| Assessment (quiz/CAT) | `services/api/app/agents/assessment` | `docs/architecture/assessment.md` |
| Knowledge / RAG | `services/api/app/agents/knowledge` + retrieval core | `docs/architecture/knowledge-rag.md` |
| Memory | `services/api/app/agents/memory` | `docs/architecture/memory.md` |
| Learner evaluation | `services/api/app/agents/evaluation` | `docs/architecture/evaluation.md` |
| UX intelligence | `services/api/app/agents/ux_intelligence` | `docs/architecture/ux.md` |
| Learning orchestration | `services/api/app/agents/learning_orchestration` | `docs/architecture/learning-orchestration.md` |
| Skill runtime / tools | `services/api/app/agents/skill_runtime` + `app/tools` | `docs/architecture/skill-runtime.md` |
| Classroom | `services/api/app/classroom` | `docs/architecture/classroom.md` |
| Site assistant | `services/api/app/agents/site_assistant` | `docs/architecture/site-assistant.md` |
| Notes | notes store + `app/agents/notes_agent.py` | `docs/architecture/notes.md` |
| Diagrams / illustration | `services/api/app/diagrams` + `app/illustration` | `docs/architecture/diagrams-illustration.md` |
| Voice | `services/api/app/voice` + `services/voice` | `docs/architecture/voice.md` |
| Backend runtime / storage | `services/api/app/core` + `app/main.py` | `docs/architecture/backend-runtime.md` |
| Pedagogy policy | prompts + strategy layers | `docs/architecture/pedagogy.md` |

Decisions with lasting impact live in `docs/adr/` (immutable once accepted; supersede by a new ADR, never edit history).

## 3. Non-negotiable invariants

- `identity` 侧的 `resolve_student_id()` is the only trusted student identifier.
- Route all JSON persistence through `core/atomic.py` and sanitize file keys.
- Register prompts in `prompts/registry.py`; changing prompt text requires a version bump.
- Public textbook data uses the fixed `public` namespace: readable by all users, writable only by administrators.
- Never log or commit secrets, passwords, raw chain-of-thought, or private user data.
- Frontend requests must use `apiFetch`; list views use the shared `Pager`; form controls use the shared `ui/Input.tsx` primitives (`Input`/`Textarea`/`Field`/`FIELD_CLS`) instead of ad-hoc class strings. Overlay entrances use the `motion-modal`/`motion-drawer`/`motion-pop` classes (reduced-motion aware); long forms like textbook upload live in `Modal`, not embedded page cards.
- Production requires `AUTH_MODE=1`, a strong `AUTH_JWT_SECRET`, restricted `CORS_ORIGINS`, and nginx SSE buffering disabled.
- Content policy (details in `docs/compliance/content-policy.md`): only project-authored synthetic fixtures for RAG/E2E; no textbook PDFs, parsed text, chunks, graphs, embeddings, user data, or real account exports in the repository.

## 4. Test selection

- Focused first: `cd services/api && python -m tests tests.test_<module>`; frontend `cd apps/web && pnpm check`.
- Full battery when behavior, storage, or APIs change: backend full run, `pnpm check && pnpm build`, browser regression per `docs/development/testing.md`, then `git diff --check`.
- Disabled intelligence layers must degrade without breaking chat.
- Frontend visual work also needs light/dark and narrow-screen verification.

Tests must never write to production storage roots — everything resolves through the runtime data root (`students/`, `chat_history/`, `traces/`, `uploads/`, `notes/`, `knowledge/`, `users/` under it); synthetic IDs leaking into a live data root were the source of thousands of orphan files. Inherit `tests/storage_sandbox.py::StorageSandboxTestCase` (or call its `patch_all_storage_roots` when a custom fixture is unavoidable) so the runtime root override, every storage-root binding, `prompt_memory`, `settings.trace_dir`/`chroma_dir`, and the StudentModel/vector-store caches are redirected into a `TemporaryDirectory`. Never use a bare `tempfile.mkdtemp` without cleanup in `tearDown`. When adding a new per-user storage root, bind it via `core/paths.py::bind_storage_path` AND register it in `core/orphan_cleanup.py`'s scan categories in the same change.

Account deletion (self-service or admin) purges all account data via `core/account_data.purge_account` with no empty-dir residue; the admin "数据清理" page (`GET/POST /admin/orphan-data`) scans and purges orphan runtime data left by tests or legacy deletions.

## 5. Documentation update rule

- `docs/architecture/*.md` describe current behavior — update the affected module doc in the same change when you modify architecture, storage, APIs, or agent pipelines.
- Add an ADR when making a decision that future contributors must not silently reverse (storage model, retrieval baseline, deployment shape, content policy).
- Module READMEs are navigation only: fix their links when moving code, don't grow them into designs.
- Never commit working plans (`plan*.md` stays local); tracked code/docs must not reference plan sections.
- `python scripts/repo/check_documentation.py` (CI gate) verifies links, required module READMEs, and banned legacy references; `python scripts/repo/check_repository_hygiene.py` guards copyright/runtime-data/history boundaries.

## 6. Commit / PR guidelines

Prefer imperative, scoped commits such as `m5: add textbook taxonomy` or `chat: fix formula layout`; avoid vague checkpoint messages. Pull requests should explain behavior changes, compatibility and permission boundaries, tests run, linked issues, and documentation updates. Include screenshots or recordings for UI work.
