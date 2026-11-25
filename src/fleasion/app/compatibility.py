"""Typed callbacks and errors at native application boundaries."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict

type VoidCallback = Callable[[], object]


class RelaunchCompletion(TypedDict, total=False):
    wait_result: int
    exit_code_read: bool
    exit_code: int | None


class CompatibilityBoundaryError(RuntimeError):
    """Wrap failures from dynamic/native compatibility boundaries."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


def call_compatibility_boundary[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except Exception as exc:
        raise CompatibilityBoundaryError(exc) from exc


class RestartHandoffUncertainError(RuntimeError):
    """The old process cannot safely reclaim state from a failed replacement."""


RestartHandoffUncertain = RestartHandoffUncertainError
