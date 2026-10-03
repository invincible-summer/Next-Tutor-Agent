"""意图解析（A09）。

精确别名与固定功能问法走零 LLM 快路；模糊问法由调用方注入的一次
低预算结构化解析（llm_call）完成，输出仍然落在闭集内。任何失败都
回落确定性意图，绝不因为模型失败拒绝回答。

意图闭集（§10.2-4）：guide / navigate / search / learning_report /
teaching_report / planning_advice / prepare_action / general_chat /
clarify。时间窗口解析按 §6.4：本地日历边界、半开区间、夏令时安全。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from app.schemas.assistant import AssistantRouteId

from . import catalog

INTENT_KINDS = (
    "guide", "navigate", "search", "learning_report", "teaching_report",
    "planning_advice", "prepare_action", "general_chat", "clarify",
)

WindowKind = str  # "recent" | "this_week" | "last_week" | "custom" | ""

MAX_CUSTOM_DAYS = 90

_NAVIGATE_VERBS = re.compile(
    r"带[我咱]?去|打开|跳转|切?换?到|前往|转到|进入|导航|回到|回去|回一次|返回|"
    r"go\s+to|open|navigate|take\s+me\s+to|show\s+me|jump\s+to", re.IGNORECASE)
_QUESTION_MARKERS = re.compile(
    r"[?？]|怎么|如何|什么|哪些|哪一|哪里|在哪|吗\b|呢\b|能不能|会不会|"
    r"how|what|where|which|can\s+i|does\s+it", re.IGNORECASE)
_CLARIFY_ONLY = re.compile(
    r"^\s*(第[一二两三四五个](个|章|课|条|部分)?|另一?个|它|这(?:个|个吧)|那个|"
    r"继续|就(?:这|那)个|同上|yes|ok|okay|好的|可以)\s*[。.!！]?\s*$")
_TEACHING_RE = re.compile(
    r"教学效果|教学评价|教学报告|教得怎么样|教得如何|讲得.{0,4}长|"
    r"AI\s*老师.{0,12}(讲|教|答)|教学洞察|teaching\s+effect|"
    r"how.{0,20}(tutor|teacher)", re.IGNORECASE)
_LEARNING_RE = re.compile(
    r"学习近况|学得怎么样|学得如何|学习情况|学习总结|学习报告|学习表现|"
    r"学习效果(?!评价)|答题情况|作答情况|做(了|了多少)|最近.{0,6}(学|练|做)|"
    r"(这|本|上)周.{0,8}(学|答|练)|learning\s+report|my\s+progress|"
    r"how\s+am\s+i\s+doing|recent\s+learning", re.IGNORECASE)
_PLANNING_RE = re.compile(
    r"学习计划|今天(学|做)什么|接下来.{0,6}(学|做|安排)|任务安排|待办|"
    r"今日任务|学习安排|有什么任务|计划的|study\s+plan|my\s+tasks|"
    r"what\s+should\s+i\s+(learn|study|do)", re.IGNORECASE)
_PREPARE_RE = re.compile(
    r"帮我(备|讲|出|写|生成)|备一节课|生成(一节|一门)?(课|课程|课件|笔记|"
    r"题)|创建(一节|一门)?(课|课程)|来一节课|做一?个?(课件|PPT|ppt)|"
    r"继续(上次|上次的|接着)?(课|上课|课程|课件)|接着上|"
    r"prepare\s+a\s+lesson|make\s+me\s+a?\s*(quiz|lesson|note)", re.IGNORECASE)
# 「编辑/修改课件」：定位唯一课程后打开编辑视图（§2.1/§8.3 lesson view=edit）。
# 查问语气（怎么编辑）不触发，仍走 guide。
_LESSON_EDIT_RE = re.compile(
    r"(编辑|修改|改一下|调整|润色|优化)"
    r".{0,12}(课件|课程|这节课|这堂课|讲稿|PPT|ppt|幻灯片)|"
    r"(课件|这节课|这堂课|讲稿).{0,6}(怎么|如何)?(编辑|修改|改)|"
    r"edit\s+(the\s+)?(lesson|course|slides|deck)", re.IGNORECASE)
# 领域写请求（§21）：明确的创建/修改/完成指令 → prepare_action；具体
# operation 与参数由 policy 从文字确定性解析，模型不直接产出动作。
# 疑问语气（怎么/如何/什么）不触发；「好吗/吗」等请求后缀仍通过。
_DOMAIN_WRITE_RE = re.compile(
    r"(?:创建|添加|新建|加|记)[^，,。；;？?]{0,16}?任务|"
    r"(完成|做完|搞定了|标记完成)(了|好)?(这|这个|今天|今天的|当日)?任务|"
    r"(对话|会话|这个对话|当前对话)(重新)?(命名|改名|改名为|改叫|重命名为)|"
    r"每(天|日).{0,8}(时间|时长|预算|分钟).{0,4}(改|设|调)|"
    r"(记|写|保存|整理成?)(一?个?|一?条?|一?份?)(一条)?笔记|"
    r"(?:创建|新建|建)(一?个)?(学习|辅导)?(工作区|学习区|专区)|"
    r"(?:创建|新建|建)(一?个)?(?:资料)?文件夹|"
    r"(归档|删除|删掉|移入归档)(掉|了)?(这个|当前|此)(对话|会话|聊天记录)|"
    r"(这个|当前|此)(对话|会话)(移动|移|挪|转移)到|"
    r"(?:这篇|当前|此)(篇)?笔记(的)?(末尾|后面|最后|结尾)?"
    r"(追加|添加|加上|补充)|"
    r"(开启|打开|启用|关闭|取消|停止)(这篇|当前|此)?笔记的?(间隔)?复习|"
    r"(?:创建|新建|添加|设定|制定|建立)(一?个)?(学习)?目标|"
    r"(重新生成|重新规划|重排|重新安排)(我的)?(学习|复习)?计划|"
    r"任务[^，,。；;？?]{0,24}(改到|移到|挪到|移至|改至|调整到|换到|"
    r"改名为?|重命名为?|改名成|改成)|"
    r"(以后|回答|回复|答案)[^，,。；;？?]{0,6}(简短|简洁|详细)|"
    r"(记忆|跨会话记忆)(窗口)?(改成?|调到?|设为?|设置为?|改为?)|"
    r"(切换|改成|换成|打开|启用|用|恢复)(深色|浅色)(模式|主题)|"
    r"create\s+(a\s+)?(task|note|workspace|folder|goal)|rename\s+",
    re.IGNORECASE)
_WRITE_QUESTION_RE = re.compile(
    r"[?？]|怎么|如何|什么|哪些|哪里|在哪|能不能|会不会", re.IGNORECASE)
# §23.1/C03 跨模块工作流请求（单一事实源；policy._propose_start_workflow
# 引用同一组模式映射模板）。保守匹配完整多步目标，避免劫持单步写请求；
# 「继续…课」由 resume_lesson 交接路径处理，不在此列。
WORKFLOW_TEMPLATE_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("setup_learning_space", re.compile(
        r"(教材|资料|文件).{0,24}(整理|建立|搭建|变成|转化|配置)"
        r".{0,16}(学习区|学习环境|学习空间)|"
        r"(整理|建立|搭建|用).{0,24}(教材|资料|文件)"
        r".{0,16}(学习区|学习环境|学习空间)")),
    ("weekly_review_to_plan", re.compile(
        r"(总结|回顾|复盘).{0,10}本周.{0,24}(安排|计划|规划|下一步)")),
    ("weak_point_to_practice", re.compile(
        r"(针对|根据).{0,16}(薄弱|待解决|没掌握|错题|弱点).{0,20}"
        r"(练习|测评|练一练|安排练习)")),
    ("material_to_course", re.compile(
        r"(教材|资料).{0,20}(做成|生成|变成|备成|编成).{0,10}"
        r"(一节课|课件|课程)")),
    ("organize_materials", re.compile(
        r"整理.{0,20}(文件|资料).{0,30}(笔记|摘要|总结)")),
    ("continue_learning_session", re.compile(
        r"继续上次.{0,6}学习")),
)
_WORKFLOW_RE = re.compile("|".join(
    f"(?:{p.pattern})" for _n, p in WORKFLOW_TEMPLATE_PATTERNS))
# §25.1 订阅请求（C04）：明确「订阅/提醒我」意图 + 种类词 → prepare_action。
_SUBSCRIPTION_KIND_RE = re.compile(
    r"学习简报|周报|每周简报|每日任务|每天任务|当天任务提醒|"
    r"笔记复习|复习提醒|到期复习|未完成.{0,6}课程|课程提醒|继续上课提醒")
_SUBSCRIPTION_INTENT_RE = re.compile(
    r"(订阅|开通|开启|提醒我|给我).{0,24}|"
    r"(每周|每天|每日|周报|简报).{0,16}(订阅|提醒)")
_SEARCH_RE = re.compile(
    r"(找|查|搜索|查找|搜一下).{0,10}(工作区|课程|课|笔记|题|教材|记录|文件|资料|讲义|课件)|"
    r"我(的)?(工作区|课程|笔记|教材|错题|文件|资料|讲义|课件).{0,4}(在哪|哪里)|"
    r"find\s+my|where\s+(is|are)\s+my|search\s+my", re.IGNORECASE)
_GREETING_RE = re.compile(
    r"^\s*(你好|您好|hi|hello|嘿|哈喽|谢谢|感谢|多谢|thanks|thank\s+you|"
    r"好的|嗯|哦|ok|okay|再见|bye)\s*[。.!！~]?\s*$", re.IGNORECASE)
_SITE_TOUR_RE = re.compile(
    r"(网站|这里|这个站|系统|平台).{0,10}(能|可以).{0,6}(做|帮|用)|"
    r"功能介绍|有哪些功能|怎么用|使用(方法|指南)|是什么|what\s+can|"
    r"help\s+me|how\s+does\s+this\s+work", re.IGNORECASE)

_WINDOW_LAST_WEEK = re.compile(r"上周|上星期|last\s+week", re.IGNORECASE)
_WINDOW_THIS_WEEK = re.compile(r"本周|这周|这星期|本星期|this\s+week",
                               re.IGNORECASE)
_WINDOW_RECENT = re.compile(r"最近|近期|近来|recent(ly)?|lately", re.IGNORECASE)
_WINDOW_DAYS = re.compile(r"近(\d{1,3})天|最近(\d{1,3})天|(\d{1,3})\s*天(以|之)内|"
                          r"last\s+(\d{1,3})\s*days|past\s+(\d{1,3})\s*days",
                          re.IGNORECASE)


@dataclass
class ParsedIntent:
    kind: str
    module_route: AssistantRouteId | None = None
    candidates: list["catalog.CatalogModule"] = field(default_factory=list)
    workspace_name: str | None = None
    window: WindowKind = ""
    custom_days: int = 0
    confidence: str = "fallback"   # exact | strong | llm | fallback
    text: str = ""                 # 原始输入（工具查询用）
    source_id: str | None = None   # 追问来源时引用的本轮 source_id
    # 「编辑课件」意图：resolve_destination 优先解析课程并带 view=edit。
    lesson_edit: bool = False
    # 用户文字里提到的目标页码（1..5000，与资料预览上限一致）；
    # 仅 file 类目标消费（§20.3 page 字段），无则 None。
    page: int | None = None

    @property
    def primary_module(self) -> "catalog.CatalogModule | None":
        if self.module_route is not None:
            return catalog.get_module(self.module_route)
        return self.candidates[0] if self.candidates else None


def detect_window(text: str) -> tuple[WindowKind, int]:
    """从文字里提取时间窗口表达；与意图类别无关。"""
    m = _WINDOW_DAYS.search(text)
    if m:
        for group in m.groups():
            if group:
                return "custom", max(1, min(int(group), MAX_CUSTOM_DAYS))
    if _WINDOW_LAST_WEEK.search(text):
        return "last_week", 0
    if _WINDOW_THIS_WEEK.search(text):
        return "this_week", 0
    if _WINDOW_RECENT.search(text):
        return "recent", 0
    return "", 0


# 目标页码提取（§20.3 file 目标 page 字段）：与意图类别无关，页码来自
# 用户原文的确定性解析，模型不参与（不把页码当章节 ID，见 §17 示例）。
_PAGE_CN_DIGITS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
                   "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
MAX_TARGET_PAGE = 5000  # 与资料页预览渲染上限一致。
_PAGE_RE = re.compile(
    r"第\s*([0-9]{1,4}|[一二两三四五六七八九十百零]{1,8})\s*[页頁]"
    r"|(?:^|[\s,，。;；])page\s*([0-9]{1,4})", re.IGNORECASE)


def _cn_to_int(text: str) -> int | None:
    """简易中文数字（≤ 三位数：二十三 / 一百零三）换算；解析不了返回 None。"""
    total, num = 0, 0
    for ch in text:
        if ch in ("十", "百"):
            total += (num or 1) * (10 if ch == "十" else 100)
            num = 0
            continue
        digit = _PAGE_CN_DIGITS.get(ch)
        if digit is None:
            return None
        num = num * 10 + digit
    return total + num


def extract_page(text: str) -> int | None:
    """提取目标页码（1..5000）；无页码表达或越界返回 None。"""
    m = _PAGE_RE.search(str(text or ""))
    if not m:
        return None
    raw = (m.group(1) or m.group(2) or "").strip()
    if not raw:
        return None
    value = int(raw) if raw.isdigit() else _cn_to_int(raw)
    if value is None or not 1 <= value <= MAX_TARGET_PAGE:
        return None
    return value


def _module_intent(text: str, lang: str) -> tuple[str, list] | None:
    """目录强命中（score>=50）配合动词/疑问词区分 navigate / guide。"""
    matches = catalog.find_module_matches(text, lang)
    strong = [m for m in matches
              if catalog.match_score(m, text, None)[0] >= 50]
    if not strong:
        return None
    wants_nav = bool(_NAVIGATE_VERBS.search(text))
    asks = bool(_QUESTION_MARKERS.search(text))
    if wants_nav and not asks:
        return "navigate", strong
    if asks or not wants_nav:
        return "guide", strong
    return "navigate", strong


def _fast_path(text: str, lang: str) -> ParsedIntent | None:
    if _CLARIFY_ONLY.match(text):
        return ParsedIntent(kind="clarify", confidence="exact")
    # 领域写请求（§21）优先于报告/咨询类：明确的改写指令（如「重新生成
    # 学习计划」）不该被 planning_advice 吞掉；具体 operation 与参数由
    # policy 从文字确定性解析。
    if ((_DOMAIN_WRITE_RE.search(text) or _WORKFLOW_RE.search(text)
         or (_SUBSCRIPTION_INTENT_RE.search(text)
             and _SUBSCRIPTION_KIND_RE.search(text)))
            and not _WRITE_QUESTION_RE.search(text)):
        module = (_module_intent(text, lang) or (None, []))[1]
        return ParsedIntent(kind="prepare_action", candidates=module[:3],
                            confidence="strong")
    for regex, kind in ((_TEACHING_RE, "teaching_report"),
                        (_LEARNING_RE, "learning_report"),
                        (_PLANNING_RE, "planning_advice"),
                        (_PREPARE_RE, "prepare_action"),
                        (_SEARCH_RE, "search")):
        if regex.search(text):
            window, days = detect_window(text)
            module = (_module_intent(text, lang) or (None, []))[1]
            return ParsedIntent(kind=kind, candidates=module[:3],
                                window=window or ("recent" if kind in (
                                    "learning_report", "teaching_report")
                                    else ""),
                                custom_days=days, confidence="strong")
    # 编辑课件：明确动作语气才导航；查问（怎么编辑）仍走 guide。
    if (_LESSON_EDIT_RE.search(text)
            and not _QUESTION_MARKERS.search(text)):
        return ParsedIntent(kind="navigate", confidence="strong",
                            lesson_edit=True)
    module_hit = _module_intent(text, lang)
    if module_hit is not None:
        kind, strong = module_hit
        return ParsedIntent(kind=kind, module_route=strong[0].route_id,
                            candidates=strong[:3], confidence="strong")
    if _NAVIGATE_VERBS.search(text) and not _QUESTION_MARKERS.search(text):
        # 明确导航动词且非疑问，但目录无命中：仍按 navigate 走
        # resolve_destination（具名实体深链，如「打开微积分讲义第3页」；
        # 无实体命中时由 policy 不出动作，回答层如实解释）。
        return ParsedIntent(kind="navigate", confidence="strong")
    if _GREETING_RE.match(text):
        return ParsedIntent(kind="general_chat", confidence="exact")
    if _SITE_TOUR_RE.search(text):
        return ParsedIntent(kind="guide", confidence="strong")
    return None


def _fallback_intent(text: str) -> ParsedIntent:
    """模型不可用/失败时的确定性兜底（guide 或 general_chat）。"""
    if _SITE_TOUR_RE.search(text) or text.strip().endswith("?"):
        return ParsedIntent(kind="guide", confidence="fallback")
    return ParsedIntent(kind="general_chat", confidence="fallback")


def _extract_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def parse_intent(
    text: str,
    *,
    lang: str,
    route_id: AssistantRouteId | None = None,
    history: list[tuple[str, str]] | None = None,
    llm_call: Callable[[str, str], Awaitable[str]] | None = None,
) -> ParsedIntent:
    """解析意图：快路 → 注入的 llm_call（≤2 次模型预算）→ 确定性兜底。

    页码与时间窗口一样从原文确定性提取，与解析路径无关。
    """
    parsed = await _parse_intent(text, lang=lang, route_id=route_id,
                                 history=history, llm_call=llm_call)
    parsed.page = extract_page(text)
    return parsed


async def _parse_intent(
    text: str,
    *,
    lang: str,
    route_id: AssistantRouteId | None = None,
    history: list[tuple[str, str]] | None = None,
    llm_call: Callable[[str, str], Awaitable[str]] | None = None,
) -> ParsedIntent:
    fast = _fast_path(text, lang)
    if fast is not None:
        fast.text = text
        return fast

    if llm_call is None:
        fallback = _fallback_intent(text)
        fallback.text = text
        return fallback

    from app.prompts import registry as prompt_registry
    system = prompt_registry.get("site_assistant_intent").text.format(
        digest=catalog.catalog_digest(lang)[:3000],
        route_id=route_id.value if route_id else "(none)")
    context = ""
    for role, content in (history or [])[-4:]:
        context += f"{role}: {content[:200]}\n"
    user = (f"最近对话：\n{context}" if context else "") + f"用户输入：{text}"

    data: dict[str, Any] | None = None
    for attempt in range(2):  # 一次解析 + 一次受限修复（§10.5）
        try:
            raw = await llm_call(system, user if attempt == 0
                                 else user + "\n上次输出不是合法 JSON，请只输出一个 JSON 对象。")
        except Exception:
            break
        data = _extract_json(raw)
        if data is not None and data.get("kind") in INTENT_KINDS:
            break
        data = None
    if data is None:
        fallback = _fallback_intent(text)
        fallback.text = text
        return fallback

    kind = str(data.get("kind"))
    module_route: AssistantRouteId | None = None
    module_id = str(data.get("module_id") or "")
    if module_id:
        try:
            module_route = AssistantRouteId(module_id)
        except ValueError:
            module_route = None
    if module_route is not None:
        candidates = [catalog.get_module(module_route)]
    else:
        candidates = catalog.find_module_matches(text, lang)[:3]
    window, days = detect_window(text)
    llm_window = str(data.get("time_window") or "")
    if llm_window in ("recent", "this_week", "last_week", "custom"):
        window = llm_window
        days = max(0, min(int(data.get("custom_days") or 0), MAX_CUSTOM_DAYS))
    if kind in ("learning_report", "teaching_report") and not window:
        window = "recent"
    result = ParsedIntent(
        kind=kind, module_route=module_route,
        candidates=[m for m in candidates if m is not None][:3],
        workspace_name=(str(data.get("workspace_name") or "")
                        .strip() or None),
        window=window, custom_days=days, confidence="llm",
        source_id=(str(data.get("source_id") or "").strip() or None))
    result.text = text
    return result


def resolve_time_window(
    window: WindowKind,
    timezone_name: str,
    *,
    now: datetime | None = None,
    custom_days: int = 0,
    lang: str = "zh",
) -> tuple[datetime, datetime, str]:
    """§6.4：本地日历边界 → UTC 半开区间；夏令时安全。

    「最近」= 当前本地日期及之前 6 个自然日；「本周」= 本地周一 00:00
    至现在；「上周」= 前一个完整自然周。无效时区回退 UTC。
    """
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now = now or datetime.now(tz=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(tz)
    today = local.date()

    def _local_midnight(day) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=tz)

    if window == "this_week":
        start_day = today - timedelta(days=today.weekday())
        start, end = _local_midnight(start_day), now
        label = "本周" if lang == "zh" else "This week"
    elif window == "last_week":
        monday = today - timedelta(days=today.weekday())
        start = _local_midnight(monday - timedelta(days=7))
        end = _local_midnight(monday)
        label = "上周" if lang == "zh" else "Last week"
    elif window == "custom":
        days = max(1, min(custom_days or 7, MAX_CUSTOM_DAYS))
        start = _local_midnight(today - timedelta(days=days - 1))
        end = now
        label = (f"最近{days}天" if lang == "zh" else f"Last {days} days")
    else:  # recent / 缺省
        start = _local_midnight(today - timedelta(days=6))
        end = now
        label = "最近7天" if lang == "zh" else "Last 7 days"
    return (start.astimezone(timezone.utc), end.astimezone(timezone.utc),
            label)
