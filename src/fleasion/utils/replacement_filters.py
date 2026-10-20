"""Conditions that limit a replacement rule to some of the assets it targets.

A type-wide rule such as "replace every Audio" also hits short sound effects,
so a rule can carry filters on the original asset's audio length and file
size. Both are only known from the original's bytes, so filtered rules are
decided when the CDN download passes through the proxy, not when the batch
request is rewritten. Only local-file and CDN-URL rules can be served at that
point; ID rules rewrite the request before the original is seen.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

type FilteredMode = Literal['local', 'cdn']

FILTER_KEYS = ('min_seconds', 'max_seconds', 'min_kb', 'max_kb')


def _positive_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number > 0 else None


@dataclass(frozen=True, slots=True)
class ReplacementFilter:
    """Inclusive bounds; ``None`` means unbounded."""

    min_seconds: float | None = None
    max_seconds: float | None = None
    min_kb: float | None = None
    max_kb: float | None = None

    @classmethod
    def from_config(cls, raw: object) -> ReplacementFilter | None:
        """Parse a rule's ``filters`` mapping; ``None`` when it sets no bound."""
        if not isinstance(raw, dict):
            return None
        parsed = cls(**{key: _positive_number(raw.get(key)) for key in FILTER_KEYS})
        return parsed if parsed.is_active else None

    @property
    def is_active(self) -> bool:
        return any(getattr(self, key) is not None for key in FILTER_KEYS)

    @property
    def needs_duration(self) -> bool:
        return self.min_seconds is not None or self.max_seconds is not None

    def to_config(self) -> dict[str, float]:
        return {key: value for key in FILTER_KEYS if (value := getattr(self, key)) is not None}

    def matches(self, size_bytes: int, duration_seconds: float | None) -> bool:
        """Whether an original of this size and audio length passes the filter.

        An asset whose length cannot be read (not audio, or an unknown format)
        never passes a length bound.
        """
        size_kb = size_bytes / 1024
        if self.min_kb is not None and size_kb < self.min_kb:
            return False
        if self.max_kb is not None and size_kb > self.max_kb:
            return False
        if not self.needs_duration:
            return True
        if duration_seconds is None:
            return False
        if self.min_seconds is not None and duration_seconds < self.min_seconds:
            return False
        return self.max_seconds is None or duration_seconds <= self.max_seconds


ALWAYS = ReplacementFilter()
"""Matches everything; marks an unfiltered rule kept as the final fallback."""


@dataclass(frozen=True, slots=True)
class FilteredRoute:
    filters: ReplacementFilter
    mode: FilteredMode
    value: str
    """A local file path or a CDN URL, by ``mode``."""


def audio_duration_seconds(data: bytes) -> float | None:
    """Length of an audio payload, or ``None`` if it is not readable audio."""
    try:
        # Imported here so libsndfile only loads once a filter needs it
        import soundfile

        info = soundfile.info(io.BytesIO(data))
    except ImportError, OSError, RuntimeError, TypeError, ValueError:
        # Not audio, or a format libsndfile cannot read
        return None
    duration = getattr(info, 'duration', None)
    return float(duration) if isinstance(duration, int | float) and duration > 0 else None


def choose_route(routes: Iterable[FilteredRoute], original: bytes) -> FilteredRoute | None:
    """Return the first route whose filters the original passes, in rule order."""
    routes = tuple(routes)
    duration: float | None = None
    if any(route.filters.needs_duration for route in routes):
        duration = audio_duration_seconds(original)
    return next((route for route in routes if route.filters.matches(len(original), duration)), None)


def describe(filters: Mapping[str, object] | None) -> str:
    """Compact, language-neutral summary such as ``0-3s, <=200KB``."""
    parsed = ReplacementFilter.from_config(filters)
    if parsed is None:
        return ''

    def _bounds(low: float | None, high: float | None, unit: str) -> str:
        def _n(value: float) -> str:
            return f'{value:g}'

        if low is not None and high is not None:
            return f'{_n(low)}-{_n(high)}{unit}'
        if low is not None:
            return f'>={_n(low)}{unit}'
        return f'<={_n(high)}{unit}' if high is not None else ''

    parts = (
        _bounds(parsed.min_seconds, parsed.max_seconds, 's'),
        _bounds(parsed.min_kb, parsed.max_kb, 'KB'),
    )
    return ', '.join(part for part in parts if part)
