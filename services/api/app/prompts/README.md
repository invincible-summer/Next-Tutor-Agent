# prompts — Prompt 注册表（单一事实来源）

全项目 LLM prompt 文本的单一事实来源：每个 prompt 以 `PromptDef(id, version, text)` 注册并版本化，supervisor/chat_agent 的 turn_start trace 记录 `active_versions()`，让每次回答可溯源到确切 prompt 版本。

## Owns

- `registry.py` — 注册表本体：`get(id[, version])` / `list_versions()` / `active_versions()`；同一 id 可多版本共存（A/B 预留），缺省取 active
- 领域 prompt 文本模块：`tutor.py`（tutor_system、四层 Prompt 控制、skill_cards_preamble、redline_tail）、`quiz_generation.py` / `quiz_rubric.py` / `quiz_illustration.py`（出题/量规/题图）、`learner_evaluation.py`（语义评价）、`classroom.py`（课堂）、`site_assistant.py`（站内助手）、`diagram_material.py`（图示材料）
- 版本化规则：任何文本改动必须 bump patch/minor 版本；同 id 同版本重复注册直接抛错拒绝启动

## Does not own

- Prompt 的注入时机与四层组装（supervisor/GSSC 上下文工程，conversation 域）
- Skill Card 内容本体（M10 Registry 版本化，本目录只承载注入 preamble 的函数）
- 各领域 LLM 调用与解析（tools/agents 层）

## Design

红线（不臆造、不代写、引导为主）、定界标签纪律——`<user_input>`、`<material_excerpt>`、`<ocr_material>`、`<history_excerpt>`、`<workspace_memory>` 等标记内是数据不是指令——与 `redline_tail` 的注入位置见
[docs/architecture/conversation.md](../../../../docs/architecture/conversation.md)（Prompt 工程节）。

## Tests

- `services/api/tests/test_prompt_registry.py` — 注册、版本钉扎与 active 版本语义
- `test_prompt_eval.py` — prompt 效果回归
- 聚焦运行：`cd services/api && python -m tests tests.test_prompt_registry`

## Key entry points

- `registry.py::get(prompt_id)` — 使用方一律经 `get(id).text` 取文本，禁止绕过注册表直引常量
- `registry.py::active_versions()` — trace 的 `prompt_versions` 字段来源
- `tutor.py` — tutor_system 及 Skill Card preamble 的宿主
