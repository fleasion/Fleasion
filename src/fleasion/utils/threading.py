"""Threading utilities."""

from __future__ import annotations

import threading
from functools import wraps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def run_in_thread[**P, R](func: Callable[P, R]) -> Callable[P, threading.Thread]:
    """Decorator to run a function in a daemon thread."""

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> threading.Thread:
        thread = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
        thread.start()
        return thread

    return wrapper
