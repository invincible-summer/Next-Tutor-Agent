# student_model — M2 学生画像与统一学习证据

回答「这个学生会什么」：维护学生的长期画像（身份/学段/可变偏好），并以统一学习证据 journal（`students/<id>.learning_evidence.jsonl`，唯一事实源）承载全部学习评价——画像无任何能力数值字段。

## Owns

- 顶层：`manager.py`（`StudentModel` facade 与 `is_enabled`）、`state.py`（`StudentProfile` / `LearningStyle`）、`store.py`（`students/<id>.json` 读写）、`style_inference.py`（反馈窗口折叠为风格翻转，纯规则零 LLM）
- `evaluation/` 子包——统一学习评价域：
  - `schema.py` / `store.py` — journal 事务数据类与 `learning_evidence.jsonl` 事务 journal（`generation+seq` watermark、`file_lock` 纪律）
  - `service.py` — `LearnerEvaluationService`，唯一事务提交 facade（身份/范围/revision/lease 校验）
  - `dialogue.py` / `grading.py` / `evaluator.py` / `llm.py` — 对话来源受理与 eligibility、MC 确定性判分、开放题语义评价
  - `jobs.py` / `worker.py` / `schedule.py` — 评价作业调度、后台 worker（重启恢复、outbox 推进）、批次调度
  - `scope.py` / `ports.py` — 工作区→卷级授权范围；workspace/textbook/图谱读取经 ports 注入（评价核心不 import API 层）
  - `projections.py` / `readers.py` — 可删除重建的投影；教学消费者唯一合法读取口
  - `lifecycle.py` / `context.py` / `validator.py` — 复核/撤销/删除（generation 重写）、ContextPack 组装、校验

## Does not own

- 怎么教（M3 `teaching_engine`）、是否学会的测评编排（M4 assessment 拥有 `evaluate_submission` 实现入口）
- 跨会话提示词记忆（M6 memory）、UX 维度画像（M8 只向本包供给 `recent_feedback` 信号，写入归 `style_inference`）
- API 路由与 composition root（`api/v1/learner_evaluation.py`、`api/v1/student.py`、`core/learner_runtime.py` 为集成侧）

## Design

两类学习观察来源、受理契约、journal 封闭操作集、投影与读侧契约见
[docs/architecture/student-model.md](../../../../../docs/architecture/student-model.md)。

## Tests

- `services/api/tests/test_learner_{schema,scope,projection,evaluation_policy}.py` — 范围、投影与调度策略
- `test_evidence_{journal,query_core}.py` — journal 事务与查询
- `test_evaluation_{jobs,worker,lifecycle,review,context,routes}.py` — 作业、worker、复核与路由面
- `test_unified_submission.py` / `test_submission_identity.py` / `test_quiz_submission_state.py` — 统一受理与身份
- `test_style_inference.py` / `test_learning_consumers.py` / `test_projection_api.py` — 风格折叠、outbox 消费、`/student/*` 投影

## Key entry points

- `manager.py::StudentModel` — 画像 facade；`evaluation/service.py::LearnerEvaluationService` — 事务提交唯一入口
- `evaluation/readers.py` — M3/M5/M9 等消费者的唯一合法评价读取口
- `evaluation/store.py` — `learning_evidence.jsonl` journal 读写
