"""确定性事实卡（A09）。

数字、日期、状态全部来自工具结果（服务端确定性生成），模型与展示层
不得新增数字、分母或百分比。完整 LearningReport/TeachingReport 固定
结构卡在 A12 落地；本模块先产出 metrics / choices / notice 块与
模型不可用时的确定性文字兜底。

块顺序约定（§11.4）：markdown 正文 → metrics → choices → notice。
"""
from __future__ import annotations

from typing import Any

from .intent import ParsedIntent

_DAY_UNIT = {"zh": "天", "en": "days"}

# 实体类名（§20.4 kind → 展示标签）；choices 描述与搜索清单共用。
_ENTITY_CANDIDATE_LABELS = {
    "chat": "对话", "note": "笔记", "lesson": "课程",
    "file": "文件", "textbook": "教材", "concept": "概念",
    "task": "任务", "goal": "目标", "archive": "归档",
    "workspace": "工作区", "module": "模块",
}


def _by_tool(results: list[dict[str, Any]],
             name: str) -> dict[str, Any] | None:
    for result in results:
        if result.get("tool") == name:
            return result
    return None


def _fmt_date(iso: str | None) -> str:
    return str(iso or "")[:10]


def _window_line(data: dict[str, Any], lang: str,
                 label: str | None = None) -> str:
    window = data.get("window") or {}
    start = _fmt_date(window.get("start_at"))
    end = _fmt_date(window.get("end_at"))
    if not start:
        return ""
    zh = lang == "zh"
    head = label or ("统计窗口" if zh else "Window")
    if start == end:
        return f"{head}：{start}" if zh else f"{head}: {start}"
    return (f"{head}：{start} 至 {end}" if zh
            else f"{head}: {start} to {end}")


def _metric(key: str, label: str, *, value: Any, unit: str = "",
            completeness: str = "complete", known_minimum: Any = None,
            unavailable_reason: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"key": key, "label": label[:120],
                            "unit": unit[:32], "completeness": completeness}
    if value is None and completeness != "unavailable":
        item["value"] = None
    else:
        item["value"] = value
    if known_minimum is not None:
        item["known_minimum"] = known_minimum
    if unavailable_reason:
        item["unavailable_reason"] = unavailable_reason[:200]
    return item


def _learning_metrics(result: dict[str, Any],
                      lang: str) -> list[dict[str, Any]]:
    data = result.get("data") or {}
    answers = data.get("answers") or {}
    status = str(answers.get("status") or "empty")
    completeness = {"ready": "complete", "partial": "partial",
                    "empty": "complete"}.get(status, "unavailable")
    zh = lang == "zh"
    items: list[dict[str, Any]] = []
    items.append(_metric(
        "answer_attempt_count",
        "已受理作答" if zh else "Accepted answers",
        value=answers.get("answer_attempt_count"),
        completeness=completeness,
        unavailable_reason=(None if completeness != "unavailable"
                            else "学习证据读取失败")))
    items.append(_metric(
        "graded_answer_count", "已判分作答" if zh else "Graded answers",
        value=answers.get("graded_answer_count"),
        completeness=completeness))
    items.append(_metric(
        "pending_answer_count", "待判定作答" if zh else "Pending answers",
        value=answers.get("pending_answer_count"),
        completeness=completeness))
    items.append(_metric(
        "recorded_learning_days", "有记录的学习日" if zh else "Recorded days",
        value=data.get("recorded_learning_days"),
        unit=_DAY_UNIT.get(lang, "days")))
    completed = data.get("completed_tasks") or {}
    items.append(_metric(
        "completed_task_count", "完成任务" if zh else "Completed tasks",
        value=completed.get("value"),
        completeness="complete" if completed.get("complete") else "partial",
        known_minimum=(completed.get("known_minimum")
                       if not completed.get("complete") else None)))
    return items


# ---------------------------------------------------------------------------
# §7.2/§7.6/§11.4 固定结构报告（A12）
# ---------------------------------------------------------------------------

_FAILURE_LABELS_ZH = {
    "no_evidence": "证据不足", "contradiction": "作答矛盾",
    "off_topic": "偏离主题", "answer_mismatch": "答非所问",
    "depth_mismatch": "深度不匹配", "retrieval_miss": "检索未命中",
    "missing_verification": "缺少效果验证", "other": "其他",
}
_STRATEGY_LABELS_ZH = {
    "direct": "直接讲解", "analogy": "类比说明", "socratic": "苏格拉底提问",
    "example": "例题演示", "decompose": "分步拆解", "visual": "图示辅助",
    "quiz": "即时测验", "recap": "阶段回顾",
}
_PROPOSAL_LABEL = {"proposed": "待审批", "approved": "已批准",
                   "applied": "已应用", "rejected": "已拒绝"}


def _report_window(data: dict[str, Any], timezone_name: str, label: str,
                   lang: str) -> dict[str, Any]:
    window = data.get("window") or {}
    return {
        "start_at": window.get("start_at") or "",
        "end_at": window.get("end_at") or "",
        "timezone": window.get("timezone") or timezone_name,
        "label": label or ("最近7天" if lang == "zh" else "Last 7 days"),
    }


def build_learning_report(
    result: dict[str, Any],
    meta: dict[str, Any],
    *,
    window_label: str,
    timezone_name: str,
    lang: str = "zh",
    now_iso: str = "",
) -> dict[str, Any]:
    """§7.2 近期学习报告固定结构；数字与主张全部来自工具结果。"""
    data = result.get("data") or {}
    zh = lang == "zh"
    scope = meta.get("scope") or {}
    answers = data.get("answers") or {}
    completed = data.get("completed_tasks") or {}
    facts = _learning_metrics(result, lang)
    for item in facts:
        item["source_ids"] = [s.get("source_id", "")
                              for s in result.get("sources", [])][:1]

    ws_names = data.get("workspace_names") or {}
    summaries: list[dict[str, Any]] = []
    for ws in (data.get("workspace_summaries") or [])[:20]:
        wid = str(ws.get("workspace_id") or "")
        source_ids = [s.get("source_id", "") for s in result.get("sources", [])
                      if s.get("workspace_id") == wid][:30]
        summaries.append({
            "workspace_id": wid,
            "workspace_name": str(ws.get("workspace_name")
                                  or ws_names.get(wid, wid)),
            "scope_revision": str(ws.get("scope_revision") or ""),
            "evidence_watermark": "",
            "evaluation_status": str(ws.get("evaluation_status")
                                     or "pending"),
            "coverage": {
                "observed_concepts": ws.get("coverage", {}).get(
                    "observed_concepts"),
                "coverage_note": None,
            },
            "statements": [
                {"text": st.get("text") or "", "status": st.get("status"),
                 "scope_note": None, "source_ids": source_ids[:1]}
                for st in (ws.get("statements") or [])[:3]],
            "pending_counts": ws.get("pending_counts") or {
                "evaluation_pending": 0, "active_reviews": 0,
                "failed_jobs": 0},
            "source_ids": source_ids,
        })

    unscoped = data.get("unscoped") or {}
    unscoped_activity = {
        "included": bool(unscoped.get("included")),
        "summary": (
            (f"另有 {unscoped.get('days')} 天未绑定工作区的真实学习记录。"
             if zh else
             f"{unscoped.get('days')} day(s) of unscoped activity included.")
            if unscoped.get("included") and unscoped.get("days") else ""),
        "recorded_learning_days": unscoped.get("days")
        if unscoped.get("included") else None,
        "source_ids": [],
    }

    # 下一步（≤3）：来自待处理与证据入口，不虚构行动。
    next_steps: list[dict[str, Any]] = []
    pending_total = sum(s.get("pending_counts", {}).get(
        "evaluation_pending", 0) for s in summaries)
    if pending_total:
        next_steps.append({
            "title": ("查看待评价证据" if zh else "Review pending evidence"),
            "rationale": ((f"有 {pending_total} 条新作答已完成判定，"
                           "语义评价仍在处理。") if zh else
                          (f"{pending_total} graded answers await semantic "
                           "evaluation.")),
            "source_ids": [],
        })
    if summaries:
        first = summaries[0]
        next_steps.append({
            "title": ("查看学习档案" if zh else "Open learning archive"),
            "rationale": ((f"工作区「{first['workspace_name']}」的完整证据"
                           "时间线与概念主张。") if zh else
                          (f"Full evidence timeline and claims of "
                           f"\"{first['workspace_name']}\".")),
            "source_ids": first["source_ids"][:1],
        })
    if int(answers.get("pending_answer_count") or 0):
        next_steps.append({
            "title": ("查看待判定作答" if zh else "Check pending answers"),
            "rationale": ((f"{answers.get('pending_answer_count')} 条作答尚"
                           "未有可用判定。") if zh else
                          "Some answers have no usable verdict yet."),
            "source_ids": [],
        })

    notices = [dict(n) for n in result.get("notices", [])][:10]
    if completed.get("value") is None and completed.get("known_minimum"):
        notices.append({
            "code": "completed_tasks_lower_bound",
            "message": ("完成任务数为已知下界（历史记录不完整）。" if zh
                        else "Completed tasks shown as a known minimum."),
        })

    return {
        "window": _report_window(data, timezone_name, window_label, lang),
        "scope": {
            "mode": scope.get("mode") or "all_workspaces",
            "workspace_ids": scope.get("workspace_ids") or [],
            "scope_revisions": {},
        },
        "facts": facts[:6],
        "workspace_summaries": summaries,
        "unscoped_activity": unscoped_activity,
        "next_steps": next_steps[:3],
        "generated_at": now_iso or result.get("generated_at") or "",
        "complete": bool(result.get("complete")),
        "display_truncated": False,
        "notices": notices,
    }


def build_teaching_report(
    result: dict[str, Any],
    *,
    window_label: str,
    timezone_name: str,
    lang: str = "zh",
    now_iso: str = "",
) -> dict[str, Any]:
    """§7.6 教学效果报告：账号级范围；样本<5 只列现象不排名。"""
    data = result.get("data") or {}
    zh = lang == "zh"
    failure = [
        {"category": str(k), "count": int(v),
         "label": _FAILURE_LABELS_ZH.get(str(k)) if zh else None}
        for k, v in (data.get("failure_distribution") or {}).items()
    ][:20]
    strategies = [
        {"name": str(s.get("name") or "unknown"),
         "label": (_STRATEGY_LABELS_ZH.get(str(s.get("name")))
                   if zh else None),
         "attempts": int(s.get("attempts") or 0),
         "successes": int(s.get("successes") or 0)}
        for s in (data.get("top_strategies") or [])
    ][:10]
    proposals = [
        {"proposal_id": str(p.get("proposal_id") or ""),
         "title": p.get("title"),
         "status": p.get("status") if p.get("status") in (
             "proposed", "approved", "applied", "rejected") else "proposed",
         "summary": None}
        for p in (data.get("proposals") or [])
    ][:20]
    coverage = data.get("coverage") or {}
    notices = [dict(n) for n in result.get("notices", [])][:10]
    small_sample = [s for s in strategies
                    if (s["attempts"] or 0) < 5]
    if strategies and small_sample:
        notices.append({
            "code": "small_sample_suppressed",
            "message": ("部分策略样本少于 5 条，仅列出现象，不使用"
                        "「最有效」措辞。" if zh else
                        "Some strategies have <5 samples; listed as "
                        "observations, not rankings."),
        })
    return {
        "window": _report_window(data, timezone_name, window_label, lang),
        "scope": {"mode": "account", "workspace_ids": [],
                  "scope_revisions": {}},
        "total_turns": int(data.get("total_turns") or 0),
        "failure_distribution": failure,
        "top_strategies": strategies,
        "proposals": proposals,
        "active_guidance": [],
        "pending_proposals": int(data.get("pending_proposals") or 0),
        "coverage": {
            "complete": bool(coverage.get("complete")),
            "inspected_count": int(coverage.get("inspected_count") or 0),
            "invalid_count": int(coverage.get("invalid_count") or 0),
            "earliest_available_at": coverage.get("earliest_available_at"),
        },
        "generated_at": now_iso or result.get("generated_at") or "",
        "source_ids": [s.get("source_id", "")
                       for s in result.get("sources", [])][:30],
        "notices": notices,
    }


def build_blocks(
    parsed: ParsedIntent,
    results: list[dict[str, Any]],
    meta: dict[str, Any],
    *,
    lang: str,
    window_label: str = "",
    timezone_name: str = "UTC",
    now_iso: str = "",
) -> list[dict[str, Any]]:
    """意图 + 工具结果 → 结构化展示块（不含流式 markdown 正文块）。"""
    blocks: list[dict[str, Any]] = []
    seq = iter(range(1, 24))
    zh = lang == "zh"

    if parsed.kind == "learning_report":
        summary = _by_tool(results, "get_learning_summary")
        if summary is not None and summary.get("status") != "error":
            blocks.append({
                "block_id": f"b{next(seq)}", "type": "learning_report",
                "report": build_learning_report(
                    summary, meta, window_label=window_label,
                    timezone_name=timezone_name, lang=lang,
                    now_iso=now_iso)})

    if parsed.kind == "teaching_report":
        teaching = _by_tool(results, "get_teaching_summary")
        if teaching is not None and teaching.get("status") != "error":
            blocks.append({
                "block_id": f"b{next(seq)}", "type": "teaching_report",
                "report": build_teaching_report(
                    teaching, window_label=window_label,
                    timezone_name=timezone_name, lang=lang,
                    now_iso=now_iso)})

    if parsed.kind == "planning_advice":
        summary = _by_tool(results, "get_learning_summary")
        if summary and summary.get("status") == "ready":
            blocks.append({
                "block_id": f"b{next(seq)}", "type": "metrics",
                "items": _learning_metrics(summary, lang)})

    if parsed.kind == "clarify":
        options: list[dict[str, Any]] = []
        for ws in (meta.get("workspace_candidates") or [])[:4]:
            options.append({
                "option_id": str(ws.get("workspace_id")),
                "label": str(ws.get("name", ""))[:200]})
        dest = _by_tool(results, "resolve_destination")
        for cand in ((dest or {}).get("data") or {}).get("candidates", [])[:4]:
            options.append({
                "option_id": (cand.get("route_id")
                              or cand.get("workspace_id")
                              or cand.get("kind") or "opt"),
                "label": str(cand.get("title", ""))[:200]})
        if options:
            blocks.append({
                "block_id": f"b{next(seq)}", "type": "choices",
                "prompt": "你想指的是哪一个？" if zh else "Which one?",
                "options": options[:6]})

    if parsed.kind in ("guide", "navigate", "search", "prepare_action"):
        # §20.4 全站检索结果：确定性结果清单（点击导航由 choices/actions 承载）。
        if parsed.kind == "search":
            found = _by_tool(results, "search_site_entities")
            items = ((found or {}).get("data") or {}).get("items") or []
            if items:
                kind_labels = _ENTITY_CANDIDATE_LABELS if zh else {
                    "chat": "chat", "note": "note", "lesson": "lesson",
                    "file": "file", "textbook": "textbook",
                    "concept": "concept", "task": "task", "goal": "goal",
                    "archive": "archived",
                }
                total = ((found or {}).get("data") or {}).get("total")
                head = (f"找到 {total} 个相关内容："
                        if zh else f"{total} matches:")
                lines = [
                    f"- [{kind_labels.get(i.get('entity_kind'), '?')}] "
                    f"{i.get('title')}"
                    + (f"（{i.get('subtitle')}）" if zh and i.get("subtitle")
                       else "")
                    for i in items[:5]]
                if total and total > len(items[:5]):
                    lines.append("……" if zh else "…")
                blocks.append({
                    "block_id": f"b{next(seq)}", "type": "markdown",
                    "text": head + "\n" + "\n".join(lines)})
        options = []
        dest = _by_tool(results, "resolve_destination")
        for cand in ((dest or {}).get("data") or {}).get("candidates", []):
            target = cand.get("target") or {}
            if (str(target.get("kind")) == "lesson"
                    and target.get("view") == "edit"):
                # 编辑课件候选：option_id 用「编辑+课程名」，点击后重入
                # 同一意图形成闭环；工作区名放描述区分同名课程（§8.4）。
                options.append({
                    "option_id": ("编辑" + str(cand.get("title", "")))[:64],
                    "label": str(cand.get("title", ""))[:200],
                    "description": (str(cand.get("workspace_name") or "")
                                    or "备课上课")})
                continue
            # 实体候选（note/file 等）的 option_id 用 entity_id 保证唯一，
            # 点击 label（实体标题）重入意图后即可唯一命中出动作卡。
            cand_kind = str(cand.get("kind") or "")
            options.append({
                "option_id": str(cand.get("route_id")
                                 or cand.get("workspace_id")
                                 or cand.get("entity_id")
                                 or cand.get("kind") or "opt"),
                "label": str(cand.get("title", ""))[:200],
                "description": (_ENTITY_CANDIDATE_LABELS.get(cand_kind)
                                or cand_kind or None)})
        if not options:
            help_result = _by_tool(results, "get_product_help")
            for module in ((help_result or {}).get("data")
                           or {}).get("modules", [])[:3]:
                options.append({
                    "option_id": str(module.get("route_id")),
                    "label": str(module.get("name", ""))[:200],
                    "description": str(module.get("summary", ""))[:500]})
        if len(options) >= 2:
            blocks.append({
                "block_id": f"b{next(seq)}", "type": "choices",
                "prompt": ("你想去哪里？" if zh else "Where to?"),
                "options": options[:4]})

    # 首个重要提示以 notice 块呈现（其余保留在报告 notices 里，A12 展开）
    for result in results:
        notice = next((n for n in result.get("notices", [])
                       if any(tag in str(n.get("code", ""))
                              for tag in ("error", "unavailable", "truncated",
                                          "incomplete", "disabled"))), None)
        if notice is not None:
            blocks.append({
                "block_id": f"b{next(seq)}", "type": "notice",
                "tone": "warning",
                "code": str(notice.get("code", "notice"))[:64],
                "text": str(notice.get("message", ""))[:1000]})
            break
    return blocks


def deterministic_answer(
    parsed: ParsedIntent,
    results: list[dict[str, Any]],
    meta: dict[str, Any],
    *,
    lang: str,
    window_label: str = "",
) -> str:
    """模型不可用/失败时的确定性回答：只解释工具事实，不新增结论。"""
    zh = lang == "zh"
    kind = parsed.kind

    if kind == "guide":
        help_result = _by_tool(results, "get_product_help")
        modules = ((help_result or {}).get("data") or {}).get("modules", [])
        if modules:
            lines = [f"{m.get('name')}：{m.get('summary')}" for m in modules[:3]]
            head = "为你找到这些功能：" if zh else "Found for you:"
            return head + "\n" + "\n".join(lines)
        return ("这个网站围绕聊天辅导、备课上课、学习回顾三条主线。"
                if zh else
                "This site covers chat tutoring, courses and review.")

    if kind == "navigate":
        module = parsed.primary_module
        if module is not None:
            name = module.name.zh if zh else module.name.en
            return (f"可以，{name}的入口在下方。" if zh
                    else f"Sure — the entry to {name} is below.")
        return ("我没有找到唯一入口，请从下面选择。" if zh
                else "No unique match; pick one below.")

    if kind == "search":
        found = _by_tool(results, "search_site_entities")
        items = ((found or {}).get("data") or {}).get("items") or []
        if items:
            titles = "、".join(
                f"{i.get('title')}（{i.get('entity_kind')}）"
                for i in items[:3])
            return (f"找到这些内容：{titles}。" if zh
                    else f"Matches: {titles}.")
        dest = _by_tool(results, "resolve_destination")
        candidates = ((dest or {}).get("data") or {}).get("candidates", [])
        if candidates:
            titles = "、".join(str(c.get("title")) for c in candidates[:3])
            return (f"找到这些匹配：{titles}。" if zh
                    else f"Matches: {titles}.")
        return ("没有找到匹配的内容。" if zh else "No matches found.")

    if kind == "learning_report":
        summary = _by_tool(results, "get_learning_summary")
        data = (summary or {}).get("data") or {}
        window = data.get("window") or {}
        line = _window_line(data, lang, window_label or None)
        status = (summary or {}).get("status")
        if status == "empty":
            return ((line + "\n" if line else "")
                    + ("目前没有足够记录。可以先在聊天辅导里答几道题，"
                       "再来查看学习近况。" if zh else
                       "Not enough records yet. Try a few exercises first."))
        answers = data.get("answers") or {}
        completed = data.get("completed_tasks") or {}
        parts = []
        if line:
            parts.append(line)
        if zh:
            parts.append(
                f"有记录的学习日 {data.get('recorded_learning_days')} 天；"
                f"作答 {answers.get('answer_attempt_count')} 次，其中已判分 "
                f"{answers.get('graded_answer_count')} 次、待判定 "
                f"{answers.get('pending_answer_count')} 次。")
            if completed.get("value") is not None:
                parts.append(f"完成任务 {completed.get('value')} 项。")
            elif completed.get("known_minimum"):
                parts.append(f"完成任务至少 {completed.get('known_minimum')} 项"
                             "（历史记录不完整，为已知下界）。")
        else:
            parts.append(
                f"Recorded days: {data.get('recorded_learning_days')}; "
                f"answers: {answers.get('answer_attempt_count')} "
                f"(graded {answers.get('graded_answer_count')}, pending "
                f"{answers.get('pending_answer_count')}).")
            if completed.get("value") is not None:
                parts.append(f"Completed tasks: {completed.get('value')}.")
        return "\n".join(parts)

    if kind == "teaching_report":
        teaching = _by_tool(results, "get_teaching_summary")
        data = (teaching or {}).get("data") or {}
        line = _window_line(data, lang, window_label or None)
        status = (teaching or {}).get("status")
        if status == "empty":
            return ((line + "\n" if line else "")
                    + ("与你的教学交互暂无足够记录，无法给出教学效果结论。"
                       if zh else
                       "Not enough teaching records for a conclusion."))
        parts = []
        if line:
            parts.append(line)
        strategies = data.get("top_strategies") or []
        if zh:
            parts.append(f"窗口内教学轮 {data.get('total_turns')}。")
            for s in strategies[:3]:
                parts.append(
                    f"{s.get('name')}：尝试 {s.get('attempts')} 次、"
                    f"成功 {s.get('successes')} 次（样本 "
                    f"{s.get('sample_count')} 条）。")
            pending = data.get("pending_proposals") or 0
            if pending:
                parts.append(f"有 {pending} 条教学改进提案待处理。")
        else:
            parts.append(f"Teaching turns in window: {data.get('total_turns')}.")
            for s in strategies[:3]:
                parts.append(
                    f"{s.get('name')}: {s.get('successes')}/"
                    f"{s.get('attempts')} (sample {s.get('sample_count')}).")
        return "\n".join(parts)

    if kind == "planning_advice":
        tasks = _by_tool(results, "get_saved_tasks")
        data = (tasks or {}).get("data") or {}
        today = data.get("today") or []
        open_tasks = data.get("open") or []
        if not today and not open_tasks:
            return ("目前没有已保存的任务。" if zh else "No saved tasks yet.")
        zh = lang == "zh"
        lines = []
        if today:
            if zh:
                lines.append("今天的任务：")
                lines.extend(f"- {t.get('title') or t.get('id')}"
                             for t in today[:5])
            else:
                lines.append("Today:")
                lines.extend(f"- {t.get('title') or t.get('id')}"
                             for t in today[:5])
        if open_tasks:
            if zh:
                lines.append(f"待办（含逾期）：{len(open_tasks)} 项。")
            else:
                lines.append(f"Open (incl. overdue): {len(open_tasks)}.")
        return "\n".join(lines)

    if kind == "prepare_action":
        course = _by_tool(results, "find_course")
        data = (course or {}).get("data") or {}
        resume = data.get("resume")
        if resume:
            title = resume.get("lesson_title") or resume.get("lesson_id") or ""
            return (f"可以继续上次的课程「{title}」。" if zh else
                    f"You can resume the lesson \"{title}\".")
        return ("可以从课程模块开始准备一节课。" if zh
                else "Start preparing a lesson in the course module.")

    if kind == "clarify":
        return ("你指的是哪一个？请从下面选择。" if zh
                else "Which one do you mean? Pick below.")

    # general_chat
    greeting = ("我是站内学习助手，可以介绍功能、带你去模块、查学习近况。"
                if zh else
                "I'm the site assistant — tours, navigation and progress.")
    return greeting
