"""弱模型伪工具标签护栏（2026-08-15「导数」对话回归，turn 6/8；
2026-08-31「角动量守恒」回归扩展到 XML 叙述格式）。

部分模型不发起 function-calling，而是在正文里"叙述"工具调用。已观测到
两种格式，都必须拦下：

- 假标签：``<knowledge_search>检索关键词：导数</knowledge_search>``；
- XML 叙述：``<tool_call><function=knowledge_search>
  <parameter=keywords>角动量守恒定律</parameter>…</tool_call>`` —— 其中
  ``<knowledge_search`` 子串根本不出现，只匹配旧格式时整段标记会作为
  正文流进聊天和 TTS（被原样朗读）。

本模块在流式输出时做缓冲检测：

- 任一标签开始形成（含半截前缀 ``<tool_c…``）就停止对外转发该段文本；
- 命中后由调用方（chat_agent / executor 的 ReAct 环路）执行**真实**的
  knowledge_search，把结果注入消息并继续环路，让模型基于真结果续写；
- 标签前的正文（"我先检索一下…"）照常流出，作为该步可见前导。

纯文本状态机，无 LLM 依赖；每步 LLM 调用新建一个实例。
"""
from __future__ import annotations

import re

# 已观测的伪工具标记开头；命中任何一个都视为"模型在叙述工具调用"。
# <function= 单独出现也绝无可能是正常教学内容。
_TAG_OPENS = (
    "<knowledge_search",
    "<tool_call",
    "<function=",
)
_MAX_TAG_RAW = 2000  # 标签原文累计上限（防异常超长输出吃内存）
_QUERY_LINE_RE = re.compile(r"(?:检索关键词|关键词|查询|query)\s*[:：]\s*([^\n<]{2,120})")
# XML 叙述格式的 keywords 参数（可能被 _MAX_TAG_RAW 截断而未闭合）。
_KEYWORDS_PARAM_RE = re.compile(
    r"<parameter=keywords>\s*([^<]{2,200}?)\s*(?:</parameter>|$)", re.S)


class PseudoToolGuard:
    """单次 LLM 流的伪工具标记检测器（假标签 + XML 叙述两种格式）。"""

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
                self._tag_raw += delta
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
        """从标签原文提取检索词；无显式关键词时回退 fallback（用户消息）。"""
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

    def _held_prefix_len(self, buf: str) -> int:
        """尾部是否存在任一标签的半截前缀（如 ``<tool_c``），返回持有长度。"""
        lower = buf.lower()
        window = min(len(buf), self._max_tag_len)
        for i in range(max(0, len(buf) - window), len(buf)):
            if buf[i] == "<" and any(t.startswith(lower[i:]) for t in self._tags):
                return len(buf) - i
        return 0
