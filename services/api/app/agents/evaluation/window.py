"""M7 教学效果的时间窗口只读投影（plan.md GAP-05 / §7.6，A03）。

与既有 ``manager.report()`` 的区别：
- 支持半开区间 [start_at, end_at) 的窗口过滤（report() 无日期参数）。
- 严格读取 trace 文件：区分文件缺失（empty）与坏行/读取失败
  （partial/error），不再把异常吞成空集合后声称 complete=true。
- 统计沿用 ``strategy_analyzer.analyze_traces()``/``summarize()`` 的现有
  口径；本模块不新增另一套教学质量评分。
- top_strategies 显式来自 analyze_traces（旧前端类型里的字段在
  summarize() 中并不存在，不能从缺失字段读空排名冒充结论）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any

from . import store as eval_store
from .schema import TurnTrace
from . import strategy_analyzer

# 严格读取预算：超过该行数时仅分析最近部分并标记不完整。
_WINDOW_TRACE_BUDGET = 20000


def _read_traces_strict(student_id: str) -> tuple[list[TurnTrace], dict]:
    path: Path = eval_store._resolve(student_id, ext=".eval_traces.jsonl")
    coverage = {"exists": path.exists(), "readable": True,
                "invalid_count": 0, "total_lines": 0, "truncated": False}
    if not path.exists():
        return [], coverage
    out: list[TurnTrace] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                coverage["total_lines"] += 1
                try:
                    out.append(TurnTrace.from_dict(json.loads(line)))
                except Exception:
                    coverage["invalid_count"] += 1
    except Exception:
        coverage["readable"] = False
        return [], coverage
    if len(out) > _WINDOW_TRACE_BUDGET:
        out = out[-_WINDOW_TRACE_BUDGET:]
        coverage["truncated"] = True
    return out, coverage


def teaching_window_report(
    student_id: str,
    *,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    """窗口内教学效果投影；只读，不改写 trace，不触发提案生成。

    账号级范围（ResolvedScope.mode=account）：M7 trace 无可靠历史工作区
    归属，本报告不按工作区拆分。
    """
    traces, coverage = _read_traces_strict(student_id)
    earliest: str | None = None
    windowed: list[TurnTrace] = []
    if coverage["readable"]:
        for trace in traces:
            ts = getattr(trace, "ts", 0) or 0
            try:
                trace_dt = datetime.fromtimestamp(float(ts), tz=dt_timezone.utc)
            except (TypeError, ValueError, OSError):
                coverage["invalid_count"] += 1
                continue
            if earliest is None:
                earliest = trace_dt.isoformat()
            if start_at <= trace_dt < end_at:
                windowed.append(trace)

    stats = strategy_analyzer.summarize(windowed)
    effects = strategy_analyzer.analyze_traces(windowed)
    top_strategies = [
        {
            "name": getattr(effect, "mode", "") or "unknown",
            "subject": getattr(effect, "subject", ""),
            "attempts": int(getattr(effect, "total", 0) or 0),
            "successes": int(getattr(effect, "successes", 0) or 0),
            "sample_count": int(getattr(effect, "sample_size", 0) or 0),
        }
        for effect in effects
    ][:10]

    complete = bool(
        coverage["readable"] and not coverage["truncated"]
        and not coverage["invalid_count"])
    status = "empty"
    if not coverage["readable"]:
        status = "error"
    elif coverage["exists"] and coverage["invalid_count"]:
        status = "partial"
    elif windowed:
        status = "ready"
    elif coverage["exists"] and coverage["total_lines"]:
        status = "ready"  # 有记录但都不在窗口内：成功读取且窗口内为空
    else:
        status = "empty"

    notices: list[dict[str, str]] = []
    if coverage["truncated"]:
        notices.append({
            "code": "traces_truncated",
            "message": "教学记录超出读取预算，仅统计最近部分。",
        })
    if coverage["invalid_count"]:
        notices.append({
            "code": "traces_invalid_lines",
            "message": f"{coverage['invalid_count']} 条教学记录损坏，已跳过。",
        })

    return {
        "scope_mode": "account",
        "status": status,
        "window": {
            "start_at": start_at.astimezone(dt_timezone.utc).isoformat(),
            "end_at": end_at.astimezone(dt_timezone.utc).isoformat(),
        },
        "total_turns": int(stats.get("total", 0) or 0),
        "failure_distribution": {
            str(k): int(v) for k, v in
            (stats.get("failure_distribution") or {}).items()},
        "avg_tokens": float(stats.get("avg_tokens", 0.0) or 0.0),
        "top_strategies": top_strategies,
        "coverage": {
            "complete": complete,
            "inspected_count": coverage["total_lines"],
            "invalid_count": coverage["invalid_count"],
            "earliest_available_at": earliest,
        },
        "notices": notices,
    }
