# Next Tutor Agent frontend

Use Node.js 22 and the pnpm version declared in `package.json`.

```bash
pnpm install --frozen-lockfile
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
NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:8124 pnpm build
E2E_PRODUCTION=1 E2E_FRESH=1 pnpm test:e2e:ci  # critical journeys
E2E_PRODUCTION=1 E2E_FRESH=1 pnpm test:e2e     # full regression
```

See [Testing and CI maintenance](../../docs/development/testing.md) for Python setup, test
isolation, diagnostics, and the GitHub workflow policy.
