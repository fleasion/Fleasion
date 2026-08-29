"""Runtime-neutral startup Roblox-auth diagnostics for desktop frontends."""

from __future__ import annotations

import html
import sys
from collections.abc import Mapping
from typing import Any

from .localization import tr
from .utils import APP_DISCORD
from .utils.roblox_auth import windows_auth_profile_matches_username


def build_auth_warning(
    details: Mapping[str, object], *, platform_name: str | None = None
) -> dict[str, Any]:
    """Build the translated warning shown when no readable Roblox login token is available."""
    platform_name = platform_name or sys.platform
    attempted = details.get('attempted_paths') or []
    existing = details.get('existing_paths') or []
    if not isinstance(attempted, list):
        attempted = []
    if not isinstance(existing, list):
        existing = []

    existing_html = ''
    if existing:
        existing_html = tr(
            'app.auth_warning.existing_files',
            paths='<br>'.join(html.escape(str(path)) for path in existing[:8]),
        )

    skipped_token = bool(details.get('user_skipped_token'))
    unknown = tr('app.common.unknown')
    if platform_name == 'darwin':
        diagnostics_html = tr(
            'app.auth_warning.macos_diagnostics',
            home=html.escape(str(details.get('home') or unknown)),
            local_appdata=html.escape(str(details.get('local_appdata') or unknown)),
            default_cookie_path=html.escape(str(details.get('default_cookie_path') or unknown)),
            attempted_count=len(attempted),
        )
        most_likely_html = tr(
            'app.auth_warning.macos_guidance',
            lead=(
                tr('app.auth_warning.macos_skipped')
                if skipped_token
                else tr('app.auth_warning.macos_none')
            ),
        )
    elif platform_name.startswith('linux'):
        diagnostics_html = tr(
            'app.auth_warning.linux_diagnostics',
            home=html.escape(str(details.get('home') or unknown)),
            local_appdata=html.escape(str(details.get('local_appdata') or unknown)),
            default_cookie_path=html.escape(str(details.get('default_cookie_path') or unknown)),
            attempted_count=len(attempted),
        )
        most_likely_html = tr(
            'app.auth_warning.linux_guidance',
            lead=(
                tr('app.auth_warning.linux_skipped')
                if skipped_token
                else tr('app.auth_warning.linux_none')
            ),
        )
    else:
        diagnostics_html = tr(
            'app.auth_warning.windows_diagnostics',
            username=html.escape(str(details.get('username') or unknown)),
            userprofile=html.escape(str(details.get('userprofile') or unknown)),
            local_appdata=html.escape(str(details.get('local_appdata') or unknown)),
            default_cookie_path=html.escape(str(details.get('default_cookie_path') or unknown)),
            attempted_count=len(attempted),
        )
        most_likely_html = tr(
            'app.auth_warning.windows_same_user_guidance'
            if windows_auth_profile_matches_username(details)
            else 'app.auth_warning.windows_guidance'
        )

    discord_url = (
        APP_DISCORD if APP_DISCORD.startswith(('http://', 'https://')) else f'https://{APP_DISCORD}'
    )
    detail = tr(
        'app.auth_warning.info_skipped' if skipped_token else 'app.auth_warning.info_unreadable',
        most_likely_html=most_likely_html,
        existing_html=existing_html,
        diagnostics_html=diagnostics_html,
        discord_url=html.escape(discord_url),
        discord_label=html.escape(APP_DISCORD),
    )
    return {
        'title': tr('app.roblox_token_not_readable'),
        'message': (
            tr('app.fleasion_is_continuing_without_a_roblox_login')
            if skipped_token
            else tr('app.fleasion_could_not_read_a_usable_roblox')
        ),
        'detail': detail,
        'continue_text': tr('app.ok'),
        'login_text': (
            tr('app.roblox_login_source')
            if platform_name == 'darwin'
            else tr('app.open_roblox_login')
        ),
        'exit_text': tr('app.exit_fleasion'),
        'can_open_login': platform_name == 'darwin' or platform_name.startswith('linux'),
    }
