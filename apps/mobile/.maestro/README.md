# Native mobile flows

These flows target the installed `com.nexttutor.agent` development or preview binary. They are prepared regression assets; no device execution is implied by their presence.

## Prerequisites

- An Android emulator/device or iOS simulator/device with the same binary and backend revision being accepted.
- Maestro CLI on the runner; a local emulator runner is the blocking Android path and a macOS simulator runner is the iOS release path. Hosted EAS execution is supplementary.
- An isolated backend with project-authored synthetic students, a workspace, a source file, a searchable concept, a compiled lesson with at least two slides, and visible learning evidence. Enable the capabilities under test. Use deterministic provider fakes for chat, assessment, illustration, and speech; do not make routine smoke depend on paid live providers.
- A fresh synthetic student for the assessment flow, with no active assessment. The essential-question regression uses a separate student with an active question whose required illustration is unavailable. Reset that deployment between cases that mutate state.
- No production credentials or content. Values below are environment inputs, never committed. Avoid recording the invocation with expanded credentials in public logs.

## Run

From the repository root, set the following environment variables in the runner's secret/environment configuration:

| Variable                                                                                                   | Synthetic fixture value                          |
| ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `MAESTRO_MOBILE_E2E_EMAIL`, `MAESTRO_MOBILE_E2E_PASSWORD`                                                  | Dedicated student login                          |
| `MAESTRO_MOBILE_E2E_WORKSPACE_ID`, `MAESTRO_MOBILE_E2E_WORKSPACE_NAME`                                     | Existing workspace identifier and displayed name |
| `MAESTRO_MOBILE_E2E_FILE_NAME`                                                                             | Existing project-authored source filename        |
| `MAESTRO_MOBILE_E2E_NOTE_TITLE`, `MAESTRO_MOBILE_E2E_GOAL_TITLE`                                           | Unique names for this run                        |
| `MAESTRO_MOBILE_E2E_CHAT_RESPONSE`, `MAESTRO_MOBILE_E2E_CHAT_SESSION_TITLE`                                | Deterministic fake chat response/title selectors |
| `MAESTRO_MOBILE_E2E_ANSWER`                                                                                | Public answer text for the synthetic assessment  |
| `MAESTRO_MOBILE_E2E_SCENARIO_TITLE`                                                                        | Fake scenario session title selector             |
| `MAESTRO_MOBILE_E2E_CONCEPT_NAME`, `MAESTRO_MOBILE_E2E_EVIDENCE_SUMMARY`                                   | Searchable concept and evidence selectors        |
| `MAESTRO_MOBILE_E2E_LESSON_ID`, `MAESTRO_MOBILE_E2E_LESSON_TITLE`, `MAESTRO_MOBILE_E2E_SECOND_SLIDE_TITLE` | Compiled two-slide lesson and visible titles     |
| `MAESTRO_MOBILE_E2E_STT_TEXT`                                                                              | Deterministic fake cloud transcription           |

Store all reports, screenshots, recordings, and backend runtime roots outside the checkout:

```bash
export MAESTRO_MOBILE_E2E_ARTIFACT_DIR="$(mktemp -d /tmp/next-tutor-mobile-e2e.XXXXXX)"
maestro test --test-output-dir "$MAESTRO_MOBILE_E2E_ARTIFACT_DIR" apps/mobile/.maestro/smoke/notes.yaml
maestro test --test-output-dir "$MAESTRO_MOBILE_E2E_ARTIFACT_DIR" apps/mobile/.maestro/smoke
```

Maestro CLI automatically imports shell variables prefixed with `MAESTRO_`, accessed through `${...}` expressions. Runner environment injection should supply the values without putting credentials in YAML. Selectors support zh/en; deterministic fixture selectors must match the deployment's chosen language. Inspect selectors against the installed binary before promoting the flows to blocking CI.

`smoke/workspaces.yaml` selects the workspace before workspace-dependent flows. The `ws` deep-link parameter is consumed by chat and classroom; other modules use the shared current workspace. Execute workspace selection first when running insights separately.

`smoke/auth.yaml` clears local app state and signs out the synthetic session; run it only on disposable test devices. iOS Keychain may survive reinstall/clear-state, so use a fresh simulator or explicitly clear this test application's Keychain in runner setup when testing first login.

## Coverage and remaining manual cases

| Flow                                                           | Main assertion                                                        |
| -------------------------------------------------------------- | --------------------------------------------------------------------- |
| `auth`, `workspaces`                                           | Login, session restore, logout, scope switch                          |
| `chat`, `resources`                                            | Server text response, chat history restore, existing source selection |
| `notes`, `plan`                                                | Native sheet creation, edit/save/reload, goal creation                |
| `assessment`                                                   | Public text/choice answer, server feedback, stop                      |
| `scenario`                                                     | V3 ready artifact, revision actions, history restore                  |
| `knowledge`, `insights`                                        | Accessible concept list, evidence detail                              |
| `classroom`, `voice`                                           | Native play/pause, resume, cloud fake STT/TTS                         |
| `assistant`                                                    | Bordered edge entry opens global panel                                |
| `adaptive`, `appearance`, `pending-link`, `essential-question` | Rotation, preferences, protected deep link, required-image gate       |

These flows do not yet establish upload/document-picker handling on both OSes, note conflict resolution with two clients, token expiry mid-stream, offline reconnect, scenario response-loss idempotency, source/base revision distinction, all V1/V2/V3 assessment cases, classroom lease takeover/checkpoint submission, or complete tablet/a11y/performance acceptance. Execute those in the device matrix described in [mobile validation](../../../docs/validation/mobile-validation.md) before release. System document pickers and permission dialogs need OS-specific selector checks; exercise attachment with only project-authored synthetic text/images from isolated runtime storage.

See [parameters and shell variables](https://docs.maestro.dev/maestro-flows/flow-control-and-logic/parameters-and-constants), [Maestro command reference](https://docs.maestro.dev/reference/commands-available) for command/selector syntax and [artifact output](https://docs.maestro.dev/reference/commands-available/takescreenshot) for screenshot handling.
