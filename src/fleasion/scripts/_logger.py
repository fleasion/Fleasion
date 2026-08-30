"""Colored console logging for Fleasion scripts."""

from __future__ import annotations

import logging
import traceback
from types import TracebackType  # ruff: ignore[typing-only-standard-library-import]
from typing import TYPE_CHECKING

from colorama import Back, Fore, Style, just_fix_windows_console

if TYPE_CHECKING:
    from typing import ClassVar

    def _format_exception_lines(
        exception_type: type[BaseException],
        exception: BaseException,
        exception_traceback: TracebackType | None,
    ) -> list[str]: ...
else:

    def _format_exception_lines(
        exception_type: type[BaseException],
        exception: BaseException,
        exception_traceback: TracebackType | None,
    ) -> list[str]:
        return traceback.format_exception(
            exception_type,
            exception,
            exception_traceback,
            colorize=True,
        )


class ColorFormatter(logging.Formatter):
    """Format script log records with level-specific terminal colors."""

    LEVEL_COLORS: ClassVar[list[tuple[int, str]]] = [
        (logging.DEBUG, Fore.LIGHTBLACK_EX),
        (logging.INFO, Fore.LIGHTBLUE_EX),
        (logging.WARNING, Fore.YELLOW),
        (logging.ERROR, Fore.RED),
        (logging.CRITICAL, Back.RED),
    ]

    FORMATS: ClassVar[dict[int, logging.Formatter]] = {
        level: logging.Formatter(
            f'{Fore.LIGHTBLACK_EX}%(asctime)s,%(msecs)03d{Style.RESET_ALL} '
            f'{color}%(levelname)s{Style.RESET_ALL} '
            f'{Fore.MAGENTA}%(name)s{Style.RESET_ALL} '
            '%(message)s',
            '%H:%M:%S',
        )
        for level, color in LEVEL_COLORS
    }

    @staticmethod
    def _format_exception(
        exc_info: tuple[type[BaseException] | None, BaseException | None, TracebackType | None],
    ) -> str | None:
        exception_type, exception, exception_traceback = exc_info
        if exception_type is None or exception is None:
            return None

        return ''.join(
            _format_exception_lines(exception_type, exception, exception_traceback)
        ).rstrip('\n')

    def format(self, record: logging.LogRecord) -> str:
        formatter = self.FORMATS.get(record.levelno, self.FORMATS[logging.DEBUG])

        if record.exc_info:
            record.exc_text = self._format_exception(record.exc_info)

        output = formatter.format(record)
        record.exc_text = None
        return output


def setup_script_logging() -> None:
    """Configure the root logger with Fleasion's colored stream handler."""
    just_fix_windows_console()
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(ColorFormatter())
    logging.basicConfig(
        level=logging.INFO,
        handlers=[stream_handler],
        force=True,
    )
