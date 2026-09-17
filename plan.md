# DeepSeek V4.1 Flash 工具调用协议修复计划

## 1. 问题与结论

当前主模型接入 DeepSeek V4.1 Flash（API 模型名 `deepseek-flash`），主对话采用 OpenAI-compatible Chat Completions。故障表现是 reasoning/deep-thinking 通道正常，但学生可见正文变成：

```text
<｜DSML｜ calls>
<｜DSML｜ invoke name="agent.skill.knowledge.search_materials@1.0.0">
...
</｜DSML｜ calls>
```

这不是前端 Markdown 问题。主因在 Provider 边界：DeepSeek 官方 Chat Completions 对外合同应通过 `tool_calls` 返回函数调用；V4/V4.1 模型内部 DSML 若被兼容网关错误地放入 `delta.content`，原 `AsyncLLMClient` 会把它直接标成 `answer`，随后进入聊天、历史与 TTS。

另有两个耦合缺陷会放大问题：

1. DeepSeek 思考模式下的工具调用，后续请求必须回传该 assistant 消息的完整 `reasoning_content`。原 native tool-message 回填只保存 `content + tool_calls`，可能导致官方 API 400，继而触发项目的 native→legacy 降级。
2. Skill Card 展示的是 `agent.skill.*@version` 规划/审计 ID，但真实 OpenAI function 名是 `knowledge_search` 等。V4.1 在样例中已实际把 Skill ID 当成 function 名输出。

本修复坚持边界清晰：**DSML 是 Provider 协议兼容问题，Executor 只消费统一的 `thinking | answer | tool_calls | done` 事件；Skill Runtime 继续负责能力授权，不让 Provider 兼容层绕过 Skill Gate。**

## 2. 官方协议依据

DeepSeek 官方 API 文档当前合同：

- 官方 API 使用 Chat Completions；主快速模型为 `deepseek-flash`（V4.1 Flash）。
- `tools` / function calling 对外通过 `message.tool_calls` / streamed `delta.tool_calls` 表达。
- Thinking Mode 返回 `reasoning_content`。
- 带 `tools` 的多轮思考工具调用中，历史 assistant 的 `reasoning_content` 必须在下一请求完整回传，否则服务端返回 400。

DeepSeek V4.1 开源模型编码文档同时说明内部 DSML 相较 V4 调整为带空格的标签，例如 `<｜DSML｜ calls>`、`<｜DSML｜ invoke>`、`<｜DSML｜ parameter>`；`string="true"` 表示原始字符串，false 表示 JSON 值。兼容层同时接受旧 V4 无空格 `tool_calls` 格式和部分网关展示出的双全角竖线形式。

参考：

- https://api-docs.deepseek.com/
- https://api-docs.deepseek.com/guides/thinking_mode
- https://api-docs.deepseek.com/guides/tool_calls
- https://api-docs.deepseek.com/api/create-chat-completion

## 3. 代码修改

### 3.1 Provider 边界归一化

新增 `backend/app/core/tool_call_compat.py`：

- `DeepSeekDSMLStreamParser`
  - 支持 V4 `<｜DSML｜tool_calls>`；
  - 支持 V4.1 `<｜DSML｜ calls>`；
  - 支持标签跨任意 streaming chunk；
  - 支持多个 invoke；
  - `string=true` 原样字符串，`string=false` 用 JSON 解码；
  - DSML 一旦开始即 fail-closed，不完整/畸形控制块也绝不重新释放到 answer。
- `normalize_tool_calls`
  - 合并 Provider 已解析的 native tool_calls 与 raw DSML fallback；
  - 按 function + arguments 去重；
  - 保持统一内部事件契约。
- `normalize_tool_call_name`
  - 首选实际 function 名；
  - 支持 V4.1 namespace 后缀；
  - 若模型误发 `agent.skill.*@version`，只通过唯一 Skill Registry 映射；
  - **只有映射后的 function 已存在于本轮真实 tools schema 才接受**，因此不能绕过 Skill Gate。

修改 `backend/app/core/llm_async.py`：

```text
DeepSeek/OpenAI-compatible stream
        ↓
reasoning_content ──→ thinking
normal content ─────→ answer
native tool_calls ──┐
raw DSML content ───┴→ normalize_tool_calls → tool_calls
```

Executor、RAG、Skill Runtime 不解析 DSML。

### 3.2 DeepSeek reasoning/tool round-trip

修改 `backend/app/core/message_protocol.py`：

- `build_openai_tool_messages(..., reasoning_content=None)` 支持 assistant 的 `reasoning_content`；
- `AsyncLLMClient` 对 DeepSeek 工具回合把完整 reasoning 按 call id 暂存于 `ContextVar`；
- 构造下一轮 native assistant/tool messages 时一次性消费；
- raw reasoning 不写 session、不进业务 history、不进入 TTS；
- generic OpenAI-compatible Provider 不会生成该额外字段，保持原消息形状；
- native→legacy 降级显式丢弃 `reasoning_content`，避免其变成学生正文。

之所以使用 call-id transient cache，而不是给整个 Executor/Notes Agent 扩散 reasoning 参数，是为了保持 Provider 私有协议状态局限在 core message protocol 边界，并兼容现有多个 tool-loop 调用点。

### 3.3 Skill ID 与 function name 消歧

修改 `backend/app/prompts/tutor.py::skill_cards_preamble`：

```text
Skill: agent.skill.knowledge.search_materials@1.0.0
执行工具: knowledge_search
```

并明确：

- `agent.skill.*@version` 仅是规划/审计 ID；
- 不是 function 名；
- 真正调用名必须与本轮 tools schema 的 `function.name` 完全一致；
- Advisory Skill 明示“无执行工具”。

Registry 仍是 Skill→Tool 映射的唯一事实来源，不新增第二份 alias 表。

### 3.4 最终防泄漏

修改 `backend/app/agents/pseudo_tool_guard.py`：

- 保留旧 `<knowledge_search>`、`<tool_call><function=...>` 防线；
- 新增 V4/V4.1 DSML 单/双全角竖线前缀；
- 自定义/旧 adapter 即使绕过主 Parser，控制文本仍不能进入 answer/history/TTS；
- 可提取 DSML query 与原始 tool name，用于诊断或现有安全恢复路径。

主 Parser 是正确性路径，PseudoToolGuard 只是 defense-in-depth，不用字符串删除代替真实工具执行。

## 4. 明确保留现状的部分

以下架构本身合理，不为本修复重构：

- 继续使用 Chat Completions，不迁移 Responses API；项目需要兼容 DeepSeek/GLM/vLLM 等 OpenAI-compatible Provider。
- `Tool.to_schema()` 的 OpenAI function schema 保持不变。
- Executor 的“一次完成当前步骤必要 Skill”语义保持不变；Provider parser 可以完整解析多个 invoke，但主教学 ReAct 仍按已有单步策略执行第一个当前授权调用，后续是否再检索由下一模型轮决定。
- BM25/RAG、KnowledgeSearchTool、证据门、前端 SSE 合同均不改。

## 5. 测试

新增 `backend/tests/test_deepseek_tool_call_compat.py`，至少锁定：

1. V4.1 DSML 逐字符切片仍可解析；
2. V4.1 双全角竖线网关显示兼容；
3. V4 旧无空格 DSML 兼容；
4. 多 invoke 与 typed parameters；
5. 畸形/截断 DSML fail-closed；
6. 普通正文零影响；
7. Skill ID 仅在实际 tool schema 授权时才映射为 `knowledge_search`；
8. namespace alias 不能绕过授权；
9. Skill Card 明确真实 function；
10. PseudoToolGuard DSML 防泄漏；
11. `AsyncLLMClient` 集成：reasoning 正常、DSML 不进入 answer、最终产生结构化 `tool_calls`；
12. 下一轮 native tool exchange 包含完整 `reasoning_content`，且暂存值一次性消费。

现有全量 backend suite 与 CI 还必须验证没有 generic Provider / quiz / notes / RAG 回归。

## 6. 验收标准

真实 `deepseek-flash` 对话至少连续验证 10 轮以下教材问题：

> 什么是质点？请依据教材解释什么情况下物体可以看成质点，并说明位置矢量、运动函数、参考系和坐标系之间的关系。

每轮必须满足：

- 深度思考区可继续显示 reasoning；
- 学生正文、持久化 history、TTS 输入中均无 `DSML` / `invoke` / `parameter` / `<tool_call>`；
- 至少一次真实 `knowledge_search` 执行，不能只是删掉 DSML；
- 错发的 `agent.skill.knowledge.search_materials@1.0.0` 能在该工具当前被授权时安全解析为 `knowledge_search`；
- 未授权 Tool 不得因 Skill ID/namespace alias 获得执行权；
- ToolResult 后继续生成正常教学正文；
- 下一模型请求携带上一 DeepSeek 工具回合的完整 `reasoning_content`；
- 不再出现因缺失 reasoning_content 触发的 400 / native→legacy fallback；
- 最终回答仍遵守教材证据约束和页码/来源策略；
- 非 DeepSeek OpenAI-compatible Provider 的原生 tool_calls 路径保持正常。

## 7. 完成条件

代码完成不等于验收完成。合并前需同时满足：

- 新增协议回归测试通过；
- backend 全量测试通过；
- GitHub Actions 通过；
- 至少一次真实 DeepSeek V4.1 Flash live acceptance 证明工具调用和最终回答均正常；若当前 CI 无真实 API secret，则 live acceptance 明确作为部署前人工验收项，不伪报已执行。
