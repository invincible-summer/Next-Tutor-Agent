# Third-Party Notices

This project uses the following third-party software:

The original text of the complete license is kept in the [`licenses/`](licenses/) directory, only for filing, and no original text has been changed.

This is a consolidated reorganization of the project's third-party license and
copyright declarations; the authoritative audit procedure, pinned sources,
revision history and release checklist remain in
[`docs/VOICE_LICENSES.md`](docs/VOICE_LICENSES.md).

## Distribution boundary

- This repository bundles **no model weights**: the local RAG embedding-model
  runtime was removed entirely (a model-agnostic interface remains in
  `backend/app/core/embedding.py`), and the voice models below are downloaded
  at deployment time into gitignored directories. Redistribution obligations
  for those models still apply once you deploy or re-distribute them.
- Speech input uses the browser `SpeechRecognition` / `webkitSpeechRecognition`
  platform API — not software distributed by this repository. Browser/vendor
  recognition services carry their own availability, privacy, regional,
  pricing, and commercial terms.

## MeloTTS

Purpose: local Chinese TTS inference source (vendored at
`backend/vendor/MeloTTS`, mounted by the voice sidecar).

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

Exact versions are pinned in `backend/voice_sidecar/requirements.txt` (CPU
PyTorch wheels are installed by `deploy/install_voice.sh`). License full
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

## Core backend runtime dependencies

The BM25/API production runtime (`backend/requirements.txt`, versions
constrained by `backend/constraints.txt`) and the optional vector lane
(`backend/requirements-vector.txt`) install standard PyPI packages whose
license and copyright notices are carried by the packages themselves
(`*.dist-info` / `LICENSE` / `NOTICE`). Before shipping a venv, container, or
offline bundle, preserve those files for the actually installed versions and
produce a versioned SBOM. No local embedding model or model runtime is
installed by any requirements file in this repository.

## Browser service notice

Compatibility information: [MDN SpeechRecognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition)
and the [Web Speech API draft](https://wicg.github.io/speech-api/#speechreco-section).
Review the terms/privacy policy of the browser actually deployed, including
[Google Terms](https://policies.google.com/terms),
[Google Privacy](https://policies.google.com/privacy),
[Microsoft Services Agreement](https://www.microsoft.com/en-us/servicesagreement),
and [Microsoft Privacy Statement](https://privacy.microsoft.com/privacystatement)
where applicable. Edu_Agent does not grant access to or promise free
commercial use of a browser vendor's recognition service.

---

Audit date: 2026-08-30. Resolved transitive packages and native libraries vary
by target platform; this file is not a substitute for the notices/SBOM of a
shipped image. For the repository/model audit procedure and complete release
checklist, see [`docs/VOICE_LICENSES.md`](docs/VOICE_LICENSES.md).
