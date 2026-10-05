# Next Tutor Agent frontend (`@next-tutor/web`)

Use Node.js 22 and the pnpm/Node versions pinned by the root `package.json`
(`packageManager`/`engines`). This package is a member of the root pnpm
workspace and consumes the shared `@next-tutor/*` packages; install from the
repository root:

```bash
# at the repo root (installs apps/web + packages/* against the single lockfile)
pnpm install --frozen-lockfile

# then either cd here and run scripts, or use the root filter form
cd apps/web
pnpm dev          # development server
pnpm check        # types, ESLint, lightweight unit tests
pnpm build        # webpack production build
pnpm start        # serve the production build
```

Browser tests start an isolated backend and fake LLM. Install backend test
requirements, Chromium and classroom assets before running them:

```bash
pnpm exec playwright install --with-deps chromium
pnpm build:classroom
pnpm test:e2e     # builds once, then runs the full isolated regression
pnpm test:e2e tests/e2e/auth-isolation.spec.ts  # same runner, selected scope
```

See [Testing and CI maintenance](../../docs/development/testing.md) for Python setup, test
isolation, diagnostics, and the GitHub workflow policy, and
[client platform architecture](../../docs/architecture/client-platform.md) for the
workspace/shared-package boundaries.
