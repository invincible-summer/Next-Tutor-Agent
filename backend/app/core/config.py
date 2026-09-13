"""Configuration loaded from environment / .env.

Never read API keys anywhere except here. Everything else imports `settings`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (parent of backend/) if present.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = _PROJECT_ROOT / "backend"
# Test runs force the keyless CI environment (tests/__init__.py sets the
# flag and scrubs the variables); never load real credentials there.
if os.environ.get("EDU_TEST_KEYLESS") != "1":
    load_dotenv(_PROJECT_ROOT / ".env")


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
    """Resolve TRACE_DIR to an absolute path anchored at backend/.

    Relative values (including the `./traces` default) are resolved against
    backend/ rather than the process cwd, so traces/ and uploads/ (which
    knowledge_store.py derives as `Path(trace_dir).parent / "uploads"`) stay
    in the same place when systemd runs the service with a different
    WorkingDirectory. Absolute values are used as-is (env override).
    """
    p = Path(os.getenv("TRACE_DIR", "./traces"))
    if not p.is_absolute():
        p = _BACKEND_ROOT / p
    return str(p)


@dataclass
class Settings:
    # Main LLM (Chat Completions). Read LLM_* env vars; falls back to
    # DEEPSEEK_* aliases for backward compat with older .env files.
    llm_base_url: str = os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL_REASONING", "deepseek-v4-flash")
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "4000"))
    llm_context_window: int = int(os.getenv("LLM_CONTEXT_WINDOW", "65536"))
    llm_max_output_tokens: int = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8000"))
    llm_context_safety_margin: int = int(os.getenv("LLM_CONTEXT_SAFETY_MARGIN", "2500"))
    context_soft_trigger_ratio: float = float(os.getenv("CONTEXT_SOFT_TRIGGER_RATIO", "0.72"))
    context_hard_trigger_ratio: float = float(os.getenv("CONTEXT_HARD_TRIGGER_RATIO", "0.88"))
    context_history_max_tokens: int = int(os.getenv("CONTEXT_HISTORY_MAX_TOKENS", "24000"))
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
    # 旧值 500 的句窗把 520-token 的结构化 chunk 压成孤立碎片（洛伦兹取证：
    # 「式（8.50）」被切成分式残段），900 配合课文级合并/邻块扩展给出完整语境。
    rag_evidence_excerpt_chars: int = int(os.getenv("RAG_EVIDENCE_EXCERPT_CHARS", "900"))
    # Post-generation quiz verification: critic = 结构校验 + LLM 独立重解审题,
    # basic = 仅确定性结构校验, off = 旧行为（不校验）。
    quiz_verify_mode: str = os.getenv("QUIZ_VERIFY_MODE", "critic").strip().lower()
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
    # 统一语义学习评价（plan §10.3 工程预算，非教育测量阈值）。
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

    # Multimodal (optional): separate API channel for image understanding.
    # If MULTIMODAL_API_KEY is set, image recognition goes through this channel.
    # If not set, falls back to local tesseract OCR (chi_sim+eng, offline).
    multimodal_base_url: str = os.getenv("MULTIMODAL_BASE_URL") or ""
    multimodal_api_key: str = os.getenv("MULTIMODAL_API_KEY") or ""
    multimodal_model: str = os.getenv("MULTIMODAL_MODEL") or ""
    # OCR 页转录是提取任务，推理/思考链纯浪费延迟与输出预算（实测思考型模型
    # 单页 22-36s）。默认下发「关闭思考 + 最低思考强度」；=0 恢复旧行为。
    multimodal_disable_thinking: bool = os.getenv(
        "MULTIMODAL_DISABLE_THINKING", "1") not in ("0", "false", "False", "off")
    # 单页 vision 调用最大尝试次数（异常与空 content 都重试，指数退避+抖动；
    # 耗尽才回退 tesseract）。
    multimodal_ocr_retries: int = int(os.getenv("MULTIMODAL_OCR_RETRIES", "3"))

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
    chroma_dir: str = os.getenv("CHROMA_DIR") or str(_PROJECT_ROOT / "knowledge" / "vector_db")
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

    # Voice call: browser SpeechRecognition input + pluggable TTS output.
    # STT is always performed in the browser; the backend receives final text.
    # TTS: off | stub | melo (MeloTTS sidecar, see backend/voice_sidecar).
    voice_tts_provider: str = os.getenv("VOICE_TTS_PROVIDER", "off").strip().lower()
    # MeloTTS sidecar base URL (localhost only; started by start.sh).
    voice_tts_base_url: str = os.getenv("VOICE_TTS_BASE_URL", "http://127.0.0.1:8130")
    # 0.9：默认略慢于原速（2026-08-31 用户反馈），个人可在设置页
    # user.profile.prefs.tts_speed 覆盖（有效区间与 sidecar 一致 0.5–2.0）。
    voice_tts_speed: float = float(os.getenv("VOICE_TTS_SPEED", "0.9"))

    # Server
    api_host: str = os.getenv("API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("API_PORT", "8000"))


settings = Settings()


def trace_dir_path() -> Path:
    p = Path(settings.trace_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p
