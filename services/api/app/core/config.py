"""Configuration loaded from environment / .env.

Never read API keys anywhere except here. Everything else imports `settings`.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from . import paths
from .paths import runtime_paths


def _under_test_runner() -> bool:
    # Test runs force the keyless CI environment (tests/__init__.py sets the
    # flag and scrubs the variables); never load real credentials there.
    # `unittest in sys.modules` covers `discover` imports where a test module
    # reaches `app.*` before the tests package scrub runs (order-dependent
    # hermeticity hole); the runner package is always imported first by
    # `python -m unittest`, and never present in production processes.
    return os.environ.get("EDU_TEST_KEYLESS") == "1" or "unittest" in sys.modules


if not _under_test_runner():
    load_dotenv(paths.repo_root() / ".env")


def _resolve_skill_runtime_mode() -> str:
    mode = os.getenv("SKILL_RUNTIME_MODE", "shadow").strip().lower()
    return mode if mode in {"off", "shadow", "gated"} else "shadow"


def _resolve_mode(name: str, allowed: set[str], default: str) -> str:
    """Whitelist-normalize a multi-valued mode env (§13.6 纪律：非法值回默认，
    不原样透传）。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip().lower()
    return raw if raw in allowed else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no", ""}


def _resolve_trace_dir() -> str:
    """Resolve TRACE_DIR against the runtime data root (absolute env wins).

    All runtime state (traces, uploads, sessions, knowledge, notes, ...)
    lives under the single data root owned by app.core.paths; a relative
    TRACE_DIR therefore anchors there instead of the process cwd.
    """
    raw = os.getenv("TRACE_DIR", "").strip()
    if raw:
        p = Path(raw)
        return str(p if p.is_absolute() else runtime_paths().root / p)
    return str(runtime_paths().traces)


@dataclass
class Settings:
    # 单一 LLM 通道（2026-09 精简）：DeepSeek 官方或任一 OpenAI 兼容端点
    # 二选一，聊天/推理/出题/课堂/视觉 OCR 共用同一模型（请选多模态模型；
    # 模型无视觉能力时 OCR 自动回退本地 tesseract）。
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    # Image gateway: one server-selected provider/protocol.  The client never
    # chooses a provider or model; arbitrary model identifiers are forwarded as
    # configured so OpenAI-compatible third-party endpoints work without a
    # code/catalog release.
    image_api_enabled: bool = _env_bool("IMAGE_API_ENABLED", False)
    image_api_provider: str = os.getenv("IMAGE_API_PROVIDER", "custom").strip() or "custom"
    image_api_protocol: str = _resolve_mode(
        "IMAGE_API_PROTOCOL",
        {"openai_compatible", "dashscope", "seedream"},
        "openai_compatible",
    )
    image_api_base_url: str = os.getenv("IMAGE_API_BASE_URL", "").strip().rstrip("/")
    image_api_key: str = os.getenv("IMAGE_API_KEY", "").strip()
    image_api_model: str = os.getenv("IMAGE_API_MODEL", "").strip()
    image_api_timeout_seconds: int = max(15, min(300, int(os.getenv("IMAGE_API_TIMEOUT_SECONDS", "120"))))
    image_api_supports_reference: bool = _env_bool("IMAGE_API_SUPPORTS_REFERENCE", True)
    # Legacy provider-specific values are kept during migration.  New code
    # only uses them when the unified IMAGE_API_* block is incomplete.
    gpt_image_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    gpt_image_base_url: str = os.getenv("OPENAI_IMAGE_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    qwen_image_api_key: str = os.getenv("DASHSCOPE_API_KEY", "").strip()
    qwen_image_base_url: str = os.getenv("QWEN_IMAGE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1/services/aigc/image-generation/generation").rstrip("/")
    seed_image_api_key: str = os.getenv("SEED_API_KEY", os.getenv("ARK_API_KEY", "")).strip()
    seed_image_base_url: str = os.getenv("SEED_IMAGE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3/images/generations").rstrip("/")
    # 2026-09 DeepSeek API 只接受 deepseek-flash / deepseek-v4-pro；
    # 旧名（deepseek-v4-flash 等）会被 400 invalid_request_error 拒绝，
    # 表现为出题/对话全部 generation_failed。
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-flash")
    # CAT must not spend the 180-second generic budget on nested retries.  One
    # initial attempt plus one bounded fallback is enough for transient model
    # variance while keeping the endpoint responsive.
    assessment_generation_max_attempts: int = max(
        1, min(2, int(os.getenv("ASSESSMENT_GENERATION_MAX_ATTEMPTS", "2"))))
    assessment_generation_max_calls: int = max(
        2, min(8, int(os.getenv("ASSESSMENT_GENERATION_MAX_CALLS", "6"))))
    assessment_generation_deadline_seconds: int = max(
        30, min(180, int(os.getenv("ASSESSMENT_GENERATION_DEADLINE_SECONDS", "90"))))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "4000"))
    # 上下文/输出预算的内置默认值 = 2026-09 实例实际值；管理员可在
    # 「运行参数」面板在线调整（core/llm_policy.py，热更新），env 值仅在
    # 策略文件缺失时作为初始默认。
    llm_context_window: int = int(os.getenv("LLM_CONTEXT_WINDOW", "165536"))
    llm_max_output_tokens: int = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "20000"))
    llm_context_safety_margin: int = int(os.getenv("LLM_CONTEXT_SAFETY_MARGIN", "2500"))
    context_soft_trigger_ratio: float = float(os.getenv("CONTEXT_SOFT_TRIGGER_RATIO", "0.72"))
    context_hard_trigger_ratio: float = float(os.getenv("CONTEXT_HARD_TRIGGER_RATIO", "0.88"))
    context_history_max_tokens: int = int(os.getenv("CONTEXT_HISTORY_MAX_TOKENS", "30000"))
    context_recent_full_turns: int = int(os.getenv("CONTEXT_RECENT_FULL_TURNS", "4"))
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai_compatible")
    # Direct uvicorn remains conservative by default; start.sh promotes the
    # complete adapter mode explicitly after reading non-secret .env values.
    llm_runtime_mode: str = os.getenv("LLM_RUNTIME_MODE", "shadow").strip().lower()
    llm_supports_reasoning: bool = _env_bool("LLM_SUPPORTS_REASONING", True)
    llm_supports_disable_thinking: bool = _env_bool("LLM_SUPPORTS_DISABLE_THINKING", True)
    llm_supports_reasoning_effort: bool = _env_bool("LLM_SUPPORTS_REASONING_EFFORT", False)
    llm_supports_reasoning_budget: bool = _env_bool("LLM_SUPPORTS_REASONING_BUDGET", False)
    llm_reports_reasoning_tokens: bool = _env_bool("LLM_REPORTS_REASONING_TOKENS", False)
    llm_supports_native_tool_messages: bool = _env_bool("LLM_SUPPORTS_NATIVE_TOOL_MESSAGES", True)
    reasoning_summary_level: str = os.getenv("REASONING_SUMMARY_LEVEL", "adaptive").strip().lower()
    tool_context_projection_mode: str = os.getenv("TOOL_CONTEXT_PROJECTION_MODE", "on").strip().lower()
    tool_message_mode: str = os.getenv("TOOL_MESSAGE_MODE", "native").strip().lower()
    tool_context_current_max_chars: int = int(os.getenv("TOOL_CONTEXT_CURRENT_MAX_CHARS", "6000"))
    tool_context_old_preview_chars: int = int(os.getenv("TOOL_CONTEXT_OLD_PREVIEW_CHARS", "400"))
    # P9 反碎片化：单条证据摘录的字符上限（证据门 evidence_excerpt 用）。
    # 旧值 500 的句窗把 520-token 的结构化 chunk 压成孤立碎片（取证：
    # 「式（8.50）」被切成分式残段），900 配合课文级合并/邻块扩展给出完整语境。
    rag_evidence_excerpt_chars: int = int(os.getenv("RAG_EVIDENCE_EXCERPT_CHARS", "900"))
    # Post-generation quiz verification: critic = 结构校验 + LLM 独立重解审题,
    # basic = 仅确定性结构校验, off = 旧行为（不校验）。
    quiz_verify_mode: str = os.getenv("QUIZ_VERIFY_MODE", "critic").strip().lower()
    # Opt-in rollout; independent from semantic verification and user prefs.
    # SVG question diagrams are available by default; the deployment-level
    # switch remains an emergency/rollout kill switch, while each account can
    # opt out from the assessment center.
    quiz_svg_enabled: bool = os.getenv("QUIZ_SVG_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
    quiz_diagram_mode: str = os.getenv("QUIZ_DIAGRAM_MODE", "components").strip().lower()
    quiz_illustration_pipeline: str = _resolve_mode(
        "QUIZ_ILLUSTRATION_PIPELINE", {"v1", "shadow", "v2", "v3"}, "shadow")
    quiz_illustration_visual_review: str = _resolve_mode(
        "QUIZ_ILLUSTRATION_VISUAL_REVIEW", {"off", "shadow", "active"}, "active")
    quiz_illustration_max_calls: int = max(1, min(10, int(os.getenv("QUIZ_ILLUSTRATION_MAX_CALLS", "10"))))
    quiz_illustration_deadline_seconds: int = max(1, min(120, int(os.getenv("QUIZ_ILLUSTRATION_DEADLINE_SECONDS", "120"))))
    quiz_illustration_max_repairs: int = max(0, min(2, int(os.getenv("QUIZ_ILLUSTRATION_MAX_REPAIRS", "2"))))
    llm_supports_images: bool = _env_bool("LLM_SUPPORTS_IMAGES", False)
    # 出题两轮化：two_pass = 生成前先做一轮命题蓝图设计（考查角度/认知层级/
    # 陷阱设计），第二轮按蓝图写题；single = 旧行为（单轮直出）。
    # 蓝图轮失败时自动回退 single（fail-open，同 quiz_verify 哲学）。
    quiz_design_mode: str = os.getenv("QUIZ_DESIGN_MODE", "two_pass").strip().lower()
    # W3/D06 结构化评估（量规条目判定）：off = 旧行为（三级文本批改）；
    # shadow = 旁路计算并落盘对照、不写能力不改变判定；active = 有冻结量规的
    # 开放题以结构化分析为权威判定（分数仍由服务端按量规权重计算）。
    structured_assessment_mode: str = _resolve_mode(
        "STRUCTURED_ASSESSMENT_MODE", {"off", "shadow", "active"}, "off")
    # W3/D08 教学决策适配：rules = 纯规则策略（默认，零新增调用）；
    # shadow = 旁路调用并记录对照、不应用；active = 校验通过的教学决策以
    # 受限方式调整策略（单一主要行动/枚举白名单/显式约束优先）。
    teaching_decision_mode: str = _resolve_mode(
        "TEACHING_DECISION_MODE", {"rules", "shadow", "active"}, "rules")
    # 统一语义学习评价（工程预算，非教育测量阈值）。
    learner_evaluation_mode: str = _resolve_mode(
        "LEARNER_EVALUATION_MODE", {"active", "off"}, "active")
    learner_evaluation_concurrency: int = int(
        os.getenv("LEARNER_EVALUATION_CONCURRENCY", "2"))
    learner_eval_wall_deadline: int = int(
        os.getenv("LEARNER_EVAL_WALL_DEADLINE", "45"))
    learner_eval_transport_max: int = int(
        os.getenv("LEARNER_EVAL_TRANSPORT_MAX", "4"))
    learner_eval_job_budget: int = int(
        os.getenv("LEARNER_EVAL_JOB_BUDGET", "120"))
    learner_eval_lease_seconds: int = int(
        os.getenv("LEARNER_EVAL_LEASE_SECONDS", "150"))
    learner_eval_synthesis_merge_wait: int = int(
        os.getenv("LEARNER_EVAL_SYNTHESIS_MERGE_WAIT", "15"))
    learner_eval_clt_sample_ratio: float = float(
        os.getenv("LEARNER_EVAL_CLT_SAMPLE_RATIO", "0.2"))
    # 工具步允许保留模型思考（LOW，不下发关闭指令）：预算充足时让推理发生，
    # real_summary 才有真实材料；预算被压缩时 executor 的 budget_forces_direct
    # 仍会强制关思考， starving 时走 incomplete_answer_recovery 兜底。
    # 0 = 旧行为（工具步一律关闭思考）。
    executor_tool_thinking: bool = _env_bool("EXECUTOR_TOOL_THINKING", True)
    # 工具阶段的输出信封上限（max_tokens 是上限不是消费）。思考型模型会把
    # reasoning_content 也算进同一个信封：4000 的旧硬顶常被思考吃光、答案
    # 通道为空，触发 incomplete_answer_recovery 重试——重试本身就烧掉一整
    # 次 prompt+completion，比放大信封贵得多。窗口富余时放宽到 6000 反而
    # 省 token；窗口紧张的部署可用 env 调回。
    executor_tool_max_output_tokens: int = int(os.getenv("EXECUTOR_TOOL_MAX_OUTPUT_TOKENS", "6000"))
    # Live thinking stream gate (agents/reasoning_live.py): -1 (default)
    # streams provider reasoning_content deltas to the browser as thinking
    # events (display-only); 0 restores the hidden-CoT behavior (template
    # summaries only + grounding turns force-disable thinking); >0 caps the
    # total streamed chars per turn. Raw CoT is still never persisted or TTS'd.
    reasoning_live_max_chars: int = int(os.getenv("REASONING_LIVE_MAX_CHARS", "-1"))
    # OpenAI 兼容门面（清小搭广场类平台接入）：部署方签发的接入凭证。
    # 空 = 门面关闭（/models、/chat/completions 返回 503）。
    compat_api_key: str = os.getenv("COMPAT_API_KEY", "")
    # 门面会话的默认学段（接入侧没有学段概念，按部署面向的学生群体设定）。
    compat_grade: str = os.getenv("COMPAT_GRADE", "本科")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))

    # Multimodal (2026-09 精简)：不再有独立视觉通道——图片理解/逐页 OCR
    # 直接走上方主 LLM 通道（见 core/ocr.py / core/multimodal_context.py）；
    # 模型无视觉能力或调用失败时回退本地 tesseract OCR（chi_sim+eng，离线）。

    # Embedding (optional): explicit provider keeps old deployments off by
    # default. ``local`` is a generic bring-your-own interface for offline
    # embedding models — no default model is shipped or pinned, the operator
    # must set EMBEDDING_MODEL/EMBEDDING_MODEL_PATH explicitly. ``openai``
    # keeps the existing OpenAI-compatible endpoint. Any failure degrades to
    # the deterministic BM25 lane.
    embedding_provider: str = (os.getenv("EMBEDDING_PROVIDER", "off").strip().lower()
                               if os.getenv("EMBEDDING_PROVIDER", "off").strip().lower()
                               in {"off", "local", "openai"} else "off")
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL") or ""
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY") or ""
    embedding_model: str = os.getenv("EMBEDDING_MODEL") or ""
    embedding_model_path: str = os.getenv("EMBEDDING_MODEL_PATH") or ""
    embedding_cache_dir: str = os.getenv("EMBEDDING_CACHE_DIR") or ""
    embedding_device: str = os.getenv("EMBEDDING_DEVICE", "cpu").strip().lower() or "cpu"
    embedding_batch_size: int = max(1, int(os.getenv("EMBEDDING_BATCH_SIZE", "32")))
    embedding_max_threads: int = max(1, int(os.getenv("EMBEDDING_MAX_THREADS", "2")))

    # Chroma persistent dir for the vector index (defaults into the project).
    chroma_dir: str = os.getenv("CHROMA_DIR") or str(runtime_paths().vector_db)
    # Hybrid retrieval master switch (BM25 + vector RRF). 0 = pure BM25.
    rag_hybrid: bool = os.getenv("RAG_HYBRID", "1") not in ("0", "false", "False", "off")
    # Structured RAG V2 rollout controls. Invalid values degrade to safe defaults.
    rag_chunker_mode: str = (os.getenv("RAG_CHUNKER_MODE", "v2").strip().lower()
                             if os.getenv("RAG_CHUNKER_MODE", "v2").strip().lower() in {"legacy", "v2"}
                             else "v2")
    rag_evidence_gate: str = (os.getenv("RAG_EVIDENCE_GATE", "on").strip().lower()
                              if os.getenv("RAG_EVIDENCE_GATE", "on").strip().lower() in {"off", "shadow", "on"}
                              else "on")
    rag_context_compress: bool = _env_bool("RAG_CONTEXT_COMPRESS", True)
    # 原生 PDF（文本层）图表收割：find_tables 表格结构化 + 位图区域提取→
    # 多模态图述，连同 PDF page label 印刷页码并入 .txt 事实源。关闭即
    # 降级为纯文本层（扫描书路径不受影响——其标记由 OCR prompt 直接产出）。
    rag_figure_harvest: bool = _env_bool("RAG_FIGURE_HARVEST", True)

    # 图谱设计阶段（P7.7.4 W9b）：合并 spec 后由主 LLM 做标签统一/同义归并/
    # 跨章继承判断。关闭即降级纯本地（现行为），失败自动降级不阻塞。
    graph_design_mode: bool = _env_bool("GRAPH_DESIGN_MODE", True)

    # Tutor
    # （default_grade 已删除——学段由用户画像/自动识别提供，此配置项零消费。）
    agent_max_steps: int = int(os.getenv("AGENT_MAX_STEPS", "6"))
    # M10 Skill Runtime rollout. ``shadow`` records only; ``gated`` enforces
    # preconditions and stepwise tools; ``off`` disables decision/card diagnostics
    # while keeping registry-backed role projection.
    skill_runtime_mode: str = _resolve_skill_runtime_mode()

    # P2 教材库：上传教材 PDF → 自动解析+RAG 索引+自动构建知识图谱。
    # TEXTBOOK_GRAPH_ENABLED=0 时上传只解析+索引，跳过图谱构建（教材仍可检索）。
    textbook_graph_enabled: bool = _env_bool("TEXTBOOK_GRAPH_ENABLED", True)
    # 教材图谱规模上限（spec_to_graph 形参，覆盖自定义图谱的 15/120 默认）。
    textbook_graph_max_chapters: int = int(os.getenv("TEXTBOOK_GRAPH_MAX_CHAPTERS", "30"))
    textbook_graph_max_concepts: int = int(os.getenv("TEXTBOOK_GRAPH_MAX_CONCEPTS", "400"))

    # P6-D 记忆收敛：跨会话记忆调用范围。workspace（默认）= 仅工作区内跨对话
    # （公共记忆 + 同工作区会话召回）；all = 旧行为（该生全部会话）；off = 关闭。
    cross_session_memory: str = os.getenv("CROSS_SESSION_MEMORY", "workspace").strip().lower()
    # 扫描版/图片型 PDF OCR 回退（逐页渲染→OCR→拼回文本，复用 RAG/图谱管线）。
    #   auto = 仅扫描 PDF（文本层稀疏）触发；on = 所有 PDF 都 OCR（调试）；off = 禁用（扫描 PDF 被拒）
    pdf_ocr_mode: str = os.getenv("PDF_OCR_MODE", "auto").strip().lower()
    # 教材库后台 OCR 页数上限（默认 1024 页，覆盖整本教材；大书成本可控，超限截断 + warning）。
    pdf_ocr_max_pages: int = int(os.getenv("PDF_OCR_MAX_PAGES", "1024"))
    # 对话/资料库同步 OCR 页数上限（保护上传响应性；大书引导到教材库后台）。
    pdf_ocr_sync_max_pages: int = int(os.getenv("PDF_OCR_SYNC_MAX_PAGES", "20"))
    # PDF 逐页渲染 DPI（越高越准越慢；200 是扫描正文常用档）。
    pdf_ocr_dpi: int = int(os.getenv("PDF_OCR_DPI", "200"))
    # 教材库后台 OCR 并行批次大小（每批渲染 N 页后并发 vision 调用）。
    # 1 = 串行（旧行为）；用户可在账户资料 prefs.ocr_parallel 逐人覆盖开关。
    pdf_ocr_concurrency: int = int(os.getenv("PDF_OCR_CONCURRENCY", "20"))

    # 教材解析流水线调度（仅改执行顺序，不改解析方式/产出）。策略文件
    # chat_history/settings/textbook_pipeline_policy.json 优先生效，此处为
    # 文件缺失时的默认值；管理员可经 /admin/textbook-pipeline 在线调整。
    #   legacy = 原有严格串行（所有有效并发强制 1，与历史行为完全一致）。
    textbook_parse_mode: str = os.getenv("TEXTBOOK_PARSE_MODE", "parallel").strip().lower()
    # 同一 owner（学生/公共命名空间）同时在构建的教材数。
    textbook_build_concurrency: int = int(os.getenv("TEXTBOOK_BUILD_CONCURRENCY", "2"))
    # 教材组内并行处理的卷数。
    textbook_volume_concurrency: int = int(os.getenv("TEXTBOOK_VOLUME_CONCURRENCY", "2"))
    # 图谱 LLM 调用（目录/骨架/逐章概念/图谱设计）全局并发上限。
    textbook_llm_concurrency: int = int(os.getenv("TEXTBOOK_LLM_CONCURRENCY", "4"))

    # Traces
    trace_dir: str = _resolve_trace_dir()

    # Voice call: browser SpeechRecognition or server-mediated mobile STT,
    # plus pluggable TTS output. Provider secrets remain server-side.
    # TTS: off | stub | melo | azure | iflytek | deepgram | auto
    voice_tts_provider: str = os.getenv("VOICE_TTS_PROVIDER", "off").strip().lower()
    # MeloTTS sidecar base URL (localhost only; started by start.sh).
    voice_tts_base_url: str = os.getenv("VOICE_TTS_BASE_URL", "http://127.0.0.1:8130")
    # 0.9：默认略慢于原速（2026-08-31 用户反馈），个人可在设置页
    # user.profile.prefs.tts_speed 覆盖（有效区间与 sidecar 一致 0.5–2.0）。
    voice_tts_speed: float = float(os.getenv("VOICE_TTS_SPEED", "0.9"))
    # 服务端 STT（移动端 /speech/transcriptions；ADR-0012）：off | stub |
    # azure | iflytek | deepgram | auto。auto 按已配置凭证选择，否则关闭。
    speech_stt_provider: str = os.getenv(
        "SPEECH_STT_PROVIDER", "off").strip().lower()
    # iFlytek WebSocket credentials stay server-side.  Endpoints are optional
    # only for tests; production adapters validate their approved xfyun.cn host.
    iflytek_app_id: str = os.getenv("IFLYTEK_APP_ID", "").strip()
    iflytek_api_key: str = os.getenv("IFLYTEK_API_KEY", "").strip()
    iflytek_api_secret: str = os.getenv("IFLYTEK_API_SECRET", "").strip()
    iflytek_stt_endpoint: str = os.getenv(
        "IFLYTEK_STT_ENDPOINT", "wss://iat-api.xfyun.cn/v2/iat").strip()
    iflytek_tts_endpoint: str = os.getenv(
        "IFLYTEK_TTS_ENDPOINT", "wss://tts-api.xfyun.cn/v2/tts").strip()
    # Deepgram's REST APIs use one server-side API key for both directions.
    deepgram_api_key: str = os.getenv("DEEPGRAM_API_KEY", "").strip()
    deepgram_base_url: str = os.getenv(
        "DEEPGRAM_BASE_URL", "https://api.deepgram.com").strip().rstrip("/")
    deepgram_stt_model: str = os.getenv("DEEPGRAM_STT_MODEL", "nova-3").strip()
    deepgram_tts_model: str = os.getenv(
        "DEEPGRAM_TTS_MODEL", "aura-2-thalia-en").strip()

    # Server
    api_host: str = os.getenv("API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("API_PORT", "8000"))

    # ------------------------------------------------------------------
    # 站内学习助手（默认 0，发布阶段显式启用）
    # ------------------------------------------------------------------
    site_assistant_enabled: bool = _env_bool("SITE_ASSISTANT_ENABLED", False)
    # §26.5 领域写入与预览许可（B05 起）；默认随助手开启，灰度可单独关闭。
    site_assistant_actions_enabled: bool = _env_bool(
        "SITE_ASSISTANT_ACTIONS_ENABLED", True)
    # §26.5 工作流创建/执行（C01 起）；默认随助手开启。
    site_assistant_workflows_enabled: bool = _env_bool(
        "SITE_ASSISTANT_WORKFLOWS_ENABLED", True)
    # §26.5 订阅调度（C04 起）；自动建议默认关闭（§14.2/§17-4），须显式
    # 启用且用户逐项开启订阅，不随总开关一次性开放。
    site_assistant_proactive_enabled: bool = _env_bool(
        "SITE_ASSISTANT_PROACTIVE_ENABLED", False)

    # ------------------------------------------------------------------
    # 课堂模式（默认值为开发阶段值，验收后发布模板设 1）
    # ------------------------------------------------------------------
    classroom_enabled: bool = _env_bool("CLASSROOM_ENABLED", False)
    # 默认仅认证用户可生成/播放私有课堂；测试可显式放行游客
    classroom_allow_guest: bool = _env_bool("CLASSROOM_ALLOW_GUEST", False)
    # 灰度 allowlist：空为不限制（在 enabled 之上再收窄）
    classroom_allowed_users: str = os.getenv("CLASSROOM_ALLOWED_USERS", "").strip()
    # 联网检索：provider 无 key 即不可用，不伪联网
    classroom_web_provider: str = os.getenv("CLASSROOM_WEB_PROVIDER", "tavily").strip().lower()
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "").strip()
    # 图片：顺序固定，可配置禁某个 provider
    classroom_image_providers: str = os.getenv(
        "CLASSROOM_IMAGE_PROVIDERS", "pexels,pixabay").strip().lower()
    pexels_api_key: str = os.getenv("PEXELS_API_KEY", "").strip()
    pixabay_api_key: str = os.getenv("PIXABAY_API_KEY", "").strip()
    # 课堂语音策略：auto | cloud | local | silent；云端 provider 可选 Azure、讯飞、Deepgram
    classroom_tts_policy: str = _resolve_mode(
        "CLASSROOM_TTS_POLICY", {"auto", "cloud", "local", "silent"}, "auto")
    classroom_tts_cloud_provider: str = os.getenv(
        "CLASSROOM_TTS_CLOUD_PROVIDER", "azure").strip().lower()
    azure_speech_key: str = os.getenv("AZURE_SPEECH_KEY", "").strip()
    azure_speech_region: str = os.getenv("AZURE_SPEECH_REGION", "").strip()
    # 可选官方资源域；不能由普通用户配置
    azure_speech_endpoint: str = os.getenv("AZURE_SPEECH_ENDPOINT", "").strip()
    classroom_tts_voice_zh: str = os.getenv(
        "CLASSROOM_TTS_VOICE_ZH", "zh-CN-XiaoxiaoNeural").strip()
    classroom_tts_voice_en: str = os.getenv(
        "CLASSROOM_TTS_VOICE_EN", "en-US-JennyNeural").strip()
    # None = 继承 VOICE_TTS_PROVIDER 是否为 melo/auto；显式 1 才独立启用课堂本地回退
    classroom_local_tts_enabled: bool | None = (
        None if os.getenv("CLASSROOM_LOCAL_TTS_ENABLED") is None
        else _env_bool("CLASSROOM_LOCAL_TTS_ENABLED", False))
    classroom_tts_local_fallback: bool = _env_bool(
        "CLASSROOM_TTS_LOCAL_FALLBACK", True)
    # 受限作业调度
    classroom_job_concurrency: int = max(1, int(os.getenv("CLASSROOM_JOB_CONCURRENCY", "2")))
    classroom_owner_concurrency: int = max(1, int(os.getenv("CLASSROOM_OWNER_CONCURRENCY", "1")))
    classroom_llm_concurrency: int = max(1, int(os.getenv("CLASSROOM_LLM_CONCURRENCY", "3")))
    classroom_tts_cloud_concurrency: int = max(1, int(os.getenv("CLASSROOM_TTS_CLOUD_CONCURRENCY", "2")))
    classroom_job_timeout_seconds: int = max(60, int(os.getenv("CLASSROOM_JOB_TIMEOUT_SECONDS", "900")))
    classroom_max_pages: int = max(1, int(os.getenv("CLASSROOM_MAX_PAGES", "24")))
    classroom_max_revisions: int = max(1, int(os.getenv("CLASSROOM_MAX_REVISIONS", "20")))
    classroom_audio_cache_mb: int = max(50, int(os.getenv("CLASSROOM_AUDIO_CACHE_MB", "500")))
    classroom_audio_ttl_days: int = max(1, int(os.getenv("CLASSROOM_AUDIO_TTL_DAYS", "7")))
    classroom_export_ttl_hours: int = max(1, int(os.getenv("CLASSROOM_EXPORT_TTL_HOURS", "24")))
    classroom_render_timeout_seconds: int = max(5, int(os.getenv("CLASSROOM_RENDER_TIMEOUT_SECONDS", "45")))
    # 部署级可信可执行文件与固定脚本路径；客户端/模型不可改写
    classroom_node_bin: str = os.getenv("CLASSROOM_NODE_BIN", "node").strip()
    classroom_render_script: str = os.getenv(
        "CLASSROOM_RENDER_SCRIPT",
        str(paths.repo_root() / "apps" / "web" / "scripts" / "check-classroom-render.mjs")).strip()
    classroom_api_daily_tts_chars: int = max(1000, int(os.getenv("CLASSROOM_API_DAILY_TTS_CHARS", "100000")))

    # ------------------------------------------------------------------
    # 模拟实验台（化学；默认开启，发布灰度可显式关闭）
    # ------------------------------------------------------------------
    chem_lab_enabled: bool = _env_bool("CHEM_LAB_ENABLED", True)
    # 单会话事件上限之外的额外闸门：每 owner 会话数与每会话分支数。
    chem_lab_max_sessions_per_owner: int = max(1, int(os.getenv("CHEM_LAB_MAX_SESSIONS_PER_OWNER", "60")))


settings = Settings()

# Runtime storage roots are owned by app.core.paths; re-bind the two Settings
# fields so set_runtime_root() retargets them together with every other
# storage module (tests, demo exporter, embedders).
paths.bind_storage_path("app.core.config:settings", "trace_dir", "traces", as_str=True)
paths.bind_storage_path("app.core.config:settings", "chroma_dir", "vector_db", as_str=True)


def trace_dir_path() -> Path:
    p = Path(settings.trace_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p
