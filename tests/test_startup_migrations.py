from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fleasion.startup_migrations import (
    prepare_env_proxy_migration,
    restore_read_only_guard_state,
)


def test_env_proxy_migration_moves_existing_install_to_env_without_acknowledging() -> None:
    config = SimpleNamespace(
        proxy_mode='hosts',
        first_time_setup_complete=True,
        env_proxy_migration_v1_complete=False,
    )

    assert prepare_env_proxy_migration(config) is True
    assert config.proxy_mode == 'env'
    assert config.env_proxy_migration_v1_complete is False


def test_env_proxy_migration_uses_first_run_ui_for_new_install() -> None:
    config = SimpleNamespace(
        proxy_mode='hosts',
        first_time_setup_complete=False,
        env_proxy_migration_v1_complete=False,
    )

    assert prepare_env_proxy_migration(config) is False
    assert config.proxy_mode == 'env'


def test_completed_env_proxy_migration_preserves_explicit_hosts_choice() -> None:
    config = SimpleNamespace(
        proxy_mode='hosts',
        first_time_setup_complete=True,
        env_proxy_migration_v1_complete=True,
    )

    assert prepare_env_proxy_migration(config) is False
    assert config.proxy_mode == 'hosts'


def test_first_read_only_migration_clears_legacy_cacert_and_marks_complete(tmp_path: Path) -> None:
    calls: list[tuple[list[Path], bool]] = []
    config = SimpleNamespace(
        lock_roblox_files_read_only=False,
        read_only_lock_migration_v1_complete=False,
    )
    manager = SimpleNamespace(
        roblox_dirs=[tmp_path / 'RobloxA', tmp_path / 'RobloxB'],
        clear_managed_file_read_only=lambda paths=(), *, clear_untracked=False: calls.append(
            (list(paths), clear_untracked)
        ),
    )

    restore_read_only_guard_state(config, manager)

    assert calls == [
        (
            [
                tmp_path / 'RobloxA' / 'ssl' / 'cacert.pem',
                tmp_path / 'RobloxB' / 'ssl' / 'cacert.pem',
            ],
            True,
        )
    ]
    assert config.read_only_lock_migration_v1_complete is True


def test_completed_read_only_migration_only_restores_tracked_files() -> None:
    calls: list[tuple[list[Path], bool]] = []
    config = SimpleNamespace(
        lock_roblox_files_read_only=False,
        read_only_lock_migration_v1_complete=True,
    )
    manager = SimpleNamespace(
        roblox_dirs=[],
        clear_managed_file_read_only=lambda paths=(), *, clear_untracked=False: calls.append(
            (list(paths), clear_untracked)
        ),
    )

    restore_read_only_guard_state(config, manager)

    assert calls == [([], False)]


def test_enabled_read_only_guard_defers_cleanup() -> None:
    calls: list[object] = []
    config = SimpleNamespace(
        lock_roblox_files_read_only=True,
        read_only_lock_migration_v1_complete=False,
    )
    manager = SimpleNamespace(
        roblox_dirs=[],
        clear_managed_file_read_only=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    restore_read_only_guard_state(config, manager)

    assert calls == []
    assert config.read_only_lock_migration_v1_complete is False
