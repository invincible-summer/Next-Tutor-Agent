# notes — M-Notes 笔记域包

每用户笔记仓库（vault）与每笔记专属智能体的领域包。自 Phase E 起从 `core/` 迁入（原 `core/notes.py` + `core/notes_templates.py`），存储布局与对外契约不变。

## Owns

- `__init__.py` — 笔记仓库存储：vault 索引、正文、修订历史、智能体状态、资源链接解析、关系图现算；JSON 持久化一律走 `core/atomic.py`。
- `templates.py` — 内置模板骨架与用户自定义模板。

## Does not own

- 笔记智能体编排（M-Notes agent 的 LLM 循环）→ `app/agents/notes_agent.py`。
- API 路由与 schema → `app/api/v1/notes.py`、`app/schemas/`。
- 存储根解析与孤儿数据登记 → `app/core/paths.py`、`app/core/orphan_cleanup.py`（`notes` 类别不变，仅 lazy import 指向本包）。

## Design

架构与数据流：[docs/architecture/notes.md](../../../../docs/architecture/notes.md)。

## Tests

`services/api/tests/notes/`（`test_notes.py`、`test_notes_agent.py`、`test_notes_m9_sync.py`）；聚焦运行：`cd services/api && python3 -m tests tests.notes.test_notes`。

## Key entry points

- `app.notes` — 仓库 API（list/get/save/revise/关系图）
- `app.notes.templates` — `BUILT_IN_TEMPLATES`
