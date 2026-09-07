"""learning_style 的唯一生产写入路径：把 M8 已采集的体验反馈折叠成 M2 学习风格。

背景（P0 修复）：``LearningStyle`` 宣称 "auto-inferred from behaviour"，但
全库长期没有任何写入方——事件处理器、API、UX 层都不碰它，生产里永远是
默认值 ``balanced/adaptive``，读取端（M3 policy 的 depth/preference 覆盖、
M8 渲染）因此从不触发。链路在测试里活着、在生产里不存在。

本模块是唯一的写入点。纯规则、零 LLM、保守翻转：信号不足一律保持现状。
信号源是 M8 ``UXProfile.recent_feedback``（每轮规则分类的学生反馈类型，
保留最近 12 条）——M8 早在采集这些信号，缺的只是折叠进 M2 的这一步。

规则（可解释、刻意保守）：
  - 窗内「太长/啰嗦」≥2 → explanation_depth=basic（收敛篇幅）
  - 窗内「太短/没讲清」≥2 → explanation_depth=deep（展开讲透）
  - 两类同时 ≥2 → 不动（信号矛盾）
  - 窗内「太难/看不懂」≥2 → preference=step_by_step（拆解降低认知负荷）
  - 否则 → 不翻转（返回空 dict）
"""
from __future__ import annotations

from typing import Any

# 同一反馈类型在窗口内出现几次才翻转：1 次是口误，2 次才是口味。
_FLIP_THRESHOLD = 2


def infer_style_update(recent_feedback: list[Any], *,
                       current_preference: str = "balanced",
                       current_depth: str = "adaptive") -> dict[str, str]:
    """从最近反馈窗口推断 learning_style 需要的翻转。

    返回 {field: new_value}，只包含需要变化的字段；无翻转条件返回空 dict。
    元素可以是 FeedbackType 枚举或裸字符串（按 .value/str 兼容）。
    """
    counts: dict[str, int] = {}
    for f in recent_feedback or []:
        v = str(getattr(f, "value", f) or "")
        if v and v != "none":
            counts[v] = counts.get(v, 0) + 1

    too_long = counts.get("explanation_too_long", 0)
    too_short = counts.get("explanation_too_short", 0)
    too_hard = counts.get("explanation_too_hard", 0)

    out: dict[str, str] = {}
    # 篇幅：矛盾信号（又长又短都多）时不翻转
    if too_long >= _FLIP_THRESHOLD and too_short < _FLIP_THRESHOLD \
            and current_depth != "basic":
        out["explanation_depth"] = "basic"
    elif too_short >= _FLIP_THRESHOLD and too_long < _FLIP_THRESHOLD \
            and current_depth != "deep":
        out["explanation_depth"] = "deep"
    # 认知负荷：反复喊难 → 拆成更细的步骤
    if too_hard >= _FLIP_THRESHOLD and current_preference != "step_by_step":
        out["preference"] = "step_by_step"
    return out


def apply_style_inference(student_model: Any, recent_feedback: list[Any]) -> bool:
    """把推断结果写进 StudentModel（有变化才落盘）。Never raises."""
    try:
        ls = student_model.profile.learning_style
        update = infer_style_update(
            recent_feedback,
            current_preference=getattr(ls, "preference", "balanced"),
            current_depth=getattr(ls, "explanation_depth", "adaptive"),
        )
        if not update:
            return False
        return bool(student_model.update_learning_style(**update))
    except Exception:
        return False
