"""Fleasion application command-line interface."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence


type ProxyDebugMode = Literal['a', 'b', 'c', 'd', 'e', 'full']


class ApplicationArguments(argparse.Namespace):
    """Typed command-line arguments for application startup."""

    no_dashboard: bool
    kill_others: bool
    preserve_env_proxy_player: bool
    restart_handoff_token: str | None
    restart_handoff_parent_pid: int | None
    proxy_debug: bool
    proxy_debug_mode: ProxyDebugMode | None
    fleasion_user_localappdata: str | None
    fleasion_requesting_user_sid: str | None
    repair_autostart: bool
    disable_autostart: bool
    repair_roblox_permissions: bool
    repair_firewall: bool
    cleanup_hosts: bool
    fleasion_gdk_debugger: bool
    microprofile: bool
    install_linux_privileged_helper: bool
    linux_helper_promptless: bool


def parse_application_args(arguments: Sequence[str] | None = None) -> ApplicationArguments:
    """Parse application arguments while leaving Qt arguments untouched."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        '--no-dashboard',
        action='store_true',
        help='Suppress dashboard on launch (used by autostart task)',
    )
    parser.add_argument(
        '--kill-others',
        action='store_true',
        help='Kill other Fleasion instances on startup (used when relaunching elevated)',
    )
    parser.add_argument(
        '--preserve-env-proxy-player',
        action='store_true',
        help=argparse.SUPPRESS,
    )
    parser.add_argument('--restart-handoff-token', help=argparse.SUPPRESS)
    parser.add_argument('--restart-handoff-parent-pid', type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        '--proxy-debug', '-proxy-debug', action='store_true', help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--proxy-debug-mode',
        choices=['a', 'b', 'c', 'd', 'e', 'full'],
        help=argparse.SUPPRESS,
    )
    parser.add_argument('--fleasion-user-localappdata', help=argparse.SUPPRESS)
    parser.add_argument('--fleasion-requesting-user-sid', help=argparse.SUPPRESS)
    parser.add_argument('--repair-autostart', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--disable-autostart', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--repair-roblox-permissions', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--repair-firewall', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--cleanup-hosts', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--fleasion-gdk-debugger', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--microprofile', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument(
        '--install-linux-privileged-helper',
        action='store_true',
        help='Install the root-owned Linux proxy helper and Polkit policy, then exit',
    )
    parser.add_argument(
        '--linux-helper-promptless',
        action='store_true',
        help=('Allow active sudo/wheel users to run the Linux proxy helper without future prompts'),
    )

    parsed = ApplicationArguments()
    parser.parse_known_args(arguments, namespace=parsed)
    return parsed
