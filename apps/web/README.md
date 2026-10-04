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
pnpm test:e2e     # builds once, then runs the full isolated regression
pnpm test:e2e tests/e2e/auth-isolation.spec.ts  # same runner, selected scope
```

See [Testing and CI maintenance](../../docs/development/testing.md) for Python setup, test
isolation, diagnostics, and the GitHub workflow policy.
