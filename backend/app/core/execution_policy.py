"""Request-local side effects; authenticated execution retains its defaults."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ExecutionPolicy:
    persistent: bool = True
    register_quiz: Callable | None = None


_current = ContextVar("tutor_execution_policy", default=ExecutionPolicy())


def current_policy() -> ExecutionPolicy:
    return _current.get()


@contextmanager
def execution_policy(policy: ExecutionPolicy):
    token = _current.set(policy)
    try:
        yield
    finally:
        _current.reset(token)
