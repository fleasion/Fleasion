"""Lazy compatibility wrapper for updater startup."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

lazy from fleasion.utils import updater

if TYPE_CHECKING:
    from collections.abc import Callable


def start_update_check(*args: object, **kwargs: object) -> None:
    """Load the updater lazily while preserving the historical call signature."""
    compatible = cast('Callable[..., None]', updater.start_update_check)
    return compatible(*args, **kwargs)
