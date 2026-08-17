"""Account-scoped Roblox Player launch orchestration."""

from __future__ import annotations

import random
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from ..utils.windows import launch_as_standard_user
from .account_launch_target import (
    AccountLaunchCancelled,
    AccountLaunchRequest,
    CancelEvent,
    ResolvedLaunchRequest,
    RobloxTarget,
    normalize_job_id,
    parse_account_launch_request,
    parse_roblox_target,
    raise_if_cancelled,
)
from .roblox_account_client import RobloxAccountClient

LaunchCallable = Callable[[str], bool]


class JoinCoordinator(Protocol):
    """Join interception surface required by account launches."""

    def prepare(
        self,
        place_id: str,
        root_place_id: str,
        job_id: str,
        cookie: str | None,
    ) -> bool: ...

    def cancel(self) -> None: ...


class AccountLauncher:
    """Launch Roblox with an account ticket and an optional resolved target."""

    def __init__(
        self,
        client: RobloxAccountClient | None = None,
        launcher: LaunchCallable | None = None,
    ) -> None:
        self._client = client or RobloxAccountClient()
        self._launcher = launcher or launch_as_standard_user

    def launch_request(
        self,
        cookie: str,
        request: AccountLaunchRequest,
        *,
        join_coordinator: JoinCoordinator | None = None,
        cancel_event: CancelEvent | None = None,
    ) -> bool:
        resolved = self._client.resolve_launch_request(cookie, request, cancel_event)
        raise_if_cancelled(cancel_event)
        access_code = ''
        if resolved.is_private_server:
            access_code = self._client.private_server_access_code(
                cookie,
                resolved.root_place_id,
                resolved.link_code,
                cancel_event,
            )
        raise_if_cancelled(cancel_event)
        ticket = self._client.authentication_ticket(cookie)
        raise_if_cancelled(cancel_event)

        armed = False
        if join_coordinator is not None and (
            resolved.is_distinct_subplace or bool(resolved.job_id)
        ):
            join_coordinator.prepare(
                resolved.launch_place_id,
                resolved.root_place_id,
                '' if resolved.is_private_server else resolved.job_id,
                cookie,
            )
            armed = True
        try:
            raise_if_cancelled(cancel_event)
            target = build_ticket_uri(
                ticket,
                place_id=resolved.launch_place_id,
                job_id=resolved.job_id,
                access_code=access_code,
                link_code=resolved.link_code,
            )
            launched = self._launcher(target)
        except Exception:
            if armed and join_coordinator is not None:
                join_coordinator.cancel()
            raise
        if not launched and armed and join_coordinator is not None:
            join_coordinator.cancel()
        return launched

    def launch(
        self,
        cookie: str,
        target: str = '',
        job_id: str = '',
        subplace: str = '',
        *,
        join_coordinator: JoinCoordinator | None = None,
        cancel_event: CancelEvent | None = None,
    ) -> bool:
        request = parse_account_launch_request(target, job_id, subplace)
        return self.launch_request(
            cookie,
            request,
            join_coordinator=join_coordinator,
            cancel_event=cancel_event,
        )


def build_ticket_uri(
    ticket: str,
    place_id: str = '',
    job_id: str = '',
    *,
    access_code: str = '',
    link_code: str = '',
) -> str:
    """Build a deterministic-shape Roblox Player authentication URI."""
    launch_mode = 'play' if place_id else 'app'
    pieces = [
        'roblox-player:1',
        f'launchmode:{launch_mode}',
        f'gameinfo:{ticket}',
        f'launchtime:{int(time.time() * 1000)}',
    ]
    if place_id:
        tracker_id = random.randint(10_000_000_000, 99_999_999_999)
        request_type = (
            'RequestPrivateGame' if link_code else 'RequestGameJob' if job_id else 'RequestGame'
        )
        params: dict[str, Any] = {
            'request': request_type,
            'browserTrackerId': tracker_id,
            'placeId': place_id,
            'joinAttemptId': str(uuid.uuid4()),
        }
        if job_id:
            params['gameId'] = job_id
        if access_code:
            params['accessCode'] = access_code
        if link_code:
            params['linkCode'] = link_code
        launcher_url = 'https://www.roblox.com/Game/PlaceLauncher.ashx?' + urlencode(params)
        pieces.extend(
            [
                f'placelauncherurl:{quote(launcher_url, safe="")}',
                f'browsertrackerid:{tracker_id}',
            ]
        )
    pieces.extend(['robloxLocale:en_us', 'gameLocale:en_us', 'channel:', 'LaunchExp:InApp'])
    return '+'.join(pieces)


__all__ = [
    'AccountLaunchCancelled',
    'AccountLaunchRequest',
    'AccountLauncher',
    'ResolvedLaunchRequest',
    'RobloxAccountClient',
    'RobloxTarget',
    'build_ticket_uri',
    'normalize_job_id',
    'parse_account_launch_request',
    'parse_roblox_target',
]
