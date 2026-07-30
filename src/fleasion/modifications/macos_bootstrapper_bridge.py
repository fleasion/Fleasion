"""Launch-time coordination with macOS Roblox bootstrappers."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer

from ..utils import log_buffer
from ..utils.platform_macos import (
    APPLEBLOX_DATA_DIR,
    FROSTSTRAP_MOD_BACKUP_DIR,
    FROSTSTRAP_VERSIONS_DIR,
    find_bootstrapper_restore_resource_dirs,
    is_roblox_running,
)
from ..utils.threading import run_in_thread
from .fflag_manager import CLIENT_SETTINGS_REL

_SETTINGS_POLL_INTERVAL_MS = 25
_TOPOLOGY_POLL_INTERVAL_MS = 500
_LAUNCH_GUARD_TIMEOUT_SECONDS = 30.0
_POST_LAUNCH_GUARD_SECONDS = 1.0


class MacBootstrapperBridge(QObject):
    """Keep Fleasion state authoritative across bootstrapper launch rewrites.

    AppleBlox deletes and recreates ``Contents/MacOS/ClientSettings`` immediately
    before starting Roblox. A native directory watcher normally catches that
    rewrite; the short stat poll is a fallback for coalesced FSEvents and must
    run before Roblox consumes early-startup allowlisted flags.

    The slower topology poll tracks transient AppleBlox and Froststrap Resources
    backups so Fleasion modifications are mirrored into files those
    bootstrappers can later restore.
    """

    def __init__(self, modification_manager, parent: QObject | None = None):
        super().__init__(parent)
        self._manager = modification_manager
        self._stopped = False
        self._settings_signatures: dict[Path, tuple[int, int] | None] = {}
        self._restore_signature: tuple[str, ...] = ()
        self._topology_refresh_lock = threading.Lock()
        self._managed_reapply_lock = threading.Lock()
        self._launch_guard_deadline = 0.0
        self._player_seen_at = 0.0
        self._managed_signatures: dict[Path, tuple[int, int] | None] = {}
        self._managed_reapply_passes = 0
        self._internal_reapply_active = False

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)

        self._settings_timer = QTimer(self)
        self._settings_timer.setInterval(_SETTINGS_POLL_INTERVAL_MS)
        self._settings_timer.timeout.connect(self._reconcile_launch_settings)

        self._topology_timer = QTimer(self)
        self._topology_timer.setInterval(_TOPOLOGY_POLL_INTERVAL_MS)
        self._topology_timer.timeout.connect(self._reconcile_topology)

        self._launch_guard_timer = QTimer(self)
        self._launch_guard_timer.setInterval(_SETTINGS_POLL_INTERVAL_MS)
        self._launch_guard_timer.timeout.connect(self._reconcile_managed_files)

        self._sync_watches()
        self._settings_timer.start()
        self._topology_timer.start()
        QTimer.singleShot(0, self._reconcile_launch_settings)
        QTimer.singleShot(0, self._reconcile_topology)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._settings_timer.stop()
        self._topology_timer.stop()
        self._launch_guard_timer.stop()
        watched = self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)

    def _live_macos_directories(self) -> list[Path]:
        directories: list[Path] = []
        for resource_dir in self._manager.roblox_dirs:
            if not (
                resource_dir.name == 'Resources'
                and resource_dir.parent.name == 'Contents'
                and resource_dir.parent.parent.suffix == '.app'
            ):
                continue
            macos_dir = resource_dir.parent / 'MacOS'
            if macos_dir.is_dir():
                directories.append(macos_dir)
        return directories

    def _settings_targets(self) -> list[Path]:
        return [
            macos_dir / CLIENT_SETTINGS_REL
            for macos_dir in self._live_macos_directories()
        ]

    def _directories_to_watch(self) -> set[str]:
        candidates: set[Path] = set(self._live_macos_directories())
        for macos_dir in self._live_macos_directories():
            settings_dir = macos_dir / CLIENT_SETTINGS_REL.parent
            if settings_dir.is_dir():
                candidates.add(settings_dir)

        # Watching the nearest existing ancestors makes newly created/deleted
        # version and restore directories visible without persisting them.
        for path in (
            APPLEBLOX_DATA_DIR,
            APPLEBLOX_DATA_DIR / 'cache',
            APPLEBLOX_DATA_DIR / 'cache' / 'mods',
            FROSTSTRAP_VERSIONS_DIR,
            FROSTSTRAP_MOD_BACKUP_DIR,
        ):
            if path.is_dir():
                candidates.add(path)
        return {str(path) for path in candidates}

    def _sync_watches(self) -> None:
        desired = self._directories_to_watch()
        current = set(self._watcher.directories())
        if removed := sorted(current - desired):
            self._watcher.removePaths(removed)
        if added := sorted(desired - current):
            self._watcher.addPaths(added)

    def _on_directory_changed(self, _path: str = '') -> None:
        if self._stopped:
            return
        # Run immediately, then again after AppleBlox's asynchronous
        # remove/create/write sequence has had a chance to finish.
        self._sync_watches()
        self._reconcile_launch_settings()
        QTimer.singleShot(10, self._reconcile_launch_settings)
        QTimer.singleShot(50, self._reconcile_launch_settings)
        QTimer.singleShot(100, self._reconcile_topology)

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int] | None:
        try:
            stat_result = path.stat()
            return stat_result.st_mtime_ns, stat_result.st_size
        except OSError:
            return None

    def _reconcile_launch_settings(self) -> None:
        if self._stopped:
            return

        targets = self._settings_targets()
        target_set = set(targets)
        for stale in set(self._settings_signatures) - target_set:
            self._settings_signatures.pop(stale, None)

        changed = False
        launch_rewrite = False
        for target in targets:
            signature = self._file_signature(target)
            was_known = target in self._settings_signatures
            if self._settings_signatures.get(target) != signature:
                self._settings_signatures[target] = signature
                changed = changed or signature is not None
                launch_rewrite = launch_rewrite or (
                    was_known
                    and signature is not None
                    and not self._internal_reapply_active
                )

        if changed:
            updated = self._manager.reassert_macos_bootstrapper_fast_flags()
            launch_rewrite = launch_rewrite or bool(updated)
            # Atomic replacement changes the signature once more. Record the
            # resulting state now so the fallback poll does not do extra work.
            for target in targets:
                self._settings_signatures[target] = self._file_signature(target)
            self._sync_watches()
            if launch_rewrite:
                self._start_launch_guard()

    @staticmethod
    def _path_signatures(paths: list[Path]) -> dict[Path, tuple[int, int] | None]:
        return {path: MacBootstrapperBridge._file_signature(path) for path in paths}

    def _start_launch_guard(self) -> None:
        """Guard managed Resources while AppleBlox runs its pre-launch mod pass."""
        self._launch_guard_deadline = time.monotonic() + _LAUNCH_GUARD_TIMEOUT_SECONDS
        self._player_seen_at = time.monotonic() if is_roblox_running() else 0.0
        self._managed_signatures = self._path_signatures(
            self._manager.managed_resource_paths()
        )
        # Two passes avoid accepting an AppleBlox write that races between a
        # Fleasion write and the signature captured at the end of that pass.
        self._managed_reapply_passes = max(self._managed_reapply_passes, 2)
        if not self._launch_guard_timer.isActive():
            self._launch_guard_timer.start()
        if not self._managed_reapply_lock.locked():
            self._trigger_managed_reapply()

    def _reconcile_managed_files(self) -> None:
        if self._stopped:
            return

        now = time.monotonic()
        if now >= self._launch_guard_deadline:
            self._launch_guard_timer.stop()
            return

        if is_roblox_running():
            if not self._player_seen_at:
                self._player_seen_at = now
            elif (
                now - self._player_seen_at >= _POST_LAUNCH_GUARD_SECONDS
                and self._managed_reapply_passes <= 0
                and not self._managed_reapply_lock.locked()
            ):
                self._launch_guard_timer.stop()
                return

        current = self._path_signatures(self._manager.managed_resource_paths())
        if current != self._managed_signatures:
            self._managed_reapply_passes = max(self._managed_reapply_passes, 2)
        if self._managed_reapply_passes > 0 and not self._managed_reapply_lock.locked():
            self._trigger_managed_reapply()

    @run_in_thread
    def _trigger_managed_reapply(self) -> None:
        if not self._managed_reapply_lock.acquire(blocking=False):
            return
        try:
            self._internal_reapply_active = True
            self._manager.reapply_all()
            self._managed_signatures = self._path_signatures(
                self._manager.managed_resource_paths()
            )
            self._managed_reapply_passes = max(self._managed_reapply_passes - 1, 0)
        finally:
            self._internal_reapply_active = False
            self._managed_reapply_lock.release()

    def _reconcile_topology(self) -> None:
        if self._stopped:
            return
        signature = tuple(
            sorted(str(path.resolve()) for path in find_bootstrapper_restore_resource_dirs())
        )
        if signature == self._restore_signature:
            return
        self._restore_signature = signature
        self._refresh_manager_topology()

    @run_in_thread
    def _refresh_manager_topology(self) -> None:
        if not self._topology_refresh_lock.acquire(blocking=False):
            return
        try:
            changed = self._manager.refresh_roblox_dirs(reapply_if_changed=True)
            if changed:
                log_buffer.log(
                    'Modifications',
                    'Synchronized macOS bootstrapper restore snapshots',
                )
        finally:
            self._topology_refresh_lock.release()
