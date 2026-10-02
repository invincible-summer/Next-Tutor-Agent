# Repository Guidelines

## Project Structure & Module Organization

暂时不做手机端适配，Github Page 上的展示页面在无要求时不必时时更新；

在没有明确要求且不需要 commit push 时，对于小的修复和更新不需要运行全量测试.

Next-Tutor-Agent is a textbook-driven AI teaching workspace. The FastAPI backend lives in `services/api/app/`: routes are under `api/v1/`, identity handling under `identity/`, orchestration layers M1–M10 under `agents/`, and shared utilities under `core/`. Tests are in `services/api/tests/test_*.py`. The MeloTTS voice sidecar is `services/voice/`.

The Next.js frontend is in `apps/web/src/`: pages under `app/`, reusable UI under `components/`, and API, state, i18n, and types under `lib/`. Deployment templates live in `deploy/`. All runtime state lives under a single data root (`NEXT_TUTOR_DATA_DIR`, default `.runtime/data`; see `services/api/app/core/paths.py`) — runtime data, private uploads, conversations, traces, and `.env` files must never be committed. The repository ships no textbook or derived data assets: public textbook namespaces, parsed text, chunks, knowledge graphs and demo runtime data are deployment-local runtime state; the only demo content source is the synthetic `fixtures/demo/` corpus. `scripts/repo/check_repository_hygiene.py` (CI's first blocking job) enforces this over tracked files and full history; large-file exceptions go in `scripts/repo/repo-policy.toml`. Account deletion (self-service or admin) purges all account data via `core/account_data.purge_account` with no empty-dir residue; the admin "数据清理" page (`GET/POST /admin/orphan-data`) scans and purges orphan runtime data left by tests or legacy deletions. Read `docs/DESIGN.md` before changing architecture, storage, APIs, or agent pipelines.

## Build, Test, and Development Commands

- `./start.sh`: start the complete production-style runtime with automatic port fallback.
- `./start.sh dev`: run the frontend in hot-reload mode.
- `cd services/api && python -m tests`: run all backend tests.
- `cd services/api && python -m tests tests.test_<module>`: run a focused backend regression.
- `cd apps/web && pnpm check`: run frontend type, lint, and unit checks.
- `cd apps/web && pnpm build`: validate the production frontend build.
- `python scripts/repo/check_repository_hygiene.py`: run the copyright/hygiene guard locally.

See `docs/TESTING.md` for environment setup, browser smoke/full regression, and CI ownership.

## Coding Style & Naming Conventions

Use four-space Python indentation and `snake_case` names. TypeScript uses two spaces, `PascalCase` components, and `camelCase` helpers. Keep changes consistent with adjacent code. Route JSON persistence through `core/atomic.py` and sanitize file keys. Register prompts in `prompts/registry.py`. Frontend requests must use `apiFetch`; list views use the shared `Pager`; form controls use the shared `ui/Input.tsx` primitives (`Input`/`Textarea`/`Field`/`FIELD_CLS`) instead of ad-hoc class strings. Overlay entrances use the `motion-modal`/`motion-drawer`/`motion-pop` classes (reduced-motion aware); long forms like textbook upload live in `Modal`, not embedded page cards.

## Testing Guidelines

Use Python `unittest`; name files `test_<module>.py` and add route regressions for new APIs. Disabled intelligence layers must degrade without breaking chat. Run focused tests first, then broader checks and `git diff --check`. Frontend changes require typecheck, ESLint, and a production build; visual work also needs light/dark and narrow-screen verification.

Tests must never write to production storage roots — everything resolves through the runtime data root (`students/`, `chat_history/`, `traces/`, `uploads/`, `notes/`, `knowledge/`, `users/` under it); synthetic IDs leaking into a live data root were the source of thousands of orphan files. Inherit `tests/storage_sandbox.py::StorageSandboxTestCase` (or call its `patch_all_storage_roots` when a custom fixture is unavoidable) so the runtime root override, every storage-root binding, `prompt_memory`, `settings.trace_dir`/`chroma_dir`, and the StudentModel/vector-store caches are redirected into a `TemporaryDirectory`. Never use a bare `tempfile.mkdtemp` without cleanup in `tearDown`. When adding a new per-user storage root, bind it via `core/paths.py::bind_storage_path` AND register it in `core/orphan_cleanup.py`'s scan categories in the same change.

## Security & Configuration

`resolve_student_id()` is the only trusted student identifier. Never log or commit secrets, passwords, raw chain-of-thought, or private user data. Public textbook data uses the fixed `public` namespace and is readable by all users but writable only by administrators. Production requires `AUTH_MODE=1`, a strong `AUTH_JWT_SECRET`, restricted `CORS_ORIGINS`, and nginx SSE buffering disabled.

## Commit & Pull Request Guidelines

Prefer imperative, scoped commits such as `m5: add textbook taxonomy` or `chat: fix formula layout`; avoid vague checkpoint messages. Pull requests should explain behavior changes, compatibility and permission boundaries, tests run, linked issues, and documentation updates. Include screenshots or recordings for UI work.
