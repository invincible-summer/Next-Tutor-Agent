"""弱模型伪工具标签护栏。

历史回归覆盖：
- 2026-08-15「导数」：``<knowledge_search>`` 假标签；
- 2026-08-31「角动量守恒」：``<tool_call><function=...>`` XML 叙述；
- 2026-09-17 DeepSeek V4.1 Flash：Provider 未投影 tool_calls 时，内部
  ``<｜DSML｜ calls>`` 控制块落入 content（V4 为无空格 tool_calls）。

主路径由 ``core.llm_async`` 的 DSML parser 把控制文本归一化成结构化
``tool_calls``。本模块仍保留独立的 fail-closed 防线：即使下游自定义 LLM
adapter 绕过主 parser，任何已知工具控制标记也不得进入聊天、历史或 TTS。
"""
from __future__ import annotations

import re

# 已观测的伪工具标记开头；命中任何一个都视为"模型在叙述工具调用"。
# DSML 同时兼容官方 V4.1 单竖线特殊 token 展示与部分网关的双竖线展示。
_TAG_OPENS = (
    "<knowledge_search",
    "<tool_call",
    "<function=",
    "<invoke",
    "<｜dsml｜",
    "<｜｜dsml｜｜",
)
_MAX_TAG_RAW = 4000  # DSML 可含多个 invoke；仍保持严格内存上限。
_QUERY_LINE_RE = re.compile(r"(?:检索关键词|关键词|查询|query)\s*[:：]\s*([^\n<]{2,120})")
# XML 叙述格式的 keywords 参数（可能被 _MAX_TAG_RAW 截断而未闭合）。
_KEYWORDS_PARAM_RE = re.compile(
    r"<parameter=keywords>\s*([^<]{2,200}?)\s*(?:</parameter>|$)", re.S)
# DeepSeek V4/V4.1 DSML：空格由 \s* 同时覆盖 V4 ``｜parameter`` 与
# V4.1 ``｜ parameter``；单双全角竖线均兼容。
_DSML_TOKEN = r"(?:｜){1,2}DSML(?:｜){1,2}"
_DSML_QUERY_RE = re.compile(
    rf"<{_DSML_TOKEN}\s*parameter\b[^>]*name=\"(?:query|keywords)\"[^>]*>"
    rf"\s*(.*?)\s*</{_DSML_TOKEN}\s*parameter\s*>",
    re.I | re.S,
)
_DSML_INVOKE_RE = re.compile(
    rf"<{_DSML_TOKEN}\s*invoke\b[^>]*name=\"([^\"]+)\"", re.I)
_FUNCTION_RE = re.compile(r"<function=([^>\s]+)", re.I)
_INVOKE_RE = re.compile(r"<invoke\b[^>]*name=\"([^\"]+)\"", re.I)


class PseudoToolGuard:
    """单次 LLM 流的伪工具/协议标记检测器。"""

    def __init__(self, tags: tuple[str, ...] = _TAG_OPENS) -> None:
        self._tags = tuple(t.lower() for t in tags)
        self._max_tag_len = max(len(t) for t in self._tags)
        self._pending = ""   # 尾部尚未判定的文本（可能是半截标签前缀）
        self._tag_raw = ""   # 标签出现后累计的原文（用于提取检索词）
        self._emitted = ""   # 已判定安全、允许转发给前端的全部文本
        self._detected = False

    @property
    def detected(self) -> bool:
        return self._detected

    @property
    def emitted(self) -> str:
        """到目前为止已放行的文本（命中时即标签前导正文）。"""
        return self._emitted

    def feed(self, delta: str) -> str:
        """喂入一个 answer delta；返回本次可安全转发的前端文本。"""
        if self._detected:
            if len(self._tag_raw) < _MAX_TAG_RAW:
                self._tag_raw += delta[:_MAX_TAG_RAW - len(self._tag_raw)]
            return ""
        buf = self._pending + delta
        lower = buf.lower()
        idx = min((i for i in (lower.find(t) for t in self._tags) if i >= 0),
                  default=-1)
        if idx >= 0:
            self._detected = True
            self._tag_raw = buf[idx:idx + _MAX_TAG_RAW]
            out = buf[:idx]
            self._pending = ""
            self._emitted += out
            return out
        held = self._held_prefix_len(buf)
        if held:
            out, self._pending = buf[:len(buf) - held], buf[len(buf) - held:]
        else:
            out, self._pending = buf, ""
        self._emitted += out
        return out

    def flush(self) -> str:
        """流结束：冲掉未成形的半截前缀缓冲（之后不可能再成为标签）。"""
        if self._detected:
            self._pending = ""
            return ""
        out, self._pending = self._pending, ""
        self._emitted += out
        return out

    def extract_query(self, fallback: str) -> str:
        """从标签原文提取检索词；无显式关键词时回退 fallback。"""
        # DeepSeek DSML uses a normal named parameter; prefer it before the
        # historical XML/natural-language extractors.
        m = _DSML_QUERY_RE.search(self._tag_raw)
        if m and m.group(1).strip():
            return m.group(1).strip()[:120]
        m = _KEYWORDS_PARAM_RE.search(self._tag_raw)
        if m and m.group(1).strip():
            return m.group(1).strip()[:120]
        m = _QUERY_LINE_RE.search(self._tag_raw)
        if m and m.group(1).strip():
            return m.group(1).strip()
        text = re.sub(r"<[^>]*>", "", self._tag_raw).strip()
        if 2 <= len(text) <= 120 and not text.startswith("检索"):
            return text
        return (fallback or "").strip()[:120]

    def extract_tool_name(self) -> str:
        """Best-effort protocol name extraction for diagnostics/recovery."""
        m = _DSML_INVOKE_RE.search(self._tag_raw)
        if m:
            return m.group(1).strip()
        m = _FUNCTION_RE.search(self._tag_raw)
        if m:
            return m.group(1).strip()
        m = _INVOKE_RE.search(self._tag_raw)
        if m:
            return m.group(1).strip()
        if self._tag_raw.lower().startswith("<knowledge_search"):
            return "knowledge_search"
        return ""

    def _held_prefix_len(self, buf: str) -> int:
        """尾部是否存在任一标签的半截前缀（如 ``<tool_c``），返回持有长度。"""
        lower = buf.lower()
        window = min(len(buf), self._max_tag_len)
        for i in range(max(0, len(buf) - window), len(buf)):
            if buf[i] == "<" and any(t.startswith(lower[i:]) for t in self._tags):
                return len(buf) - i
        return 0
