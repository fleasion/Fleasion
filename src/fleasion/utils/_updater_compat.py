"""Lazy compatibility wrapper for updater startup."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable


def start_update_check(*args: object, **kwargs: object) -> None:
    """Load the updater lazily while preserving the historical call signature."""
    updater = importlib.import_module('.updater', __package__)
    compatible = cast('Callable[..., None]', updater.start_update_check)
    return compatible(*args, **kwargs)
