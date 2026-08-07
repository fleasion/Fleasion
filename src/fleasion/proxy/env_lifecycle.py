"""Serialized Roblox Player lifecycle for explicit Env Proxy mode."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from ..utils.logging import log_buffer


def _is_gdk_repair_path(path: Path) -> bool:
    """Avoid importing Windows-only process helpers on non-Windows builds."""
    try:
        from ..utils.platform_windows import is_roblox_gdk_exe_path

        return is_roblox_gdk_exe_path(path)
    except (ImportError, OSError):
        return False


class EnvProxyLifecycleController:
    """Own Env conversion, bounded CA repair relaunches, and ordered exit."""

    def __init__(
        self,
        *,
        config_manager,
        proxy_master,
        resolve_player_exe: Callable[[], Path | None],
        relaunch_player: Callable[[str, str | None, bool, threading.Event], bool],
        is_player_running: Callable[[], bool],
        get_player_identity: Callable[[], object | None],
        terminate_player: Callable[[], bool],
        wait_for_player_exit: Callable[[float], bool],
        adopted_player: bool = False,
        max_repairs: int = 2,
    ) -> None:
        self._config = config_manager
        self._proxy_master = proxy_master
        self._resolve_player_exe = resolve_player_exe
        self._relaunch_player = relaunch_player
        self._is_player_running = is_player_running
        self._get_player_identity = get_player_identity
        self._terminate_player = terminate_player
        self._wait_for_player_exit = wait_for_player_exit
        self._max_repairs = max(0, int(max_repairs))
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._owned_player_identity = (
            get_player_identity() if adopted_player and is_player_running() else None
        )
        self._intentional_exit_window: tuple[float, float | None] | None = None
        self._shutting_down = False

    @property
    def owns_player(self) -> bool:
        with self._state_lock:
            return self._owned_player_identity is not None

    @property
    def operation_in_progress(self) -> bool:
        return self._operation_lock.locked()

    def consume_intentional_player_exit(self, observed_at: float | None = None) -> bool:
        """Classify a sampled down edge against the exact relaunch window."""
        sample_time = time.monotonic() if observed_at is None else observed_at
        with self._state_lock:
            window = self._intentional_exit_window
            if window is None:
                return False
            started_at, finished_at = window
            intentional = started_at <= sample_time and (
                finished_at is None or sample_time <= finished_at
            )
            if intentional:
                self._intentional_exit_window = None
            return intentional

    def note_unexpected_player_exit(self) -> None:
        with self._state_lock:
            self._owned_player_identity = None

    def _mark_intentional_relaunch(self) -> None:
        with self._state_lock:
            self._intentional_exit_window = (time.monotonic(), None)

    def _finish_intentional_relaunch(self) -> None:
        with self._state_lock:
            window = self._intentional_exit_window
            if window is not None:
                self._intentional_exit_window = (window[0], time.monotonic())

    def _mark_owned(self, owned: bool) -> None:
        identity = self._get_player_identity() if owned else None
        with self._state_lock:
            self._owned_player_identity = identity

    def _owned_identity(self) -> object | None:
        with self._state_lock:
            return self._owned_player_identity

    def _enabled(self) -> bool:
        return (
            not self._shutting_down
            and not self._cancel_event.is_set()
            and bool(getattr(self._config, 'proxy_features_enabled', False))
            and getattr(self._config, 'proxy_mode', 'hosts') == 'env'
        )

    def _prepare_custom_fflags_for_relaunch(self) -> None:
        prepare = getattr(
            self._proxy_master,
            'prepare_custom_fflags_for_player_launch',
            None,
        )
        if callable(prepare):
            prepare()

    def handle_player_launch(
        self,
        exe_path: Path | None = None,
        launch_target: str | None = None,
    ) -> bool:
        """Convert one Player launch and keep its CA healthy for ten seconds."""
        if not self._enabled() or not self._operation_lock.acquire(blocking=False):
            return False
        try:
            if not self._proxy_master.wait_for_env_proxy_ready(timeout=15.0):
                log_buffer.log('Launcher', 'Env Proxy launch skipped: proxy did not become ready')
                return False

            current_exe = self._resolve_player_exe() or exe_path
            if current_exe is None:
                log_buffer.log('Launcher', 'Env Proxy launch skipped: Player path is unavailable')
                return False

            prepared = self._proxy_master.ensure_env_proxy_roblox_ca(
                Path(current_exe), settle=self._is_player_running()
            )
            if not prepared.get('success'):
                log_buffer.log(
                    'Certificate',
                    f'Env Proxy launch stopped before relaunch: {prepared.get("path") or "cacert.pem"}: '
                    f'{prepared.get("error") or "verification failed"}',
                )
                return False

            if not self._enabled():
                return False
            self._prepare_custom_fflags_for_relaunch()
            self._mark_intentional_relaunch()
            try:
                relaunched = self._relaunch_player(
                    self._proxy_master.roblox_env_proxy_url(),
                    launch_target,
                    False,
                    self._cancel_event,
                )
            finally:
                self._finish_intentional_relaunch()
            if not relaunched:
                return False
            log_buffer.log(
                'Certificate',
                'Env Proxy Player relaunch handoff returned; assigning ownership',
            )
            self._mark_owned(True)
            log_buffer.log(
                'Certificate',
                'Env Proxy Player ownership assigned; CA monitoring started',
            )

            return self._monitor_owned_player(
                Path(current_exe), relaunch_on_repair=True, launch_target=launch_target
            )
        except Exception as exc:
            log_buffer.log(
                'Launcher',
                f'Env Proxy Player lifecycle failed: {type(exc).__name__}: {exc}',
            )
            return False
        finally:
            self._operation_lock.release()

    def handle_adopted_player_launch(self, exe_path: Path | None = None) -> bool:
        """Monitor a package-activated Player without synthetic relaunch."""
        if not self._enabled() or not self._operation_lock.acquire(blocking=False):
            return False
        try:
            if not self._proxy_master.wait_for_env_proxy_ready(timeout=15.0):
                log_buffer.log('Launcher', 'Env Proxy adopted launch skipped: proxy did not become ready')
                return False

            current_exe = self._resolve_player_exe() or exe_path
            if current_exe is None:
                log_buffer.log('Launcher', 'Env Proxy adopted launch skipped: Player path is unavailable')
                return False

            prepared = self._proxy_master.ensure_env_proxy_roblox_ca(
                Path(current_exe), settle=self._is_player_running()
            )
            if not prepared.get('success'):
                log_buffer.log(
                    'Certificate',
                    f'Env Proxy adopted launch stopped before monitoring: '
                    f'{prepared.get("path") or "cacert.pem"}: '
                    f'{prepared.get("error") or "verification failed"}',
                )
                return False
            if not self._enabled():
                return False

            self._mark_owned(True)
            log_buffer.log(
                'Certificate',
                'Env Proxy adopted Player ownership assigned; CA monitoring started',
            )
            # The initial Xbox/GDK package activation already supplied the
            # environment, so it must not be synthetically relaunched. If the
            # package overwrites cacert.pem later, however, an in-place write
            # loses immediately; repair through package-aware activation so
            # the next process starts with the prepared CA.
            gdk_repair_relaunch = _is_gdk_repair_path(Path(current_exe))
            return self._monitor_owned_player(
                Path(current_exe), relaunch_on_repair=gdk_repair_relaunch
            )

        except Exception as exc:
            log_buffer.log(
                'Launcher',
                f'Env Proxy adopted Player lifecycle failed: {type(exc).__name__}: {exc}',
            )
            return False
        finally:
            self._operation_lock.release()

    def _monitor_owned_player(
        self,
        current_exe: Path,
        *,
        relaunch_on_repair: bool,
        launch_target: str | None = None,
    ) -> bool:
        """Monitor an owned Player and apply the bounded CA repair policy."""
        for repair_number in range(self._max_repairs + 1):
            if not self._enabled():
                return False
            current_exe = self._resolve_player_exe() or current_exe
            health = self._proxy_master.monitor_env_proxy_roblox_ca(
                Path(current_exe), self._cancel_event
            )
            if health.get('success'):
                # Refresh the token after startup settles. Sober in particular
                # transitions from its launcher to a long-lived engine process.
                self._mark_owned(True)
                log_buffer.log(
                    'Certificate',
                    'Env Proxy Player cacert.pem remained healthy through launch',
                )
                return True
            if health.get('cancelled'):
                return False
            if repair_number >= self._max_repairs:
                log_buffer.log(
                    'Certificate',
                    f'Env Proxy CA repair stopped after {self._max_repairs} relaunches: '
                    f'{health.get("path") or "cacert.pem"}: '
                    f'{health.get("error") or "verification failed"}',
                )
                self._terminate_owned_player()
                return False

            repaired = self._proxy_master.ensure_env_proxy_roblox_ca(
                Path(current_exe), settle=False
            )
            if not repaired.get('success'):
                log_buffer.log(
                    'Certificate',
                    f'Env Proxy CA repair failed: {repaired.get("path") or "cacert.pem"}: '
                    f'{repaired.get("error") or "verification failed"}',
                )
                self._terminate_owned_player()
                return False
            if not self._enabled():
                return False
            if not relaunch_on_repair:
                self._mark_owned(True)
                log_buffer.log(
                    'Certificate',
                    f'Env Proxy adopted Player CA repair {repair_number + 1}/{self._max_repairs} applied',
                )
                continue

            self._prepare_custom_fflags_for_relaunch()
            self._mark_intentional_relaunch()
            try:
                relaunched = self._relaunch_player(
                    self._proxy_master.roblox_env_proxy_url(),
                    launch_target,
                    True,
                    self._cancel_event,
                )
            finally:
                self._finish_intentional_relaunch()
            if not relaunched:
                log_buffer.log('Launcher', 'Env Proxy CA repair relaunch failed')
                return False
            self._mark_owned(True)
            log_buffer.log(
                'Certificate',
                f'Env Proxy CA repair relaunch {repair_number + 1}/{self._max_repairs} completed',
            )
        return False

    def cancel(self) -> None:
        with self._state_lock:
            self._shutting_down = True
        self._cancel_event.set()

    def _terminate_owned_player(self) -> bool:
        owned_identity = self._owned_identity()
        current_identity = self._get_player_identity()
        if owned_identity is None or current_identity != owned_identity:
            if current_identity is not None:
                log_buffer.log(
                    'Launcher',
                    'Env Proxy Player ownership changed; leaving the current process untouched',
                )
            self._mark_owned(False)
            return True
        self._terminate_player()
        exited = self._wait_for_player_exit(5.0)
        if exited:
            self._mark_owned(False)
        return bool(exited)

    def close_owned_player_for_exit(self, *, timeout: float = 20.0) -> bool:
        """Cancel launch work, then close only a Player this controller owns."""
        self.cancel()
        acquired = self._operation_lock.acquire(timeout=max(0.0, timeout))
        if not acquired:
            log_buffer.log('Launcher', 'Timed out waiting for Env Proxy launch work to stop')
            return False
        try:
            return self._terminate_owned_player()
        finally:
            self._operation_lock.release()

    def preserve_owned_player_for_restart(self) -> bool:
        """Cancel work and report whether the next Fleasion can adopt Player."""
        self.cancel()
        deadline = time.monotonic() + 8.0
        while self._operation_lock.locked() and time.monotonic() < deadline:
            time.sleep(0.05)
        owned_identity = self._owned_identity()
        return owned_identity is not None and self._get_player_identity() == owned_identity
