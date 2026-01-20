"""Threading utilities."""

import threading
from collections.abc import Callable
from functools import wraps


def run_in_thread(func: Callable) -> Callable:
    """Decorator to run a function in a daemon thread."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
        thread.start()
        return thread

    return wrapper
