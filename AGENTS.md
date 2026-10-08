# Repository Guidelines

在没有明确要求且不需要 push 时，对于小的修复和更新不需要运行全量测试，也无需运行大量检查脚本，只需要按要求更新对应文档即可.

## 1. Repository map

Textbook-driven AI teaching monorepo: `apps/web/` (Next.js), `apps/mobile/` (Expo Native), `packages/*` (shared client platform), `services/api/` (FastAPI; routes `app/api/v1/`, agents `app/agents/`, shared infra `app/core/`, enterprise persistence `app/persistence/`, durable workflows `app/workflows/` + `worker.py`, observability `app/observability/`, tests `tests/`), `services/voice/` (optional MeloTTS sidecar), `fixtures/demo/` (the only demo content source — synthetic), `scripts/` (per-domain READMEs), `deploy/` (`local/` infra compose + `self-hosted/` templates).

All runtime state resolves through one data root (`NEXT_TUTOR_DATA_DIR`, `services/api/app/core/paths.py`). Never commit runtime data, uploads, conversations, traces, or `.env`; the repo ships no textbook or derived assets. Docs entry: [`docs/README.md`](docs/README.md).

## 2. Module maintenance map

For anything beyond mechanical edits, open the module README plus its canonical doc. Docs are in `docs/architecture/`; agents-layer code is under `app/agents/` (rows listing `agents/<pkg>`):

| Area | Code | Doc |
| :--- | :--- | :--- |
| Frontend | `apps/web` | `frontend.md` |
| Native mobile | `apps/mobile` | `mobile-app.md`; development/validation in `docs/development/mobile-dev.md` and `docs/validation/mobile-validation.md` |
| Shared client packages | `packages/*` + `apps/web/src/platform` | `client-platform.md` |
| Identity / accounts | `app/identity` | `identity.md` |
| Enterprise persistence | `app/persistence` + `services/api/migrations` + `scripts/migrations` | `enterprise-infra.md`（`docs/development/`） |
| Observability | `app/observability` | `backend-runtime.md`（Observability 节） |
| Durable workflows | `app/workflows` + `services/api/worker.py` | `backend-runtime.md` + ADR-0013；运维见 `docs/development/enterprise-infra.md` |
| Chat / supervisor | `app/agents` root + `api/v1/chat.py` | `conversation.md` |
| Student model | `agents/student_model` | `student-model.md` |
| Teaching engine | `agents/teaching_engine` | `teaching-engine.md` |
| Assessment (quiz/CAT) | `agents/assessment` | `assessment.md` |
| Knowledge / RAG | `agents/knowledge` | `knowledge-rag.md` |
| Memory | `agents/memory` | `memory.md` |
| Evaluation | `agents/evaluation` | `evaluation.md` |
| UX intelligence | `agents/ux_intelligence` | `ux.md` |
| Learning orchestration | `agents/learning_orchestration` | `learning-orchestration.md` |
| Skill runtime / tools | `agents/skill_runtime` + `app/tools` | `skill-runtime.md` |
| Classroom | `app/classroom` | `classroom.md` |
| Site assistant | `agents/site_assistant` | `site-assistant.md` |
| Notes | `app/notes` + `agents/notes_agent.py` | `notes.md` |
| Diagrams / illustration | `app/diagrams` + `app/illustration` | `diagrams-illustration.md` |
| Tool assistant / scenario illustration | `app/illustration` + frontend `/tools` | `tool-assistant.md` |
| Chem lab (simulation bench) | `app/chem_lab` + `packages/domain/src/chem-lab` + frontend `/tools/lab` | `chem-lab.md` |
| Voice | `app/voice` + `services/voice` | `voice.md` |
| Backend runtime / core | `app/core` + `app/main.py` | `backend-runtime.md` |

Lasting decisions live in `docs/adr/` (immutable; supersede with a new ADR).

## 3. Non-negotiable invariants

- `resolve_student_id()`（identity 侧）is the only trusted student identifier.
- All JSON persistence goes through `core/atomic.py`; sanitize file keys.
- Prompts live in `prompts/registry.py`; text changes require a version bump.
- Public textbook data: fixed `public` namespace — all users read, only admins write.
- Never log/commit secrets, passwords, raw chain-of-thought, or private user data.
- Frontend: `apiFetch` for requests; shared `Pager` for lists; `ui/Input.tsx` primitives (`Input`/`Textarea`/`Field`/`FIELD_CLS`) for forms; `motion-modal`/`motion-drawer`/`motion-pop` for overlay entrances; long forms in `Modal`, not page cards.
- Native mobile: use `packages/api-client` through the platform adapter; use native UI primitives and long-form `Sheet`; derive layout from current window size. Keep private queries/drafts in memory, refresh credentials in SecureStore, and speech provider keys on the server.
- Production: `AUTH_MODE=1`, strong `AUTH_JWT_SECRET`, restricted `CORS_ORIGINS`, nginx SSE buffering off.
- Content policy (`docs/compliance/content-policy.md`): fixtures are project-authored synthetic only — no textbook PDFs, parsed text, chunks, graphs, embeddings, or user data in the repo.

## 4. Test selection

Focused first: `cd services/api && python -m tests tests.<owner>.test_<module>`; frontend `cd apps/web && pnpm check`. Full battery (backend full run, `pnpm check && pnpm build`, browser regression, `git diff --check`) when behavior/storage/APIs change. Mobile: `pnpm mobile:check`, focused Jest, Expo Doctor/SDK checks and Android/iOS export; device/Maestro/a11y evidence is a separate release gate. Disabled intelligence layers must degrade without breaking chat; visual work needs light/dark + narrow-screen checks.

Tests never write production roots — everything resolves under the runtime data root. Inherit `tests/support/storage_sandbox.py::StorageSandboxTestCase` (or call `patch_all_storage_roots`) so all storage bindings, `prompt_memory`, `settings.trace_dir`/`chroma_dir`, and shared caches land in a `TemporaryDirectory`; no bare `tempfile.mkdtemp` without tearDown cleanup. New per-user storage root ⇒ bind via `core/paths.py::bind_storage_path` AND register in `core/orphan_cleanup.py` scan categories, in the same change. (Account deletion purges via `core/account_data.purge_account`; orphan leftovers are cleaned via `GET/POST /admin/orphan-data`.)

## 5. Documentation update rule

- Change architecture/storage/APIs/pipelines ⇒ update the module's `docs/architecture/*.md` in the same change.
- Module READMEs are navigation only — fix links when moving code, don't grow them.
- Working plans (`plan*.md`) stay local; tracked files never reference plan sections.
- CI gates: `scripts/repo/check_documentation.py` (links, READMEs, legacy refs, generated catalogs), `scripts/compliance/generate_notices.py --check` (pinned source inventory/license/SBOM), and `scripts/repo/check_repository_hygiene.py` (copyright/runtime-data/history).

## 6. Commit / PR guidelines

Imperative, scoped commits (`m5: add textbook taxonomy`, `chat: fix formula layout`); no vague checkpoints. PRs state behavior changes, compatibility/permission boundaries, tests run, and doc updates; include screenshots for UI work.
