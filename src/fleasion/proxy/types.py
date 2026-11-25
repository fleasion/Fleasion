"""Shared proxy traffic and interception contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict

if TYPE_CHECKING:
    from .server import ProxyFlow


class AutoReplaceRule(TypedDict, total=False):
    enabled: bool
    direction: str
    host_filter: str
    path_filter: str
    type: str
    match: str
    replacement: str


class ProxyRequestLogEntry(TypedDict):
    id: int
    time: float
    host: str
    port: int
    method: str
    path: str
    intercepted: bool
    status: int | None
    size: int
    ms: int | None
    request_raw: bytes | bytearray | None
    response_raw: bytes | bytearray | None
    pending_stage: str | None
    was_intercepted: bool
    dropped_request: NotRequired[bool]
    dropped_response: NotRequired[bool]


class ModuleInterceptor(Protocol):
    def request(self, flow: ProxyFlow) -> None: ...

    def response(self, flow: ProxyFlow) -> None: ...
