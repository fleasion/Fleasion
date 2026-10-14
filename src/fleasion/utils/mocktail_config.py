"""Minimal, comment-preserving edits to Mocktail's ``config.yaml``.

Mocktail ships its own Fleasion integration (``integrations.fleasion``): when
enabled it routes itself through ``127.0.0.1:<proxy_port>`` and builds a CA
bundle that trusts Fleasion's certificate. Environment values take precedence
over the config file. Fleasion keeps both sources consistent while armed and
restores the original settings when it disarms the client.

Mocktail generates the file with explanatory comments and promises never to
replace the user's edits, so this module changes only the integration keys it owns
and leaves every other line byte-for-byte intact. It understands just the
block-style mappings Mocktail writes; anything it cannot place safely is
reported as an error instead of being rewritten.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

INTEGRATION_KEYS = ('enabled', 'proxy_mode', 'proxy_port', 'ca_certificate')
_QUOTED_SCALAR_MIN_LENGTH = 2

_KEY_LINE = re.compile(r'^(?P<indent>[ ]*)(?P<key>[A-Za-z0-9_]+):(?P<rest>.*)$')


class MocktailConfigError(ValueError):
    """Raised when the config cannot be edited without guessing."""


@dataclass(frozen=True, slots=True)
class _Line:
    indent: int
    key: str | None
    value: str


def _parse(line: str) -> _Line:
    stripped = line.strip()
    if not stripped or stripped.startswith('#'):
        return _Line(-1, None, '')
    if '\t' in line[: len(line) - len(line.lstrip())]:
        msg = 'tab indentation is not supported'
        raise MocktailConfigError(msg)
    match = _KEY_LINE.match(line.rstrip('\n'))
    if match is None:
        return _Line(len(line) - len(line.lstrip(' ')), None, stripped)
    return _Line(len(match['indent']), match['key'], _strip_comment(match['rest']).strip())


def _strip_comment(value: str) -> str:
    """Drop a trailing `` # comment`` outside quotes."""
    quote = ''
    for index, char in enumerate(value):
        if quote:
            if char == quote:
                quote = ''
        elif char in '"\'':
            quote = char
        elif char == '#' and (index == 0 or value[index - 1] in ' \t'):
            return value[:index]
    return value


def _block_end(lines: list[str], start: int, indent: int) -> int:
    """Index just past the mapping that opens at ``lines[start]``."""
    end = start + 1
    for index in range(start + 1, len(lines)):
        parsed = _parse(lines[index])
        if parsed.indent == -1:
            continue
        if parsed.indent <= indent:
            break
        end = index + 1
    return end


def _find_key(lines: list[str], key: str, start: int, end: int, indent: int) -> int | None:
    for index in range(start, end):
        parsed = _parse(lines[index])
        if parsed.key == key and parsed.indent == indent:
            return index
    return None


def _child_indent(lines: list[str], start: int, end: int, parent_indent: int) -> int:
    for index in range(start + 1, end):
        parsed = _parse(lines[index])
        if parsed.indent > parent_indent:
            return parsed.indent
    return parent_indent + 2


def read_section_value(text: str, section: str, key: str) -> str | None:
    """Return the raw scalar at top-level ``section.key`` (``None`` when absent)."""
    lines = text.splitlines(keepends=True)
    top = _find_key(lines, section, 0, len(lines), 0)
    if top is None:
        return None
    top_end = _block_end(lines, top, 0)
    child = _child_indent(lines, top, top_end, 0)
    index = _find_key(lines, key, top + 1, top_end, child)
    return _parse(lines[index]).value if index is not None else None


def _unquote(value: str | None) -> str | None:
    if (
        value is not None
        and len(value) >= _QUOTED_SCALAR_MIN_LENGTH
        and value[0] == value[-1]
        and value[0] in '"\''
    ):
        return value[1:-1]
    return value


def conflicting_network_setting(text: str, proxy_port: int | None) -> str | None:
    """Name the ``network`` setting that Mocktail rejects alongside Fleasion.

    Mocktail refuses to start when its Fleasion integration is enabled together
    with ``use_system_proxy``, or with a fixed proxy other than Fleasion's own
    ``127.0.0.1:<proxy_port>`` (``proxy_port`` is ``None`` in hosts mode, where
    any fixed proxy conflicts).
    """
    use_system = _unquote(read_section_value(text, 'network', 'use_system_proxy'))
    if use_system in {'true', '1', 'on'}:
        return 'network.use_system_proxy'
    host = _unquote(read_section_value(text, 'network', 'proxy_host'))
    if host is None:
        return None
    port = _unquote(read_section_value(text, 'network', 'proxy_port'))
    if proxy_port is not None and host == '127.0.0.1' and port == str(proxy_port):
        return None
    return 'network.proxy_host'


def read_integration(text: str) -> dict[str, str | None]:
    """Return the raw ``integrations.fleasion`` values (``None`` when absent)."""
    values: dict[str, str | None] = dict.fromkeys(INTEGRATION_KEYS)
    lines = text.splitlines(keepends=True)
    top = _find_key(lines, 'integrations', 0, len(lines), 0)
    if top is None:
        return values
    top_end = _block_end(lines, top, 0)
    child = _child_indent(lines, top, top_end, 0)
    block = _find_key(lines, 'fleasion', top + 1, top_end, child)
    if block is None:
        return values
    block_end = _block_end(lines, block, child)
    key_indent = _child_indent(lines, block, block_end, child)
    for key in INTEGRATION_KEYS:
        index = _find_key(lines, key, block + 1, block_end, key_indent)
        if index is not None:
            values[key] = _parse(lines[index]).value
    return values


def read_certificate_path(text: str) -> str | None:
    value = read_integration(text)['ca_certificate']
    if value is None:
        return None
    if value.startswith('"'):
        parsed: object = json.loads(value)
        if not isinstance(parsed, str):
            msg = 'Mocktail certificate path must be a string'
            raise MocktailConfigError(msg)
        return parsed
    return _unquote(value)


def _update_keys(lines: list[str], block: int, child: int, updates: dict[str, str | None]) -> None:
    block_end = _block_end(lines, block, child)
    key_indent = _child_indent(lines, block, block_end, child)
    pad = ' ' * key_indent
    insert_at = block + 1
    for key, value in updates.items():
        index = _find_key(lines, key, block + 1, block_end, key_indent)
        if index is None:
            if value is not None:
                lines.insert(insert_at, f'{pad}{key}: {value}\n')
                insert_at += 1
                block_end += 1
        elif value is None:
            del lines[index]
            block_end -= 1
        else:
            scalar = lines[index].rstrip('\n').split(':', 1)[1]
            stripped = _strip_comment(scalar)
            comment = scalar[len(stripped) :]
            spacing = stripped[len(stripped.rstrip()) :] if comment else ''
            lines[index] = f'{pad}{key}: {value}{spacing}{comment}\n'


def _remove_empty_block(lines: list[str], start: int, indent: int) -> None:
    end = _block_end(lines, start, indent)
    if all(_parse(line).indent == -1 for line in lines[start + 1 : end]):
        del lines[start]


def set_integration(text: str, updates: dict[str, str | None]) -> str:
    """Set (or, for ``None``, remove) keys under ``integrations.fleasion``.

    ``updates`` values are written verbatim as YAML scalars.
    """
    unknown = set(updates) - set(INTEGRATION_KEYS)
    if unknown:
        msg = f'unsupported keys: {sorted(unknown)}'
        raise MocktailConfigError(msg)
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
    for line in lines:
        _parse(line)  # Validate indentation before touching anything

    top = _find_key(lines, 'integrations', 0, len(lines), 0)
    if top is None:
        additions = {key: value for key, value in updates.items() if value is not None}
        if not additions:
            return ''.join(lines)
        lines.append('integrations:\n  fleasion:\n')
        lines.extend(f'    {key}: {value}\n' for key, value in additions.items())
        return ''.join(lines)
    if _parse(lines[top]).value:
        msg = 'integrations is not a block mapping'
        raise MocktailConfigError(msg)

    top_end = _block_end(lines, top, 0)
    child = _child_indent(lines, top, top_end, 0)
    block = _find_key(lines, 'fleasion', top + 1, top_end, child)
    if block is None:
        additions = {key: value for key, value in updates.items() if value is not None}
        if not additions:
            return ''.join(lines)
        pad = ' ' * child
        new = [f'{pad}fleasion:\n']
        new.extend(f'{pad}  {key}: {value}\n' for key, value in additions.items())
        lines[top + 1 : top + 1] = new
        return ''.join(lines)
    if _parse(lines[block]).value:
        msg = 'integrations.fleasion is not a block mapping'
        raise MocktailConfigError(msg)

    _update_keys(lines, block, child, updates)
    _remove_empty_block(lines, block, child)
    _remove_empty_block(lines, top, 0)
    return ''.join(lines)
