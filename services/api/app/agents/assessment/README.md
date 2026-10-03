# Assessment（M4 智能测评）

M4 回答「学生真的学会了吗」：承载练习题生成、统一作答受理与三级判分、量规（rubric）冻结、CAT 自适应测试、提示与异议复核。

## Owns

- `manager.py` — 统一受理 facade：`evaluate_submission`、`register_task_snapshot(s)` / `load_task_snapshot`、`record_assistance`、`resolve_submission_binding`、`run_assessment_job` 与错误族（`QuestionNotFound` / `QuestionRevisionMismatch` / `QuestionAlreadyAnswered` / `AnswerTooLarge` / `SessionNotOwned` / `WorkspaceNotOwned` / `AssessmentBindingError` / `ScopeRevisionConflict`）。
- `adaptive_test.py` — CAT 自适应测试实例：`CatInstance`、难度步进 `next_difficulty`、停止判定 `should_stop`、报告 `report`、journal 持久化。
- `generator.py` — M4 约束出题。
- `question.py` — 题目模型 `Question` / `QuestionType`。
- `state.py` — `AssessmentContext` 出题上下文。

## Does not own

- 统一学习评价域（journal 事务、开放题语义作业、worker、投影、复核）在 `agents/student_model/evaluation/`；本包是其出题/受理侧的主要生产者与消费者。
- 出题质量门与生成预算（`core/quiz_verify.py` / `core/quiz_design.py` / `core/quiz_generation_budget.py`）及出题工具（`tools/quiz.py` / `tools/fit_quiz.py`）在包外。
- API 路由在 `api/v1/quiz.py`、`api/v1/assessment.py`、`assessment_grounding.py`、`assessment_illustration.py`。
- 与 `agents/evaluation/`（M7）的边界：本包是**学生测评**（这一题这个学生答得如何）；M7 是**教学系统评估**（教师 Agent 自己是否越来越好）。
- 题图（question illustration）管线与工具调用协议归 diagrams-illustration / skill-runtime 域。

## Design

- 三条出题路径（聊天工具 `generate_quiz` / `fit_quiz`、M4 约束出题与 CAT）共用同一套质量门（结构校验 + critic 独立重解 + 命题蓝图两轮化）与生成预算；题目连同冻结量规以 TaskSnapshot 注册进学习证据 journal。
- 所有作答（聊天题卡、习题中心、CAT）经唯一入口 `evaluate_submission` 进入统一学习评价协议：服务端权威题目、答案指纹幂等、MC 确定性判分、开放题异步语义作业。
- 量规在学生作答前冻结（`rubric_hash`，改量规必须新 revision）；帮助事件在判分前入账（帮助后的表现不伪装独立）；异议走 journal 复核生命周期，可撤销错误解释而不抹除原始证据。
- 权威事实源与完整协议见 [docs/architecture/assessment.md](../../../../../docs/architecture/assessment.md)。

## Tests

`services/api/tests/`（`python -m tests tests.test_<module>`）：

- 统一受理：`test_unified_submission.py`、`test_submission_identity.py`、`test_quiz_submission_state.py`、`test_quiz_ownership.py`。
- 测评中心与 CAT：`test_assessment.py` 及 `test_assessment_*` 系列（lifecycle / identity / binding_cas / generation_fast）、`test_classroom_assessment.py`。
- 出题质量：`test_quiz_quality.py`、`test_quiz_design.py`、`test_quiz_card_contract.py`、`test_quiz_grounding*.py`。
- 评价域与复核（M2 统一评价域，与本包受理侧共测）：`test_evaluation_jobs.py`、`test_evaluation_review.py`、`test_evidence_journal.py`、`test_question_audit.py`。

## Key entry points

- `manager.evaluate_submission` — 统一作答受理唯一入口。
- `manager.register_task_snapshot(s)` / `load_task_snapshot` — 题目与冻结量规的注册/加载。
- `manager.record_assistance` / `manager.run_assessment_job` — 帮助入账与测评作业执行。
- `adaptive_test.next_difficulty` / `should_stop` / `report` — CAT 生命周期。
