"""统一语义学习评价域（plan.md §4–§6）。

模块边界（§6.2）：评价核心不得 import `api/v1/*`，也不得 import M3/M9 的
manager/store。本包只依赖 Pydantic 与标准库；跨模块读取经 `ports.py` 协议
由 `core/learner_runtime.py` composition root 注入。
"""
