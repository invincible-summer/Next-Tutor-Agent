"""Task Understanding: turn the student's natural-language message into a
structured learning task (intent / subject / concept / goal).

Three paths, tried in order (Hybrid Understanding):
  0. Pending-context match (D02) -- an explicit option pick ("B"/"选C") or a
     bare «继续» resolves against the session's pending exercise BEFORE the
     greeting short-circuit can swallow it.
  1. Rule short-circuit -- greetings/acks -> CHITCHAT (no LLM call, saves
     tokens + latency, preserves V1's "don't loop on '你好'" behavior).
  2. LLM structured output -- for substantive questions, a low-budget
     non-streaming call returns a JSON task object; the bounded session
     context (pending question / active concepts) rides along (D02). The
     response_mode field explicitly decides whether this is a new structured
     quiz request.
  3. Semantic guard + rule fallback -- if the LLM fails or contradicts a clear
     new-question request, a bounded phrase/verb/object scorer repairs the
     decision; existing-question markers and explicit no-practice wording win.

Every path returns a TaskUnderstanding; `source` records which path won so the
trace can explain the resulting plan.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..core.llm_async import AsyncLLMClient
from ..core.session import TutorSession
from .state import TaskType, TaskUnderstanding
from ..core.quiz_illustration_policy import explicit_illustration_request

# --- rule layer (mirrors + extends V1 chat_agent._classify_intent) ------------

# Exact greetings / acks -> direct chat, no tools, no LLM understanding call.
_GREETINGS = {
    "你好", "您好", "hi", "hello", "hey", "在吗", "在", "谢谢", "感谢",
    "好的", "ok", "嗯", "嗯嗯", "收到", "了解", "明白", "知道了",
    "行", "好", "666", "好的好的", "thx", "thanks", "bye", "再见",
}

# Keyword -> TaskType. First match wins (order matters: specific before general).
# These cover the V1 _TOOL_TRIGGERS plus a few diagnostic/review signals.
_KW_RULES: list[tuple[tuple[str, ...], TaskType]] = [
    (("出题", "练习", "测验", "测一测", "巩固", "考考", "做题", "刷题",
      "出几道"), TaskType.PRACTICE),
    (("错题", "分析错", "为什么总错", "为什么老错", "薄弱", "哪里不懂",
      "诊断", "为什么做错"), TaskType.DIAGNOSE),
    (("复习", "总结", "回顾", "梳理", "归纳"), TaskType.REVIEW),
    (("计划", "怎么学", "学习路线", "学习计划", "复习计划", "规划"), TaskType.PLAN),
    (("解一下", "这道题", "算一下", "求解", "怎么算", "解题", "算出"), TaskType.SOLVE),
    (("生成", "帮我写", "编一道", "设计一道", "教案"), TaskType.GENERATE),
]

# "explain" signals -- broadest. Used both as a fast-path and as the fallback.
_EXPLAIN_KW = ("讲一下", "讲讲", "讲解", "解释", "是什么", "什么是", "为什么",
               "怎么理解", "我不懂", "我不会", "没懂", "没理解", "没学会",
              "原理", "概念", "帮我讲")

_ONE_SENTENCE_RE = re.compile(r"一句话|一两句话|one\s+sentence", re.I)
_CONCISE_RE = re.compile(r"简短|简洁|简单说|只要结论|不要展开|不要详细", re.I)
_TABLE_RE = re.compile(r"表格|对比表", re.I)
_STEPS_RE = re.compile(r"分步骤|按步骤|逐步", re.I)
_NO_ASSESS_RE = re.compile(r"(?:不要|别|无需|不用|不必|不需要|不想|先不)(?:再)?(?:出题|测验|练习|做题)|只(?:要|需)讲解|只回答", re.I)
_ASSESS_RE = re.compile(r"出.{0,5}题|练习|测验|测一测|考考|检测题|诊断题|做题", re.I)

# A new-question request is a semantic combination, not a single keyword.
# The LLM understanding prompt is the primary decision-maker; this bounded
# scorer is the fail-safe used when the provider is unavailable or returns an
# invalid/contradictory intent. It intentionally distinguishes generating a
# fresh problem from asking how to solve an already-mentioned problem.
_NEW_QUESTION_PHRASES = (
    "给我一道", "给我一题", "给我个题", "给我一个题", "给我几道",
    "来一道", "来一题", "来个题", "来几个题", "出个题", "出个例题",
    "安排一道", "安排个题", "随机一道", "随机来题", "随便来道",
    "考我", "考我一下", "考考我", "测试我", "测试一下", "测测我", "测一下",
    "挑战我", "问我一道", "给个问题", "来个问题", "出一题", "出一道",
    "出几题", "出点题", "出些题", "给我几题",
    "让我做一道", "让我练一道", "想练练", "练一练", "练练看",
    "做点题", "做几道", "刷几道", "给点练习", "来点练习",
    # Space-free forms are included because the scorer normalizes whitespace.
    "give me a question", "givemeaquestion", "give me a problem", "givemeaproblem",
    "quiz me", "quizme", "test me", "testme",
    "make an exercise", "makeanexercise", "practice problem", "practiceproblem",
    "practice question", "practicequestion",
)
_QUESTION_NOUNS = (
    "题", "题目", "例题", "习题", "练习", "问题", "测验", "quiz",
    "exercise", "problem", "question",
)
_QUESTION_GENERATION_VERBS = (
    "出", "给", "来", "编", "设计", "生成", "提供", "安排", "准备",
    "考", "测试", "挑战", "练", "刷", "做",
)
_EXISTING_QUESTION_MARKERS = (
    "这道题", "这题", "上面这题", "刚才这题", "该题", "题目怎么做",
    "怎么解", "求解", "答案是什么", "为什么错", "解析一下", "如何做题",
    "怎么做题", "做题方法", "做题思路",
)


def new_question_request_score(message: str) -> int:
    """Score whether ``message`` asks for a fresh interactive question.

    This is a conservative semantic fallback, not the source of truth: it
    combines request phrases, an action verb + question noun, and explicit
    practice language, then subtracts strong existing-question/solve signals.
    The score is exposed for tests and trace diagnostics without retaining the
    raw message.
    """
    text = re.sub(r"\s+", "", str(message or "").strip().lower())
    if not text:
        return 0
    if _NO_ASSESS_RE.search(text):
        return -5
    score = 0
    if any(phrase in text for phrase in _NEW_QUESTION_PHRASES):
        score += 4
    has_noun = any(noun in text for noun in _QUESTION_NOUNS)
    has_generation_verb = any(verb in text for verb in _QUESTION_GENERATION_VERBS)
    if has_noun and has_generation_verb:
        score += 3
    # Practice verbs can omit the noun ("我想练练" / "考我一下").
    if any(phrase in text for phrase in ("练习", "练练", "练一练", "考我", "测试我",
                                         "测试一下", "测一下", "挑战我", "quizme",
                                         "testme", "practice")):
        score += 2
    if any(marker in text for marker in _EXISTING_QUESTION_MARKERS):
        score -= 4
    return score


def is_new_question_request(message: str) -> bool:
    """Return the lexical fallback decision for a fresh quiz request."""
    return new_question_request_score(message) >= 3


def is_existing_question_request(message: str) -> bool:
    """Whether the wording points at an already-present question."""
    text = re.sub(r"\s+", "", str(message or "").strip().lower())
    return bool(text) and any(marker in text for marker in _EXISTING_QUESTION_MARKERS)


def detect_response_constraints(msg: str) -> tuple[str, bool]:
    """Extract hard presentation constraints before adaptive teaching logic.

    A request such as “一句话解释 X” is not merely a style preference: it
    is a user-visible contract.  It suppresses the default closing assessment
    unless the same message explicitly asks for a quiz.
    """
    text = msg.strip()
    if _ONE_SENTENCE_RE.search(text):
        fmt = "one_sentence"
    elif _CONCISE_RE.search(text):
        fmt = "concise"
    elif _TABLE_RE.search(text):
        fmt = "table"
    elif _STEPS_RE.search(text):
        fmt = "steps"
    else:
        fmt = ""
    explicit_assessment = bool(_ASSESS_RE.search(text))
    allow_assessment = not bool(_NO_ASSESS_RE.search(text))
    if fmt in {"one_sentence", "concise"} and not explicit_assessment:
        allow_assessment = False
    return fmt, allow_assessment

# Regex patterns for spoken variants the substring keywords miss. Ordered
# most-specific first; first match wins. (re is imported at module top.)
_RULE_PATTERNS: list[tuple["re.Pattern[str]", TaskType]] = [
    (re.compile(r"出.{0,8}题"), TaskType.PRACTICE),       # 出5道...题 / 出两道题
    (re.compile(r"[做刷].{0,4}题"), TaskType.PRACTICE),   # 做题 / 刷几道题
    (re.compile(r"[总老].{0,4}错"), TaskType.DIAGNOSE),   # 总做错 / 老错
]

# Subject hints by keyword (best-effort, not authoritative).
_SUBJECT_KW: list[tuple[tuple[str, ...], str]] = [
   (("物理", "力", "运动", "电", "磁", "光", "热", "波", "能量", "功"), "物理"),
    (("牛顿", "定律", "惯性", "重力", "摩擦", "压强", "浮力", "密度"), "物理"),
    (("数学", "函数", "方程", "几何", "代数", "微积分", "导数", "积分",
      "三角", "向量", "概率", "统计", "数列"), "数学"),
    (("化学", "反应", "元素", "分子", "原子", "离子", "化学键", "有机"), "化学"),
    (("生物", "细胞", "基因", "遗传", "光合", "生态", "进化", "DNA"), "生物"),
    (("英语", "语法", "单词", "时态", "从句", "translation"), "英语"),
    (("语文", "文言文", "古诗", "阅读理解", "作文", "修辞"), "语文"),
    (("历史", "地理", "政治"), "文科综合"),
]


def _is_greeting(msg: str) -> bool:
    msg = msg.strip().lower()
    if is_new_question_request(msg):
        return False
    if msg in _GREETINGS:
        return True
    # very short (<5 chars) no punctuation -> treat as greeting/ack ONLY if it
    # carries no substantive signal (subject keyword or a teach/solve verb).
    # e.g. "讲惯性" is short but clearly an explain request, not a greeting.
    if len(msg) < 5 and not any(c in msg for c in "？！，。、,.?"):
        has_subject = any(any(kw in msg for kw in kws) for kws, _ in _SUBJECT_KW)
        starts_verb = any(msg.startswith(v) for v in
                          ("讲", "解", "算", "出", "为什么", "怎么", "帮"))
        if not has_subject and not starts_verb and not any(kw in msg for kw in _EXPLAIN_KW):
            return True
    return False


# --- D02: bounded session context (W3) ----------------------------------------
# A10 的根因：规则短路与 LLM 理解都只看当前消息，session 里「待答检测题、正在
# 教学的概念」完全不可见，"B"/「继续」这类短确认因此丢失指代。这里构造一个
# 只读、有界的上下文投影（供规则匹配与 LLM prompt 共用），不引入第二份状态。

# 学生想推进当前学习时最常用的短语（存在可续上下文时不落寒暄快道）。
_CONTINUE_PHRASES = {"继续", "继续吧", "接着来", "继续学", "继续练", "继续练习",
                     "下一题", "下一道", "next", "go on", "continue"}
# 选项点选的口语前缀（"选B"/"答案是b"/"我选 C"）。
_OPTION_PREFIXES = ("答案是", "我选", "我选的是", "选", "答案", "choose", "answer is")


def _session_context(session: TutorSession | None) -> dict[str, Any]:
    """Bounded read-only projection of pending learning context.

    Returns the latest quiz set's first unanswered question (type / stem
    prefix / option keys+values / knowledge point) plus the learning card's
    active concepts and recent verdicts. Everything is capped so the block
    stays cheap to embed; any malformed session shape degrades to empty.
    """
    ctx: dict[str, Any] = {
        "pending_q_type": "", "pending_stem": "", "pending_knowledge_point": "",
        "option_keys": [], "option_values": [],
        "active_concepts": [], "latest_verdicts": [],
    }
    if session is None:
        return ctx
    try:
        history = getattr(session, "quiz_history", None) or []
        for qset in reversed(history):
            if not isinstance(qset, dict):
                continue
            questions = qset.get("questions")
            if not isinstance(questions, list):
                continue
            unanswered = [q for q in questions
                          if isinstance(q, dict) and not q.get("result")]
            if not unanswered:
                continue
            q = unanswered[0]
            ctx["pending_q_type"] = str(q.get("type", ""))[:20]
            ctx["pending_stem"] = str(q.get("stem", ""))[:80]
            ctx["pending_knowledge_point"] = str(q.get("knowledge_point", ""))[:30]
            opts = q.get("options")
            if isinstance(opts, dict):
                for k, v in list(opts.items())[:8]:
                    ctx["option_keys"].append(str(k).strip())
                    ctx["option_values"].append(str(v).strip()[:40])
            break
        card = getattr(session, "context_card", None)
        if isinstance(card, dict):
            ctx["active_concepts"] = [str(c)[:30] for c in
                                      (card.get("active_concepts") or [])][:3]
            ctx["latest_verdicts"] = [str(v)[:10] for v in
                                      (card.get("latest_verdicts") or [])][:3]
    except Exception:
        pass
    return ctx


def _match_pending_option(msg: str, ctx: dict[str, Any]) -> TaskUnderstanding | None:
    """Deterministic structured handling of an explicit option pick (D02).

    "B" / "选C。" / "答案是b" / an exact option value all resolve to the pending
    question's knowledge point with goal=answer_pending. Grading itself stays
    on the quiz-card API -- chat never grades on the student's behalf.
    """
    keys = [k for k in ctx.get("option_keys", []) if k]
    if not keys:
        return None  # 选项点选只对带选项的待答题目成立
    text = msg.strip().strip("。．.!！?？，, ")
    for prefix in _OPTION_PREFIXES:
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    text = text.strip("。．.!！?？，, ：: ")
    if not text or len(text) > 12:
        return None
    upper = text.upper()
    if upper not in [k.upper() for k in keys]:
        if text not in [v for v in ctx.get("option_values", []) if v]:
            return None
    return TaskUnderstanding(
        intent=TaskType.PRACTICE,
        concept=str(ctx.get("pending_knowledge_point", "")),
        goal="answer_pending",
        requires_tools=False,
        confidence=0.9,
        source="rule_option",
        allow_followup_assessment=False,  # 本轮在答待答题，不再追新题
    )


def _match_pending_continue(msg: str, ctx: dict[str, Any]) -> TaskUnderstanding | None:
    """«继续/下一题» with continuable context -> keep the reference instead of
    dropping to chitchat. Pending question wins (continue = engage the
    exercise); otherwise continue teaching the active concept."""
    if msg.strip().lower() not in _CONTINUE_PHRASES:
        return None
    if ctx.get("pending_stem"):
        return TaskUnderstanding(
            intent=TaskType.PRACTICE,
            concept=str(ctx.get("pending_knowledge_point", "")),
            goal="answer_pending",
            requires_tools=False,
            confidence=0.85,
            source="rule_continue",
            allow_followup_assessment=False,
        )
    concepts = ctx.get("active_concepts") or []
    if concepts:
        return TaskUnderstanding(
            intent=TaskType.EXPLAIN,
            concept=concepts[0],
            goal="understand",
            requires_tools=False,
            confidence=0.7,
            source="rule_continue",
        )
    return None


def _context_block(ctx: dict[str, Any]) -> str:
    """Render the bounded context for the LLM understanding prompt (D02)."""
    parts: list[str] = []
    if ctx.get("pending_stem"):
        opts = " ".join(ctx.get("option_keys", [])[:8])
        parts.append(f"存在待答题目（{ctx.get('pending_q_type', '')}）："
                     f"{ctx['pending_stem']}"
                     + (f"（选项：{opts}）" if opts else "")
                     + (f"（知识点：{ctx['pending_knowledge_point']}）"
                        if ctx.get("pending_knowledge_point") else ""))
    if ctx.get("active_concepts"):
        parts.append("正在教学的概念：" + "、".join(ctx["active_concepts"]))
    if ctx.get("latest_verdicts"):
        parts.append("最近判定：" + "、".join(ctx["latest_verdicts"]))
    if not parts:
        return ""
    return "\n\n【会话上下文】" + "；".join(parts)


def _rule_classify(msg: str) -> TaskType:
    """Best-guess TaskType from keywords (no LLM). Falls back to EXPLAIN."""
    suppress_practice = bool(_NO_ASSESS_RE.search(msg))
    # Handle natural phrasing before the legacy keyword table. Examples:
    # 「给我来个题」「考我一下」「想练练函数」 contain no literal “出题” but
    # unambiguously request a new question.
    if not suppress_practice and is_new_question_request(msg):
        return TaskType.PRACTICE
    for kws, ttype in _KW_RULES:
        if suppress_practice and ttype == TaskType.PRACTICE:
            continue
        if any(kw in msg for kw in kws):
            return ttype
    # Looser path: regex patterns catch split-up spoken variants that exact
    # substrings miss (e.g. "出5道...题" -> PRACTICE, "总...做错" -> DIAGNOSE).
    for pat, ttype in _RULE_PATTERNS:
        if suppress_practice and ttype == TaskType.PRACTICE:
            continue
        if pat.search(msg):
            return ttype
    if any(kw in msg for kw in _EXPLAIN_KW):
        return TaskType.EXPLAIN
    return TaskType.EXPLAIN  # default teaching intent


def _guess_subject(msg: str) -> str:
    for kws, subj in _SUBJECT_KW:
        if any(kw in msg for kw in kws):
            return subj
    return ""


def _guess_concept(msg: str) -> str:
    """Best-effort concept extraction: strip leading politeness/verbs and
    punctuation, keep the short noun-ish remainder."""
    m = msg.strip()
    for lead in ("请用一句话", "用一句话", "请简短地", "简短地", "简短",
                 "我不会", "我不懂", "没懂", "没理解", "没学会",
                 "讲一下", "讲讲", "讲解一下", "讲解", "解释一下", "解释",
                 "什么是", "什么叫做", "请问", "帮我", "麻烦"):
        if m.startswith(lead):
            m = m[len(lead):]
    m = m.strip("，。、,.?？！！ \t\n")
    return m[:30]  # cap length; the planner/LLM will refine


def rule_understand(msg: str) -> TaskUnderstanding:
    """Pure rule path. CHITCHAT for greetings, else coarse-classify."""
    if _is_greeting(msg):
        return TaskUnderstanding(intent=TaskType.CHITCHAT, source="rule",
                                 requires_tools=False, confidence=1.0)
    ttype = _rule_classify(msg)
    response_format, allow_assessment = detect_response_constraints(msg)
    return TaskUnderstanding(
        intent=ttype,
        subject=_guess_subject(msg),
        concept=_guess_concept(msg),
        goal="practice" if ttype == TaskType.PRACTICE else "understand",
        requires_tools=ttype in (TaskType.PRACTICE, TaskType.DIAGNOSE),
        confidence=0.5,
        source="rule",
        illustration_request=explicit_illustration_request(msg),
        structured_quiz_request=(ttype == TaskType.PRACTICE),
        response_format=response_format,
        allow_followup_assessment=allow_assessment,
    )


# --- LLM layer ---------------------------------------------------------------

from ..prompts.registry import get as _prompt

# 阶段D：prompt 文本统一由注册表管理（含版本号），此处薄 re-export 兼容。
_UNDERSTAND_SYSTEM = _prompt("understand_system").text
# Keep the historical understand_system@1.3.0 stable for replayed traces and
# add a focused, versioned classifier contract to the same LLM call. This
# prevents fuzzy quiz language from being treated as ordinary explanation
# while avoiding an extra provider round trip on every turn.
_QUIZ_INTENT_SYSTEM = _prompt("quiz_intent_system").text


def _extract_json(text: str) -> dict[str, Any] | None:
    """Robustly pull a JSON object out of an LLM response."""
    if not text:
        return None
    text = text.strip()
    # strip ```json ... ``` fences if present
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        # last resort: find the first {...} block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _clean_search_queries(raw: Any) -> list[str]:
    """Normalize the LLM's search_queries into a bounded, safe variant list.

    Accepts only a JSON array of short strings; each entry is stripped,
    de-duplicated, capped at 3 items and 20 chars (the prompt contract).
    Anything else (missing / wrong type / junk) yields [] so pre-retrieval
    keeps its raw-message fallback instead of searching garbage.
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        term = str(item).strip()
        term = term.strip("，。、,.?？！！；;：: \"'`“”‘’\t\n")
        # A "query" longer than a locator term is a sentence -> drop, the
        # deterministic compact variant of the raw message already covers it.
        if not term or len(term) > 20:
            continue
        if term not in out:
            out.append(term)
        if len(out) >= 3:
            break
    return out


async def llm_understand(msg: str, llm: AsyncLLMClient,
                         ctx: dict[str, Any] | None = None) -> TaskUnderstanding | None:
    """LLM structured-output path. Returns None on any failure so the caller
    can fall back to rules (never raises into the turn).

    `ctx` is the bounded session context (D02): appended to the user message
    so short references ("这一步呢"/"继续") keep their referent."""
    user_content = msg
    if ctx is not None:
        block = _context_block(ctx)
        if block:
            user_content = msg + block
    try:
        content, usage = await llm.complete(
            [{"role": "system", "content": _UNDERSTAND_SYSTEM + "\n\n" + _QUIZ_INTENT_SYSTEM},
             {"role": "user", "content": user_content}],
            temperature=0.1,
            max_tokens=400,
            disable_thinking=True,  # JSON extraction: reasoning would starve the budget
        )
    except Exception:
        return None
    if not content:
        return None
    obj = _extract_json(content)
    if not obj:
        return None
    intent = TaskType.from_value(obj.get("intent"))
    if intent is None:
        return None  # junk intent -> let rules decide
    requires = obj.get("requires_tools")
    if not isinstance(requires, bool):
        # infer from intent if the model omitted/typed it wrong
        requires = intent in (TaskType.PRACTICE, TaskType.DIAGNOSE)
    goal = str(obj.get("goal", "") or "")
    response_mode = str(obj.get("response_mode", "") or "").strip().lower()
    structured_quiz = response_mode == "structured_quiz"
    # Backward-compatible model payloads often only return intent/goal. Treat
    # a practice intent or explicit practice goal as the same structured mode.
    structured_quiz = structured_quiz or intent == TaskType.PRACTICE \
        or str(obj.get("goal", "") or "").lower() in {
            "practice", "quiz", "test", "exercise", "new_question",
        }
    if structured_quiz and intent != TaskType.PRACTICE:
        intent = TaskType.PRACTICE
        requires = True
    if structured_quiz:
        # Keep the control-plane fields internally consistent even when a
        # provider returns response_mode without changing its coarse goal.
        goal = "practice"
    return TaskUnderstanding(
        intent=intent,
        subject=str(obj.get("subject", "") or ""),
        concept=str(obj.get("concept", "") or ""),
        goal=goal,
        difficulty=obj.get("difficulty") if isinstance(obj.get("difficulty"), str) else None,
        requires_tools=requires,
        response_format=str(obj.get("response_format", "") or ""),
        allow_followup_assessment=bool(obj.get("allow_followup_assessment", True)),
        illustration_request=obj.get("illustration_request") if isinstance(obj.get("illustration_request"), str) and obj.get("illustration_request") in {"auto", "none", "required"} else "auto",
        structured_quiz_request=structured_quiz,
        search_queries=_clean_search_queries(obj.get("search_queries")),
        confidence=0.8,
        source="llm",
    )


# --- public entry point ------------------------------------------------------

def _use_llm(understanding: "TaskUnderstanding | None") -> bool:
    """Whether to spend an LLM call. We only call the LLM when rules are
    uncertain: a high-confidence greeting/ack already settled it."""
    return understanding is None or (
        understanding.source == "rule" and understanding.intent != TaskType.CHITCHAT
    )


async def understand(msg: str, session: TutorSession, llm: AsyncLLMClient | None = None,
                     *, use_llm: bool | None = None) -> TaskUnderstanding:
    """Top-level task understanding.

    Order: pending-context match (D02) -> rule short-circuit -> (optional) LLM
    -> rule fallback. A CHITCHAT rule result is final unless continuable
    session context re-anchors it (no LLM). For substantive questions we try
    the LLM with the bounded context block; if it fails we keep the rule
    result with source='fallback'.

    `use_llm` overrides the env-driven default (None = read env var)."""
    import os
    if use_llm is None:
        use_llm = os.getenv("SUPERVISOR_LLM_UNDERSTAND", "1") not in ("0", "false", "False")

    ctx = _session_context(session)

    # D02: an explicit option pick or a bare «继续» resolves against the
    # pending exercise BEFORE the greeting short-circuit can swallow it.
    option_pick = _match_pending_option(msg, ctx)
    if option_pick is not None:
        return option_pick

    ruled = rule_understand(msg)
    if ruled.intent == TaskType.CHITCHAT:
        continued = _match_pending_continue(msg, ctx)
        if continued is not None:
            return continued
        return ruled  # greeting/ack: never spend an LLM call

    if not use_llm or llm is None:
        return ruled

    llm_result = await llm_understand(msg, llm, ctx)
    if llm_result is not None:
        # The LLM is authoritative for natural language intent, with the
        # bounded semantic scorer as a safety net. If the wording clearly asks
        # for a fresh exercise but the model answered “explain/solve”, force
        # the structured quiz path before planning; this is the exact failure
        # mode that used to leak plain-text questions into Chat.
        if (not llm_result.structured_quiz_request
                and is_new_question_request(msg)
                and not _NO_ASSESS_RE.search(msg)):
            llm_result.intent = TaskType.PRACTICE
            llm_result.goal = "practice"
            llm_result.requires_tools = True
            llm_result.structured_quiz_request = True
            llm_result.source = "llm_semantic_guard"
        # Conversely, do not let a generic model answer turn an existing-
        # question request ("这道题怎么做") into a newly generated card.
        # A fresh-generation phrase such as "再来一道类似题" wins via the
        # positive score and is intentionally exempt from this correction.
        if (llm_result.structured_quiz_request
                and is_existing_question_request(msg)
                and not is_new_question_request(msg)):
            llm_result.intent = TaskType.SOLVE
            llm_result.goal = "solve_problem"
            llm_result.requires_tools = False
            llm_result.structured_quiz_request = False
        # Deterministic lexical constraints override an LLM style guess. The
        # student should not lose “一句话/不要出题” because the classifier
        # chose a remediation mode.
        response_format, allow_assessment = detect_response_constraints(msg)
        if response_format:
            llm_result.response_format = response_format
        if not allow_assessment:
            llm_result.allow_followup_assessment = False
        explicit = explicit_illustration_request(msg)
        if explicit != "auto":
            llm_result.illustration_request = explicit
        return llm_result

    # LLM failed -> degrade to the rule result, tagged as fallback
    return TaskUnderstanding(
        intent=ruled.intent, subject=ruled.subject, concept=ruled.concept,
        goal=ruled.goal, requires_tools=ruled.requires_tools,
        response_format=ruled.response_format,
        allow_followup_assessment=ruled.allow_followup_assessment,
        illustration_request=ruled.illustration_request,
        structured_quiz_request=ruled.structured_quiz_request,
        confidence=0.4, source="fallback",
    )
