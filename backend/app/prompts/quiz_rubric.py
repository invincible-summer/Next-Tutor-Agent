"""Shared generation rubric text; contains no execution imports."""

RUBRIC_REQUIREMENT = """
量规（与题目一起生成，用于在看学生作答前冻结判分标准）——每道题对象内额外输出两个字段：
- "rubric_criteria": 2-4 条评分点数组。每条为 {{"id": "c1", "description": "可从学生作答直接观察的判分点（关键步骤/条件/结果）", "weight": 1.0, "critical": true}}。critical=true 表示关键步骤（该步不成立则整题不能算对）；description 写判分点本身（如「正确写出归一化分母」），禁止抄题目答案原文；weight 为该条权重（正数，一般 1.0）。
- "equivalent_solutions": 可接受的等价解法/写法数组（没有则 []）。"""
