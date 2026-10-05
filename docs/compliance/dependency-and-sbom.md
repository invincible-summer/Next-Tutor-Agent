# Dependency licenses and source SBOM

The distribution entry point is [THIRD-PARTY-NOTICES.md](../../THIRD-PARTY-NOTICES.md). Original license and attribution bytes are retained in [licenses](../../licenses/README.md); the versioned [package index](../../licenses/PACKAGE-NOTICES.md) covers the complete root pnpm lock graph, Python constraints and requirements, and installed Python dependency closure.

## Rebuild

From the repository root, after a frozen root workspace install and the desired Python profile installation:

```bash
python3 scripts/compliance/generate_notices.py --fetch
python3 scripts/compliance/generate_notices.py --check
```

The generator uses already constrained `PyYAML` and `packaging`; it does not import application modules, read environment files, or access learning data. `--fetch` obtains missing exact-version official npm/PyPI package metadata and license files from tarballs/wheels. It verifies npm artifact integrity and PyPI SHA-256, extracts bytes without running package hooks, and commits only license/notice/attribution text and sanitized metadata. It never installs downloaded packages or stores model weights.

The default generation command is offline and reuses archived registry evidence. `--check` is fully offline: it validates manifest/lock/requirements/deployment input hashes, every referenced original license SHA-256 and generated package-index/SBOM bytes. This check can run in the repository CI lane without a Python voice environment. Version or lock changes require regeneration before release.

## Evidence scope

- `installed-package` and `installed-distribution` indicate actual installed metadata inspected for the exact version.
- `registry-artifact;not-installed` indicates official artifact evidence for a pinned package; it does not assert installation on this host or inclusion in a final mobile binary.
- `lock-only` and `declared;not-installed` are explicit unresolved installation boundaries. Missing license expressions/texts remain listed in `inventory.json:gaps`; no placeholder license is treated as permission.
- npm records include runtime, development and optional platform graph scopes. A reachable dependency may still be removed by the final bundler; source inventory scope is deliberately broader than a single produced bundle.
- Python transitive closure records the generating interpreter's actual installed dependency metadata and marker evaluation. Constraint-only entries do not assert installation. The optional voice environment and its independently selected native wheels must be audited after deployment; source declarations do not substitute for that target environment's resolver output.
- Deployment image tags and OS tools are references, not redistributed images. A source license reference does not assert every image layer or native subcomponent was examined.

## Native, assets and optional profiles

Preserve KaTeX JavaScript/CSS/font licenses with the browser bundle, offline mobile math asset and generated classroom renderer. Preserve Playfair Display OFL and Lucide's ISC plus Feather-derived MIT attribution. System fallback font names are not vendored font files. React Native/Expo native binaries, Hermes, platform frameworks and every resolved CocoaPods/Gradle dependency require their own final binary inventory.

Keep actual wheel/native notices for PyMuPDF/MuPDF, Pillow, numpy/scipy, cryptography, ONNX Runtime, sharp/libvips and optional PyTorch/torchaudio/soundfile. A top-level package label cannot erase bundled component terms. For LGPL components, satisfy the selected release's corresponding source/relinking obligations when distributing a binary. PyMuPDF/MuPDF AGPL/commercial, Redis 8 license alternatives and MinIO AGPL boundaries remain explicit in the distribution notice; this audit does not choose a project-wide license or purchase commercial rights.

## Release evidence

Before distributing a venv/container/native binary, archive its exact resolver output and wheel/image/binary hashes, generate a target artifact SBOM and retain all accompanying LICENSE/COPYING/NOTICE files. Complete security audit results separately; license checks do not prove absence of vulnerabilities. The current source BOM is not evidence that native device, enterprise cutover, SCA or store release gates have passed.
