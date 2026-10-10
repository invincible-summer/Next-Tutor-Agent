# Third-Party Notices

This project uses the following third-party software:

The original text of the complete license is kept in the [`licenses/`](licenses/) directory, only for filing, and no original text has been changed.

The complete versioned package index is
[`licenses/PACKAGE-NOTICES.md`](licenses/PACKAGE-NOTICES.md), including root,
Web, Mobile and shared workspace direct/transitive npm packages, Python runtime,
vector, test, observability and optional voice declarations. Each package links
to preserved original license/notice bytes and names its evidence boundary.
Machine evidence: [`inventory.json`](licenses/inventory.json) and
[CycloneDX source SBOM](licenses/sbom.cdx.json). Rebuild/check instructions:
[dependency-and-sbom.md](docs/compliance/dependency-and-sbom.md).

No project-wide license is granted by this notice. The repository currently has
no project LICENSE; dependency permissions apply to their respective components,
not automatically to the application's original code.

## Distribution boundary

- This repository bundles **no model weights**: the local RAG embedding-model
  runtime was removed entirely (a model-agnostic interface remains in
  `services/api/app/core/embedding.py`), and the voice models below are downloaded
  at deployment time into gitignored directories. Redistribution obligations
  for those models still apply once you deploy or re-distribute them.
- Web speech input may use browser `SpeechRecognition` /
  `webkitSpeechRecognition`. Mobile speech uses server-mediated cloud providers.
  Online recognition/synthesis services, system fonts and platform frameworks
  are not software redistributed by this source repository; their service,
  privacy, availability and commercial terms remain separate.
- Development and platform-optional packages are included in the source
  inventory. A registry license inspection does not assert that an optional
  package is installed, bundled, or admitted on a device. The source SBOM is not
  an attestation of any final native binary, wheel environment, or container.

## MeloTTS

Purpose: optional local Chinese TTS inference source, downloaded during deployment
to gitignored `services/voice/vendor/MeloTTS` and mounted by the voice sidecar;
it is not vendored into the source release.

Pinned revision: `209145371cff8fc3bd60d7be902ea69cbdb7965a`

License: MIT

Copyright © 2024 MyShell.ai

Full text: [`licenses/melotts-MIT.txt`](licenses/melotts-MIT.txt)

When distributing source or substantial portions, retain the upstream
copyright and license text.

## MeloTTS-Chinese (model)

Purpose: TTS configuration and weights; downloaded at deployment, never
committed to this repository.

Pinned revision: `af5d207a364ea4208c6f589c89f57f88414bdd16`

License: MIT (per model card)

If redistributed, retain the model card, README, license, revision, and
hashes; this label does not grant unrelated data or trademark rights.

## bert-base-multilingual-uncased (model)

Purpose: MeloTTS Chinese/mixed-English BERT features and tokenizer;
downloaded at deployment.

Pinned revision: `7cbf9a625e29989f6b9c6c2fa68234c304f7e38f`

License: Apache-2.0

Copyright © the respective rights holders named in the model distribution

Full text: [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt)

Retain the model card, license, copyright, and NOTICE if supplied.

## bert-base-uncased (model)

Purpose: MeloTTS mixed-English tokenizer; downloaded at deployment.

Pinned revision: `86b5e0934494bd15c9632b12f734a8a67f723594`

License: Apache-2.0

Copyright © the respective rights holders named in the model distribution

Full text: [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt)

## Voice sidecar dependencies

Exact versions are pinned in `services/voice/requirements.txt` (CPU
PyTorch wheels are installed by `deploy/self-hosted/install_voice.sh`). License full
texts kept in this repository are linked below; for everything else, preserve
the notices shipped inside the actual wheels.

| Packages | License / boundary | Full text in this repo |
|---|---|---|
| `fastapi`, `pydantic` | MIT | — |
| `uvicorn` | BSD-3-Clause | [`licenses/BSD-3-Clause.txt`](licenses/BSD-3-Clause.txt) |
| `torch`, `torchaudio` | Multi-license bundle: Apache-2.0, BSD-2/3-Clause, BSL-1.0, MIT and other bundled notices — preserve the actual wheel notices rather than labeling them only MIT | [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt), [`licenses/BSD-2-Clause.txt`](licenses/BSD-2-Clause.txt), [`licenses/BSD-3-Clause.txt`](licenses/BSD-3-Clause.txt) |
| `transformers`, `huggingface_hub` | Apache-2.0 | [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt) |
| `numpy`, `scipy`, `soundfile` | BSD-3-Clause plus actual binary-wheel/native notices; `soundfile` may carry an LGPL-2.1 `libsndfile` boundary | [`licenses/BSD-3-Clause.txt`](licenses/BSD-3-Clause.txt), [`licenses/LGPL-2.1.txt`](licenses/LGPL-2.1.txt) |
| `librosa` | ISC | [`licenses/ISC.txt`](licenses/ISC.txt) |
| `tqdm` | MPL-2.0 AND MIT | — |
| `jieba`, `pypinyin`, `cn2an` | MIT | — |
| `g2p_en`, `nltk` | Apache-2.0; NLTK data retains its own terms | [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt) |

NLTK's data index marks the averaged-perceptron tagger packages MIT. Its
CMUdict entry states research and commercial use is unrestricted and requests
acknowledgement of Carnegie Mellon University; preserve the accompanying data
README/attribution when redistributing it.

## Web, Mobile and shared JavaScript packages

The [complete package index](licenses/PACKAGE-NOTICES.md) covers the root
`pnpm-lock.yaml` graph, including build/test tooling and optional platform
packages. Original package LICENSE/COPYING/NOTICE and attribution files are
retained byte-for-byte under [`licenses/texts/`](licenses/texts/), deduplicated
by SHA-256. The inventory records the source within each package or official
registry artifact. Preserve the relevant texts alongside redistributed bundles.

Particular asset and native boundaries:

| Component | Terms and retained attribution | Use |
|---|---|---|
| KaTeX | MIT, upstream authors identified in the preserved package LICENSE | Web math, generated offline classroom JS/CSS/fonts, mobile offline math fallback. The generated assets derive from the same pinned npm package. |
| three (`three@0.186.1`) | MIT, preserved package LICENSE under `licenses/texts/` | Web 3D presentation layer for the math workbench (`/tools/geometry`); `@types/three@0.186.0` stays a devDependency. No Three example assets (models/HDRI/fonts/demo scenes) are vendored or redistributed. |
| `@fontsource/playfair-display` | OFL-1.1; Playfair Display Project Authors and Reserved Font Name retained in original LICENSE | Web display font files |
| `lucide-react`, `lucide-react-native` | ISC plus MIT for Feather-derived icons, both notices retained | Web and mobile icon sets |
| React / React Native / Expo / Hermes | Package originals and native bundled notices; preserve final CocoaPods/Gradle/Hermes artifact notices | JS source dependency inventory does not replace a native binary inventory |
| sharp / libvips | Apache-2.0 at sharp's top level plus LGPL/native bundled component notices shipped with platform libvips packages | Image generation/build tool and Next.js image pipeline; preserve actual binary notices and applicable source/relinking materials |

System fallback font names such as Noto/Source Han are not vendored font files.
No Material Symbols asset or import is present in the current source tree; no
license grant is inferred for an absent asset. Native Apple/Android fonts and
system APIs stay subject to platform terms.

## Python runtime, optional lanes and native wheels

The package index includes `services/api/requirements*.txt`, their exact
`constraints.txt` versions and actual installed transitive metadata, plus
`services/voice/requirements.txt` declarations. Constraint-only or uninstalled
optional records are labeled explicitly. No local embedding model is installed
by these requirements. An optional vector backend library is not a bundled
embedding model.

The PDF backend runs on pypdf, pdfplumber (pdfminer.six) and pypdfium2
(ADR-0015) — BSD-3-Clause, MIT and Apache-2.0/BSD-3-Clause respectively.
pypdfium2 wheels bundle the PDFium library: preserve the bundled PDFium
license and third-party notice files shipped inside the wheel with any
redistributed binary. PyMuPDF/MuPDF (AGPL or commercial) is no longer a
dependency of this project.

Preserve actual wheel/native notices for Pillow, numpy/scipy (including BLAS,
LAPACK and compiler runtimes where bundled), cryptography/OpenSSL, ONNX Runtime,
lxml/libxml2/libxslt, soundfile/libsndfile, torch/torchaudio and
other native packages. Top-level package labels do not replace bundled licenses.
The source archive preserves inspectable installed/artifact notice files; a
final deployment's OS libraries and different platform wheels require a separate
artifact inventory. Optional voice inference/weights are not asserted installed.

## Deployment software references

Deployment templates reference external images/tools; the source release does
not redistribute those images. Original upstream reference terms are archived
in [external-components.json](licenses/external-components.json) and the package
index. The final image layers, OS libraries, executable versions and source
obligations must be recorded when distributing a deployment artifact.

| Reference | License boundary |
|---|---|
| PostgreSQL 18 | PostgreSQL License |
| Valkey `9.1.2` | BSD-3-Clause (upstream COPYING archived). Cache/rate-limit/lease tier only — never business truth. The `redis` Python client (MIT) speaks RESP to it. [Valkey](https://valkey.io/) |
| Temporal server `1.25.2`, OpenTelemetry Collector Contrib `0.116.0` | MIT / Apache-2.0 at the referenced upstream level, plus final image contents |
| Tesseract, nginx, Node.js, Python, Docker/container tooling | Independently installed/deployment-selected tools and runtimes; preserve actual distribution terms. Tesseract Apache-2.0 and nginx BSD reference texts are archived; no installed tool version is asserted. |

## Browser service notice

Compatibility information: [MDN SpeechRecognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition)
and the [Web Speech API draft](https://wicg.github.io/speech-api/#speechreco-section).
Review the terms/privacy policy of the browser actually deployed, including
[Google Terms](https://policies.google.com/terms),
[Google Privacy](https://policies.google.com/privacy),
[Microsoft Services Agreement](https://www.microsoft.com/en-us/servicesagreement),
and [Microsoft Privacy Statement](https://privacy.microsoft.com/privacystatement)
where applicable. Next-Tutor-Agent does not grant access to or promise free
commercial use of a browser vendor's recognition service.

---

Source inventory audit: 2026-10-05. Resolved package versions and evidence hashes
are in the machine inventory. Missing declarations/originals remain explicit
`gaps`; no placeholder is treated as a verified license. Optional voice wheels,
platform binaries and image layers may have additional notices, so this file is
not a substitute for a shipped artifact's SBOM. This is not a vulnerability audit
pass or native/store release acceptance. Voice/model audit procedure:
[voice-licenses.md](docs/compliance/voice-licenses.md).
