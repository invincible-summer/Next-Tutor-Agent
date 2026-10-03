# scripts/evaluation — prompt 回归评测

`run_prompt_eval.py` 对版本化 prompt registry 的讲解/出题/拒答行为做 golden 回归评测，两种模式：

- `--mock`（默认）：对 golden 集做确定性结构/规则断言，零网络、零成本；由 `tests/core/test_prompt_eval.py` 封装进 unittest，全量测试即覆盖。
- `--llm`：真实 LLM 逐条评测（读 `.env`，需网络，产生费用）；仅人工按需执行，CI 永不运行。

## 契约

- **联网**：仅 `--llm` 模式。
- **真实 LLM 费用**：仅 `--llm` 模式；mock 模式为零。
- **输入**：golden 集 `services/api/tests/support/prompt_eval/golden.jsonl`（项目编写的 synthetic 用例，覆盖讲解结构/学段适配/红线拒答/检索忠实度/工具选择）。
- **输出**：结果打印到 stdout，不落仓库、不写运行数据根。
- **推荐调用位置**：mock 门禁经 unittest（`cd services/api && python -m tests tests.core.test_prompt_eval`）；`--llm` 在改 prompt 语义后人工执行并人工审读输出。
- **对应文档**：[docs/development/testing.md](../../docs/development/testing.md)、prompt 体系见 [services/api/app/prompts/](../../services/api/app/prompts/)。
