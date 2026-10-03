<div align="center">

<img src="../assets/learning-journey.svg" alt="Next Tutor Agent — a textbook-centered learning journey" width="900" />

# Next Tutor Agent

**Turn a textbook into a learning journey with direction.**

A textbook-driven AI workspace · Tutoring · Lessons · Assessment · Notes & review

[简体中文](../../README.md) · **English**

[Quick start](#quick-start) · [Features](#features) · [Project showcase](https://invincible-summer.github.io/Next-Tutor-Agent/)

</div>

---

Next Tutor Agent brings textbooks, explanations, practice, and review into one learning space. Ask follow-up questions about a concept, turn a chapter into a lesson, review feedback on your answers, and save useful ideas as notes.

Beyond answering questions, it considers **what supports the answer, what your work demonstrates, and what to study next**. It supports textbook-based self-study, lesson preparation, and ongoing one-to-one tutoring.

## Features

| Learning activity | What you can do |
| :--- | :--- |
| **Chat tutoring** | Ask textbook questions, work through derivations, and request hints or examples. Includes math rendering, image questions, and optional voice conversations. |
| **Courses** | Generate slides and scripts from chapters or topics, preview and edit lessons, start AI teaching, resume unfinished lessons, and export slides and scripts. |
| **Resources** | Use public textbooks, upload personal materials, and associate them with workspaces. Search becomes available after indexing while the knowledge graph continues building. |
| **Practice & assessment** | Generate questions in chat, create variations of a reference problem, or start an assessment that adjusts difficulty to your answers. Review explanations, recent questions, mistakes, and optional diagrams. |
| **Knowledge map & dashboard** | Explore concepts and their connections, review learning records and current evaluations, and identify areas to revisit. |
| **Notes & review** | Organize Markdown notes with backlinks, tags, and folders. Create notes from conversations, textbooks, or mistakes, and schedule reviews. |
| **Learning orchestration** | Break long-term goals into weekly plans and daily tasks, with study and review in one routine. |
| **Site assistant** | When enabled, discover features, check recent learning activity, and confirm actions to navigate or resume lessons. |

The interface supports Chinese and English, with light and dark themes. It currently targets desktop browsers. Classroom, voice, and assistant availability depends on the instance configuration.

## A typical learning session

1. **Prepare** — Choose a textbook in Resources, create a workspace, and associate your materials.
2. **Explore** — Ask follow-up questions in Chat Tutoring, or turn a chapter into a lesson in Courses.
3. **Practice** — Complete questions or an assessment, explain your reasoning, and use feedback to find gaps.
4. **Reflect** — Save key ideas as notes and schedule your next study and review tasks.

> Try: **“Explain derivatives using this textbook. Start with an intuitive example, then give me a practice question.”**

After launching the app, open Docs in the sidebar for the task-based user guide.

## Design principles

- **Textbooks provide grounding.** Relevant passages inform explanations and questions; insufficient evidence is treated as a meaningful limit.
- **Performance provides evidence.** Evaluations consider actual answers and assistance received, rather than treating “I understand” as proof of mastery.
- **Learning stays connected.** Materials, conversations, lessons, notes, and tasks provide continuity between sessions.

BM25 retrieval works without an embedding model; vector retrieval is optional. Adaptive assessment adjusts difficulty based on the current session, and its conclusions remain scoped to the observed tasks.

## Technology

| Layer | Stack |
| :--- | :--- |
| Frontend | Next.js 16 · React 19 · TypeScript · Tailwind CSS 4 · Zustand |
| Content | Markdown · KaTeX · SVG question diagrams · HTML lessons |
| Backend | Python 3.11 · FastAPI · Pydantic · OpenAI-compatible model API |
| Textbooks & retrieval | PyMuPDF · OCR · Structured chunking · BM25 · Optional Chroma vector retrieval |
| Learning & data | Multi-agent teaching orchestration · Textbook knowledge graphs · Learning evidence records · JSON / JSONL and Markdown storage |
| Validation | unittest · TypeScript / ESLint · Playwright |

## Quick start

### 1. Set up the environment

Linux / WSL is recommended. Install **Python 3.11, Node.js 22 LTS, and pnpm 11**, and have an OpenAI-compatible model service available.

```bash
git clone https://github.com/invincible-summer/Next-Tutor-Agent.git
cd Next-Tutor-Agent

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r services/api/requirements.txt

cd apps/web
pnpm install
cd ..
```

If you use Conda, create a Python 3.11 environment named `edu_agent` instead of `.venv`. The startup script tries to activate that environment first.

### 2. Configure your model

```bash
cp .env.example .env
```

Edit the root `.env` with values from your model provider:

```dotenv
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your-api-key
LLM_MODEL=your-model-name

ADMIN_EMAIL=you@example.com
ADMIN_PASSWORD=replace-with-your-own-password
```

The administrator credentials create the initial admin account. Image understanding and visual OCR require a vision-capable model. Local OCR fallback for scanned textbooks additionally requires Tesseract and the appropriate language packs.

### 3. Launch

Before using classroom features for the first time, prepare the lesson assets and browser used for layout checks:

```bash
cd apps/web
pnpm run build:classroom
pnpm exec playwright install --with-deps chromium
cd ..

./start.sh
```

The preferred frontend address is **http://localhost:3001**, with backend port `8123`. The script tries alternative ports when needed; use the address printed in the terminal. The first launch builds the frontend, and later launches can reuse the build. For hot reload:

```bash
./start.sh dev
```

Sign in and choose a textbook in Resources, or start in Chat Tutoring. For text tutoring alone, set `CLASSROOM_ENABLED=0` in `.env` and skip the classroom preparation commands.

<details>
<summary><strong>Optional capabilities</strong></summary>

| Capability | Configuration |
| :--- | :--- |
| Classroom | `.env.example` enables `CLASSROOM_ENABLED=1`; complete the preparation above. |
| Site assistant | Set `SITE_ASSISTANT_ENABLED=1`. |
| Classroom web search and images | Provide `TAVILY_API_KEY`, `PEXELS_API_KEY`, or `PIXABAY_API_KEY` as needed. |
| Cloud classroom speech | Provide `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION`. |
| Local speech | Install the local MeloTTS service, then set `VOICE_TTS_PROVIDER=melo`. |

The interface indicates available capabilities when optional services are missing. See [`.env.example`](../../.env.example) for configuration options.

</details>

See [Testing and CI maintenance](../development/testing.md) for setup, local checks, extended regressions, and release verification.

## Project layout

```text
apps/web/           Next.js frontend (pages, learning interactions, shared components)
services/api/       FastAPI backend (APIs, teaching agents, textbook processing, learning data)
services/voice/     Local MeloTTS voice sidecar (optional)
fixtures/demo/      Synthetic-only data source for the GitHub Pages demo
scripts/            Repo hygiene guard / demo export / dev tooling
deploy/             Deployment templates (systemd, nginx, ...)
```

The repository ships source, tests, deployment templates, and synthetic demo data only:
textbook files, parsed text, chunks, knowledge graphs, and user runtime state are
deployment-local (`.runtime/data`, see `services/api/app/core/paths.py`) and never
published with the repository.

Explore the [project showcase](https://invincible-summer.github.io/Next-Tutor-Agent/) —
built entirely from project-authored synthetic data (a fictional textbook library) —
or launch the app and open the user guide.
