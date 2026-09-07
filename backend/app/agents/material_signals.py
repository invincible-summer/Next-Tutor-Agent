"""Shared material-grounding trigger signals.

P9 触发统一：此前 preresearch / skill_runtime.decision / chat_agent 三处各持
一套互不一致的关键词表，同一句话在三条路径上的触发判定可以互相矛盾
（2026-08-25 取证：「《湖畔夜色讲》」7 字过不了 ≥8 字符门槛，skill 决策又
剪掉检索技能 → 完全不触发，而教材就在工作区里）。此模块是唯一词源。
"""
from __future__ import annotations

import re

CONTENT_QUERY_RE = re.compile(
    r"(什么|为何|为什么|如何|怎么|原理|定义|概念|公式|变换|解释|讲解|区别|"
    r"关系|意义|推导|计算|总结|介绍|证明|条件|特点|过程|影响|"
    r"what|why|how|define|definition|principle|formula|explain)", re.IGNORECASE)

NON_CONTENT_RE = re.compile(
    r"^(你好|您好|嗨|谢谢|收到|继续|好的|好|嗯|取消|删除|打开|关闭|"
    r"返回|重试|再见|hello|hi|thanks|ok|continue)$", re.IGNORECASE)

FILE_REF_RE = re.compile(
    r"(文件|资料|文档|课件|教材|讲义|这份|该份|这篇|这本|这份文件|"
    r"pdf|docx|doc|pptx|ppt|txt|md|markdown|"
    r"上传|附件|报告|综述|开题|论文|作业)", re.IGNORECASE)

# 书名号内容 ≥2 字：篇目/课文名几乎总是教材检索意图（「《湖畔夜色讲》」只有
# 7 个字符，靠它过不了任何长度门槛）。
TITLE_MARK_RE = re.compile(r"《[^》《\s]{2,20}》")


def mentions_title(user_message: str) -> bool:
    return bool(TITLE_MARK_RE.search(user_message or ""))
