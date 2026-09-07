"""Image text extraction: local tesseract by default, multimodal if configured.

Default path: tesseract OCR (chi_sim+eng) — no API call needed, works offline.
Override path: if MULTIMODAL_API_KEY is set in .env, images go through a
vision model channel (user chooses which model in MULTIMODAL_MODEL).
No automatic model switching — the user opts in via env config.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import random
import re
import shutil
from dataclasses import dataclass

from PIL import Image

from .config import settings

logger = logging.getLogger(__name__)

_VISION_PROMPT = (
    "请仔细观察这张图片，它可能是一道物理/数学/化学等学科的题目照片。"
    "请提取并完整描述题目内容，包括：\n"
    "1. 题干文字（完整、准确，保留原有标点）\n"
    "2. 图中所示的物理模型/数学图形/化学结构等信息（如斜面倾角、滑轮连接方式、"
    "电路连接、受力方向、坐标系等，用文字描述清楚）\n"
    "3. 已知条件和数据\n"
    "4. 要求求解的问题\n"
    "如果有选项，一并列出。所有数学公式用LaTeX语法（$...$行内）。"
    "只输出题目内容，不要解答。"
)

# 通用整页 OCR prompt v2（扫描教材/讲义逐页转录）。相比 v1 新增：
#   - 印刷页码标记 [页码=N]（教材自己标注的页码；无则省略）——检索证据
#     双轨页码（教材页码优先，PDF 物理页兜底）的事实源；
#   - 图表结构化标记 [图|...] + 图述、[表|...]——扫描页无法在文本层区分
#     图文区域，视觉模型即版面分析器：插图输出内容描述，表格保留 | 结构。
_PAGE_OCR_PROMPT = (
    "请把这张文档页面图片完整转录为结构化纯文本。要求：\n"
    "1. 页面若有自己印刷的页码（页眉/页脚处的页码数字），第一行输出 [页码=N]"
    "（N 为印刷页码数字；没有印刷页码就省略这一行）。\n"
    "2. 按阅读顺序完整、准确转录正文，保留段落结构与原有标点；不要省略、不要总结。\n"
    "3. 数学公式用 LaTeX（行内 $...$，独立 $$...$$）。\n"
    "4. 表格：先输出一行 [表|表题或所在小节]（没有表题可写 [表|表格]），"
    "随后逐行转录，用 | 分隔各列，保持行列对应。\n"
    "5. 插图/示意图/照片/坐标图：先输出一行 [图|图的编号或图注]"
    "（如 [图3-2|斜面上物块受力示意]；没有编号图注就用一句话概括内容），"
    "随后新起一行以「图述：」开头，用 2-4 句话客观描述图中信息"
    "（对象、方向、几何关系、数值趋势、连接结构等），不要解答。"
    "纯装饰性小图标可合并为一行 [图|装饰] 略过。\n"
    "6. 页眉页脚、水印可省略；只输出页面正文内容，不要加任何解释或前后缀。"
)

# 单张裁剪插图的内容描述（原生 PDF 图表收割复用同一多模态通道）。
_FIGURE_DESC_PROMPT = (
    "这是从教材 PDF 中裁剪出的一张插图/图表区域。请以「图述：」开头，"
    "用 2-4 句话客观描述该图：类型（示意图/照片/坐标图/结构图/装置图等）、"
    "主要对象与文字标注、方向/几何关系/数值趋势等关键信息。"
    "数学公式用 LaTeX（$...$）。只输出描述本身，不要解答、不要延伸。"
)

# 用户上传文档（docx/pptx/pdf/md）内嵌图片的通用分型描述：题目照片完整转录
# 题干，插图/图表客观图述，装饰图标记略过——与教材图表提取同一智能水平。
_EMBEDDED_DESC_PROMPT = (
    "这是从用户上传的文档（讲义/笔记/试卷等）中提取的一张内嵌图片。请先判断图片类型：\n"
    "- 若是题目/练习/试卷照片：先输出一行「题目转录」，随后完整转录题干（含选项），"
    "公式用 LaTeX（$...$），不要解答。\n"
    "- 若是插图/示意图/照片/图表：先输出一行「图述」，随后用 2-4 句话客观描述图中内容"
    "（对象、方向、几何关系、数值趋势、连接结构等），不要解答。\n"
    "- 若是纯装饰性图标/分隔线/水印：只输出「装饰」两个字。\n"
    "只输出上述对应内容，不要加任何解释或前后缀。"
)


def is_image_file(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"))


def _tesseract_ocr(image_bytes: bytes, *, psm: int = 6) -> str:
    """Local tesseract OCR (chi_sim+eng). No API call, works offline.

    ``psm``: 6=均匀文本块（题目照片，默认）；3=自动分页（整页文档 OCR 用）。
    """
    if not shutil.which("tesseract"):
        return ""
    try:
        import pytesseract
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != "L":
            img = img.convert("L")
        return pytesseract.image_to_string(img, lang="chi_sim+eng", config=f"--psm {psm}").strip()
    except Exception:
        return ""


def _has_multimodal() -> bool:
    return bool(settings.multimodal_api_key)


# 模块级客户端缓存：逐页 OCR 每页新建 AsyncOpenAI+httpx 意味着整本书数百次
# TCP/TLS 握手（实测浪费）。按 (base_url, model) 缓存复用 keep-alive 连接；
# 记录创建时的 running loop——测试里多个 asyncio.run 交替时重建，避免跨 loop
# 复用报错。
_client_cache: dict[tuple[str, str], tuple[object, object]] = {}
_textbook_client_cache: dict[tuple[str, str, int], tuple[object, object]] = {}


@dataclass(frozen=True)
class TextbookOCRResult:
    """One exact-count textbook multimodal API attempt (never local fallback)."""
    success: bool
    text: str = ""
    error_code: str = ""
    error_summary: str = ""
    retryable: bool = False
    http_status: int | None = None
    attempt: int = 1


def _get_client(base_url: str, model: str):
    from openai import AsyncOpenAI
    import httpx

    key = (base_url, model)
    try:
        loop: object = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    cached = _client_cache.get(key)
    if cached is not None and cached[1] is loop:
        return cached[0]
    client = AsyncOpenAI(
        api_key=settings.multimodal_api_key,
        base_url=base_url,
        timeout=60.0,
        max_retries=2,
        http_client=httpx.AsyncClient(trust_env=False, timeout=60.0),
    )
    _client_cache[key] = (client, loop)
    return client


def _get_textbook_client(base_url: str, model: str, timeout_seconds: int):
    """Textbook-only client with SDK retries disabled for exact admin counts."""
    from openai import AsyncOpenAI
    import httpx

    timeout = max(10, min(300, int(timeout_seconds)))
    key = (base_url, model, timeout)
    try:
        loop: object = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    cached = _textbook_client_cache.get(key)
    if cached is not None and cached[1] is loop:
        return cached[0]
    client = AsyncOpenAI(
        api_key=settings.multimodal_api_key,
        base_url=base_url,
        timeout=float(timeout),
        max_retries=0,
        http_client=httpx.AsyncClient(trust_env=False, timeout=float(timeout)),
    )
    _textbook_client_cache[key] = (client, loop)
    return client


def _classify_textbook_error(exc: Exception) -> tuple[str, bool, int | None, str]:
    status = getattr(exc, "status_code", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    name = exc.__class__.__name__.lower()
    message = str(exc).strip().replace("\n", " ")[:240]
    if status in (401, 403):
        return ("authentication_error" if status == 401 else "permission_error",
                False, status, message or "多模态 API 鉴权失败")
    if status == 404:
        return "model_or_endpoint_not_found", False, status, message or "模型或端点不存在"
    if status == 400:
        return "bad_request", False, status, message or "多模态 API 请求无效"
    if status in (408, 409, 425, 429) or (status is not None and status >= 500):
        return "provider_retryable", True, status, message or "多模态 API 暂时不可用"
    if "timeout" in name or "timeout" in message.lower():
        return "timeout", True, status, message or "多模态 API 超时"
    if "connection" in name or "connect" in message.lower():
        return "connection_error", True, status, message or "多模态 API 连接失败"
    return "provider_error", True, status, message or "多模态 API 调用失败"


async def textbook_ocr_page_api(image_bytes: bytes, *, attempt: int = 1,
                                 timeout_seconds: int = 60) -> TextbookOCRResult:
    """Textbook page OCR through the configured multimodal API only.

    This deliberately never calls tesseract.  Retry/fallback policy belongs to
    the durable textbook scheduler, not this one-attempt transport function.
    """
    if not settings.multimodal_api_key:
        return TextbookOCRResult(
            False, error_code="multimodal_not_configured",
            error_summary="未配置教材多模态 OCR API", retryable=False,
            attempt=max(1, int(attempt)))
    try:
        base_url = settings.multimodal_base_url or settings.llm_base_url
        model = settings.multimodal_model or "glm-4.6v"
        client = _get_textbook_client(base_url, model, timeout_seconds)
        img = Image.open(io.BytesIO(image_bytes))
        if img.width > 2000:
            ratio = 2000 / img.width
            img = img.resize((2000, int(img.height * ratio)))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        text = await _vision_once(client, model, _PAGE_OCR_PROMPT, b64)
        if text.strip():
            return TextbookOCRResult(True, text=text.strip(), attempt=max(1, int(attempt)))
        return TextbookOCRResult(
            False, error_code="empty_content", error_summary="多模态 OCR 返回空内容",
            retryable=True, attempt=max(1, int(attempt)))
    except Exception as exc:
        code, retryable, status, summary = _classify_textbook_error(exc)
        logger.warning("Textbook multimodal OCR failed (attempt %s, code=%s, status=%s)",
                       attempt, code, status)
        return TextbookOCRResult(
            False, error_code=code, error_summary=summary,
            retryable=retryable, http_status=status, attempt=max(1, int(attempt)))


async def _vision_once(client, model: str, prompt: str, b64: str) -> str:
    """单次 vision 调用（含 400 extra_body 降级重试一次）。返回文本（可能空）。"""
    kwargs: dict = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 4000,
        "temperature": 0.1,
    }
    if settings.multimodal_disable_thinking:
        # 转录是提取而非推理：关闭 thinking + 请求最低思考强度（两种写法
        # 覆盖 deepseek/qwen 系与 OpenAI 系兼容端点），与主 LLM 客户端
        # complete(disable_thinking=True) 同一意图。
        kwargs["extra_body"] = {"thinking": {"type": "disabled"},
                                "reasoning_effort": "low"}
    try:
        response = await client.chat.completions.create(**kwargs)
    except Exception as e:
        # Provider-portable fallback（照 llm_async 既有契约）：网关拒绝
        # 可选推理控制字段时，去掉后重试一次，而不是整页失败。
        if getattr(e, "status_code", None) == 400 and "extra_body" in kwargs:
            kwargs.pop("extra_body", None)
            response = await client.chat.completions.create(**kwargs)
        else:
            raise
    msg = response.choices[0].message
    text = (msg.content or "").strip()
    if not text:
        text = (getattr(msg, "reasoning_content", None) or "").strip()
    return text


async def _multimodal_understand(image_bytes: bytes, *, prompt: str = _VISION_PROMPT,
                                 fallback_psm: int = 6) -> str:
    """Send image to a vision model via the configured multimodal channel.

    ``prompt`` 默认题目照片专用；整页文档 OCR 传 ``_PAGE_OCR_PROMPT``。
    异常与空 content 都按指数退避重试（``settings.multimodal_ocr_retries``
    次）——瞬时 429/5xx/截断不再丢页；耗尽才回退 tesseract（``fallback_psm``
    区分题目照片 6 / 整页文档 3）。
    """
    try:
        base_url = settings.multimodal_base_url or settings.llm_base_url
        model = settings.multimodal_model or "glm-4.6v"
        client = _get_client(base_url, model)

        img = Image.open(io.BytesIO(image_bytes))
        if img.width > 2000:
            ratio = 2000 / img.width
            img = img.resize((2000, int(img.height * ratio)))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        attempts = max(1, settings.multimodal_ocr_retries)
        for attempt in range(1, attempts + 1):
            try:
                text = await _vision_once(client, model, prompt, b64)
                if text:
                    return text
                logger.warning("Multimodal vision empty content (attempt %d/%d)",
                               attempt, attempts)
            except Exception as e:
                logger.warning("Multimodal vision failed (attempt %d/%d): %s",
                               attempt, attempts, e)
            if attempt < attempts:
                await asyncio.sleep(2 * attempt + random.uniform(0, 1))
    except Exception as e:
        logger.warning(f"Multimodal vision setup failed: {e}")
    logger.warning("Multimodal vision exhausted retries, falling back to tesseract")
    return _tesseract_ocr(image_bytes, psm=fallback_psm)


async def understand_image(image_bytes: bytes, filename: str = "image.png") -> str:
    """Extract text from an image.

    If MULTIMODAL_API_KEY is configured, use the vision model channel.
    Otherwise, use local tesseract OCR (chi_sim+eng).
    """
    if _has_multimodal():
        return await _multimodal_understand(image_bytes)
    return _tesseract_ocr(image_bytes)


async def ocr_page_image(image_bytes: bytes) -> str:
    """OCR a full document page (扫描教材/讲义逐页转录).

    双通道同 understand_image：视觉模型优先（用通用整页 _PAGE_OCR_PROMPT）/
    本地 tesseract 回退（--psm 3 自动分页，区别于题目专用的 --psm 6）。供
    pdf_ocr.ocr_pdf_pages 逐页调用。视觉模型故障自动回退 tesseract。
    """
    if _has_multimodal():
        return await _multimodal_understand(image_bytes, prompt=_PAGE_OCR_PROMPT,
                                            fallback_psm=3)
    return _tesseract_ocr(image_bytes, psm=3)


async def describe_figure_image(image_bytes: bytes) -> str:
    """单张裁剪插图 → 内容描述（图述）。双通道同 understand_image；
    多模态未配置/失败时回退 tesseract（至少保留图内文字标注），全空返回 ""。"""
    if _has_multimodal():
        return await _multimodal_understand(image_bytes, prompt=_FIGURE_DESC_PROMPT,
                                            fallback_psm=6)
    return _tesseract_ocr(image_bytes, psm=6)


def is_decoration_description(text: str) -> bool:
    """内嵌图描述是否为装饰标记（「装饰」/「[图|装饰]」等变体）。"""
    return re.fullmatch(r"\s*[\[［【]?\s*(?:图\s*[|｜]?\s*)?装饰\s*[\]］】]?\s*",
                        text or "") is not None


async def describe_embedded_image(image_bytes: bytes) -> str:
    """用户上传文档的内嵌图 → 分型描述（题目转录 / 图述 / 装饰）。

    双通道同 understand_image；tesseract 回退只保留图内文字（无分型结构），
    结果为空返回 ""（调用方丢弃该图，宁缺毋滥）。
    """
    if _has_multimodal():
        return await _multimodal_understand(image_bytes, prompt=_EMBEDDED_DESC_PROMPT,
                                            fallback_psm=6)
    return _tesseract_ocr(image_bytes, psm=6)
