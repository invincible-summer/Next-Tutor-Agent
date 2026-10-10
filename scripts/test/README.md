# Test helpers

`register-typescript-loader.mjs` and `typescript-loader.mjs` provide the Node
test runner with a local TypeScript ESM transform when the Node binary does not
ship native type stripping.

- No network access and no model calls.
- Inputs are workspace TypeScript test modules and their source imports.
- The loader writes no artifacts; it is used from local and CI test commands.
- Type checking remains a separate `tsc --noEmit` step.
