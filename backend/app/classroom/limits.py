"""课堂统一限制常量（plan.md §15.4/§20.1）。

约束只在此定义并通过 capability 返回用户相关限制，不在多份前后端配置
复制。管理员只能通过环境变量调整 settings 层；改动约束必须带验证用例。
"""
from __future__ import annotations

from ..core.config import settings

# 生成频率：单用户新建/重生成（重复幂等请求不扣额）
GENERATION_WINDOW_COUNT = 3
GENERATION_WINDOW_SECONDS = 600
GENERATION_DAILY_COUNT = 20

# 排队
QUEUE_PER_OWNER = 3
QUEUE_GLOBAL = 20

# LLM 预算
def llm_call_budget(planned_pages: int) -> int:
    """全课 LLM 逻辑调用上限：min(40, 2N+8)，N 为计划页数。"""
    return min(40, 2 * max(1, planned_pages) + 8)


LLM_INPUT_TOKEN_BUDGET = 180_000
LLM_OUTPUT_TOKEN_BUDGET = 40_000
SINGLE_LLM_TIMEOUT_SECONDS = 90
SCHEMA_REPAIR_ATTEMPTS = 1
JOB_DEADLINE_SECONDS = settings.classroom_job_timeout_seconds

# 检索/提取（缓存命中不计外部额度，计内部步骤）
SEARCH_CALL_BUDGET = 6
EXTRACT_URL_BUDGET = 8
EXCERPT_CHARS_PER_MATERIAL = 4000
EVIDENCE_CHARS_TOTAL = 40_000

# 图片
IMAGE_SEARCH_BUDGET = 8
IMAGE_COUNT_MAX = 12
IMAGE_DOWNLOAD_TOTAL_BYTES = 64 * 1024 * 1024
EMBEDDED_IMAGE_TOTAL_BYTES = 12 * 1024 * 1024
IMAGE_BYTES_TARGET = 700 * 1024
IMAGE_BYTES_HARD_MAX = 1572864
IMAGE_DOWNLOAD_CONCURRENCY = 2
IMAGE_PER_PAGE_MAX = 2
IMAGE_CANDIDATES_PER_INTENT = 8
IMAGE_CACHE_TTL_HOURS = 24
PIXEL_MAX = 20_000_000
IMAGE_LONG_EDGE_MAX = 1600

# TTS / 音频
TTS_CHARS_PER_RUN = 30_000
TTS_PREVIEW_PER_HOUR = 10
TTS_DAILY_CLOUD_CHARS = settings.classroom_api_daily_tts_chars
AUDIO_QUEUE_MAX = 32
AUDIO_PREFETCH_SEGMENTS = 2
AUDIO_REQUEST_SEGMENTS_MAX = 3
AUDIO_REFS_MAX = 1000
AUDIO_OWNER_MB = settings.classroom_audio_cache_mb
AUDIO_LESSON_MB = 150
AUDIO_TTL_DAYS = settings.classroom_audio_ttl_days
TTS_CONNECT_TIMEOUT_SECONDS = 5
TTS_CLOUD_REQUEST_SECONDS = 25
TTS_LOCAL_REQUEST_SECONDS = 60
VOICE_PREVIEW_MAX_CJK = 40
VOICE_PREVIEW_MAX_EN_WORDS = 30
VOICE_PREVIEW_DEADLINE_SECONDS = 25

# 渲染
RENDER_CONCURRENCY = 1
RENDER_TIMEOUT_SECONDS = settings.classroom_render_timeout_seconds
RENDER_LAYOUT_REPAIR_ATTEMPTS = 1
RENDER_VIEWPORTS = ((1280, 720), (960, 540), (390, 0))  # 0 = 阅读模式重排

# 导出
EXPORT_TTL_HOURS = settings.classroom_export_ttl_hours
EXPORT_OWNER_MB = 200

# 结构上限（与 schema 同步；schema 是最终门）
MAX_PAGES = settings.classroom_max_pages
MAX_PUBLISHED_REVISIONS = settings.classroom_max_revisions
MAX_DURATION_MINUTES = 30

# 幂等与进度
IDEMPOTENCY_KEY_MIN = 16
IDEMPOTENCY_KEY_MAX = 128
IDEMPOTENCY_RETENTION_DAYS = 7
PROGRESS_DEDUP_EVENTS = 256
RUN_ANNOTATIONS_MAX = 100
RUN_ANNOTATION_CHARS = 4000

# 问答上下文
QA_CONTEXT_RECENT_ROUNDS = 6
QA_ADJACENT_SUMMARY_CHARS = 400

# 页数档位（duration → 目标页数）
PAGE_PLAN_RANGES = {
    5: (4, 6), 10: (6, 9), 15: (8, 12), 20: (10, 15), 30: (14, 20),
}

# 检查点密度（duration → 正式题上限；不出题优先）
CHECKPOINT_DENSITY = {
    5: (0, 1), 10: (1, 1), 15: (2, 2), 20: (2, 2), 30: (2, 3),
}

# 时长估算（§15.5 时长门）
SPEECH_RATE_ZH_CPM = (180, 220)   # 中文 字/分钟
SPEECH_RATE_EN_WPM = (120, 160)   # 英文 词/分钟
DURATION_TOLERANCE = 0.25         # 超过目标 ±25% 压缩/拆课

# 讲稿分段（§6.3）
SEGMENT_SPLIT_HARD_CAP = 240      # 朗读规范化后字符硬上限
AUTO_ADVANCE_DEFAULT_PAUSE_MS = 800

# 恢复
JOB_RECOVERY_MAX = 3
FAILED_STAGING_TTL_DAYS = 7
