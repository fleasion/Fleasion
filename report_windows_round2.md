READY_FOR_AUTOMATIC_MIGRATION: NO

# Windows handoff round 2

Date: 2026-08-07
Base commit: `bc55605` (`Refactor tests for Windows platform compatibility and enhance autostart functionality`)
Environment: native Windows 11 Pro 10.0.26200, 64-bit, non-administrator process, CPython 3.14.3, native `uv` environment.
Scope: `handoff_windows.md` executed without implementing automatic migration.

Player/Studio inventory: the Xbox/GDK install contains `C:\XboxGames\Roblox\Content\RobloxPlayerBeta.exe` and `Content\ssl\cacert.pem`; the active GDK process resolves to the corresponding `WindowsApps` package executable. Regular `%LOCALAPPDATA%\Roblox\Versions` and Studio roots were also discovered during the earlier handoff run.  
Initial repository state for this continuation: the committed worktree at the base commit above; the focused autostart/GUI/GDK changes below were then tested as an uncommitted diff.
Tested both the source environment and the packaged executable from that diff.
Initial autostart state: no `Fleasion_Autostart` task; the profile was an existing installation with Env Proxy already selected, not a clean profile.

## Gate summary

| Gate | Result | Evidence / remaining limitation |
|---|---|---|
| 1. Native Env proxy startup | PASS | Native Windows pytest passed; packaged `Fleasion-v2.3.0-Windows.exe --no-dashboard` bound `127.0.0.1:58443` and `[::1]:58443`, logged Env mode and passed the explicit TLS self-test for six intercept hosts. Hosts and Fleasion firewall state were unchanged. |
| 2. Player traffic and startup flags | PASS | Native coverage passed. Live Store-entry activation reached the real WindowsApps `RobloxPlayerBeta.exe` with the package-aware Env Proxy block; the user confirmed FFlags, grey-sky, successful Roblox startup, and visible `vagueboypng.png` textures. The earlier no-texture observation was stale GDK asset storage, not a proxy-routing failure. |
| 3. CA overwrite ceiling | PARTIAL | The capped native watcher performed three CA-only injections. The packaged log recorded adopted-player repair `1/2`, repair `2/2`, then `CA repair stopped after 2 relaunches`; no third relaunch loop occurred and unrelated CA content was restored exactly. The GDK activation-preservation guard deliberately leaves the package Player running at the ceiling instead of force-closing it, so the strict “stop the owned Player” clause is not claimed. |
| 4. Locking/update/exit ownership | PARTIAL | Native regression coverage passed. Packaged cleanup was verified after closing the identified Fleasion processes: zero listeners remained on 58443 and settings were restored byte-for-byte. A complete live Player lock/update/close-on-exit sequence was not available. |
| 5. Studio/autostart/ACL/firewall/manual proxy | BLOCKED | The user confirmed Studio opened and published a game with no TLS errors; the packaged task used per-user `Interactive`/`Limited` metadata and Fleasion firewall rules stayed at 0. Live ACL repair and the GUI-only blank-credential manual-proxy timeout/restore path were not exercised, so this required gate remains incomplete. |

## Changes and regression evidence

The changes include Windows-validity guards in 12 platform-specific tests, the normal-user Task Scheduler and mode-aware Run on Boot fixes, package-aware Xbox/GDK Env Proxy activation with a fail-safe fallback, a dashboard-independent Windows custom-FFlag hotkey controller, and the bounded adopted-GDK CA monitor. The production fixes do not implement automatic migration.

Native Windows commands and results:

- `uv run pytest -q` in the native Windows environment — **350 passed, 103 skipped, 15 warnings** in 10.28s.
- Native `uv tool run ruff check src tests` — **All checks passed**.
- `uv run build --clean` — **passed**; PyInstaller produced `dist\\Fleasion-v2.3.0-Windows.exe` (71,339,451 bytes).
- Final package SHA-256: `47F945A1DB72158EAD0868682593E8DC707A8B7670AC0A6582541FF8FF145D5E`.

The build emitted existing non-fatal packaging warnings for optional OpenGL acceleration, `tzdata`, legacy `MSVCR90.dll` references, and macOS-only framework imports. The Windows build completed successfully.

## Packaged smoke evidence

The packaged executable was started in Env mode against the real local Roblox/Fleasion profile after backing up the profile state. The latest log recorded:

- certificate readiness completed;
- CA already installed in the discovered Player/Studio installs;
- `Roblox Env Proxy mode active; skipping privileged relay startup`;
- explicit proxy TLS self-test passed for six intercept hosts;
- `Fleasion Proxy Active`.

The Player certificate count was 1 and Studio certificate count was 2 both before and after; all recorded SHA-256 values were unchanged. The hosts-file hash was unchanged, and Fleasion firewall-rule count stayed at 0. The test settings were restored byte-for-byte from the backup. After the package process was closed, port 58443 had zero loopback listeners.

The final rebuilt executable was started in Env mode and left `127.0.0.1:58443` and `[::1]:58443` listening; its log recorded the six-host TLS self-test and `Fleasion Proxy Active`. The exact smoke processes were then closed and no test instance remained. A live Store-entry GDK run confirmed that the GameLaunchHelper child does not inherit a caller's scoped Env Proxy variables; package-aware activation injected the environment into the actual WindowsApps Player child. The final PID-synchronized CA run waited for the adopted-player marker before injecting, then restored the exact original CA hash after the bounded ceiling test. The user confirmed successful startup, active custom flags, grey sky, and visible local textures. Returning to an older experience reused stale GDK asset storage until the cache was cleared; the proxy logs still showed the replacement KTX2 being served, so this was cache state rather than a routing defect.

## Defects and fixes

### DEFECT-WIN-001: Standard-user autostart registration returned Access Denied

The original Windows path used `schtasks /Create` with an XML task containing an explicit user. On this non-admin Windows account, both that path and a temporary limited `schtasks` task reproduced `Access is denied`. The existing elevated repair then left a task that the ordinary user could query but could not delete or overwrite.

The fix in `src/fleasion/utils/autostart.py` uses the Windows Task Scheduler COM API for normal-user create/update, explicitly setting interactive-token logon and limited run level. The elevated repair keeps the explicit-user XML path. A clean packaged retest with no task present created `Fleasion_Autostart` without UAC; metadata reported `Interactive` and `Limited`, the current package executable, and Env proxy readiness.

### DEFECT-WIN-002: Run on Boot GUI incorrectly implied Administrator was always required

`src/fleasion/tray.py` unconditionally appended `On Windows, ensure Fleasion is running as Administrator.` to the failure dialog. The startup gate itself already correctly skipped elevation in Env mode, so this was stale and misleading UI text.

The fix makes the message mode-aware. Env Proxy says it uses a normal per-user task; Hosts File mode explains that administrator permission applies to proxy startup while the Run on Boot task remains per-user. `src/fleasion/app.py` and `src/fleasion/gui/settings_tab.py` now pass the active mode through autostart synchronization.

### Mode-switch refresh

Autostart metadata now includes `proxy_mode`. Changing Hosts File ↔ Env Proxy forces a task refresh, so a stale elevated/legacy task cannot be silently retained when switching into Env mode. The focused regression test covers the metadata transition; a packaged stale-Hosts-metadata refresh returned to `proxy_mode=env`, kept `Interactive`/`Limited`, and logged no autostart failure. A separate attempt to mark the validation task HighestAvailable was not applied, so no live Highest-to-Limited transition is claimed beyond the source regression and refresh evidence.

### DEFECT-WIN-003: Xbox/GDK package child drops scoped Env Proxy variables

The native XboxGames scan found `Content\\RobloxPlayerBeta.exe` and its CA bundle, but Windows launched the Microsoft Store/Xbox package whose manifest entry point is `GameLaunchHelper.exe`; the active child resolves to the protected `WindowsApps\\...RobloxGDK...\\RobloxPlayerBeta.exe`. The user’s search-bar launch is an AppX launch, not a `roblox-player:` URI. Fleasion first tried direct Player relaunch, then the package helper, but live process inspection showed the resulting Player had no `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, or `FLEASION_PROXY_RELAUNCHED` variables and the proxy received no Roblox traffic.

The fix now arms the documented package-debugging API with a complete proxy environment block before Store activation, activates the manifest AUMID, and supplies Fleasion's own executable as the dummy debugger so Windows resumes the initial suspended thread. The detector recognizes both the protected `WindowsApps\\...RobloxGDK...` path and the linked `C:\\XboxGames\\Roblox\\Content\\RobloxPlayerBeta.exe` path. Once package activation has supplied the environment, the launch detector uses a dedicated adopted-player CA monitor instead of a synthetic relaunch; CA repairs are applied in place, avoiding the fragile bootstrap path associated with the `0x1` failures. Fleasion disarms package debugging during shutdown. Regression tests cover GDK detection, package identity/AUMID derivation, environment-block termination, the no-kill normal GDK path, XboxGames path detection, fallback behavior, cancellation during the startup settle, adopted-player monitoring, and desktop relaunch behavior. The final synchronized three-injection run logged repairs `1/2` and `2/2`, then stopped after the second repair without a third relaunch. Hosts File mode remains the fallback when package-aware activation is unavailable. No matching Windows Application Error event for Roblox, GDK, or `0x1` was present in the last four hours, so the original `0x1` remains confirmed by the user's dialog but not by a Windows crash bucket.

The reported local TexturePack PNG was also traced independently. The live log recorded conversion/cache selection for `vagueboypng.png`, a CDN short-circuit to the generated KTX2, and `HTTP/1.1 200 OK` local serves. The user then confirmed that the texture visibly applies after the stale GDK asset store is cleared; rejoining the older experience reproduced the stale visual because that process reused its cached asset. The unrelated ID-based replacement rules showed separate `401` precheck failures.

### DEFECT-WIN-004: Clear Cache progress window remained open after completion

`src/fleasion/gui/delete_cache.py` appended `Done.` from the completion signal but never closed the modeless dialog. The fix keeps the completion message visible for 500 ms and then calls `QDialog.accept()`. `tests/test_delete_cache_window.py` covers the timer and close callback. The focused native Windows test passed, and the full native suite passed after the fix.

### DEFECT-WIN-005: Global illegal-FFlag keybinds stopped when the dashboard closed

`CustomFFlagEditor` owned the Windows global-hotkey service. Closing the dashboard destroyed that editor, and `--no-dashboard` never created it, so saved illegal-FFlag keybinds had no receiver while Fleasion continued running in the tray.

The fix adds `WindowsCustomFFlagHotkeyController` in `src/fleasion/gui/windows_hotkeys.py`, creates it from the tray at application startup, and passes the shared controller into any later dashboard/config window. Its service survives dashboard close and supports no-dashboard mode; toggles still update `custom_fflag_disabled` and refresh proxy interception. `tests/test_windows_hotkeys.py::test_custom_fflag_hotkey_controller_toggles_without_dashboard` covers the root case, and the final native suite passed with 350 tests.

## Backups and cleanup proof

- Test-file backups: `/tmp/FleasionWindowsHandoff-20260806-2048/`, with `manifest.sha256`.
- Autostart source/test backups: `/tmp/FleasionWindowsHandoff-20260806-2048/autostart-investigation/`.
- Xbox/GDK Env Proxy source/test backups: `/tmp/FleasionWindowsHandoff-20260806-2048/xbox-env-bug/`.
- Native profile/certificate/settings backup: `C:\tmp\FleasionWindowsValidation-20260806-2102`.
- Authorized CA ceiling backup: `C:\tmp\FleasionWindowsValidation-20260807-ca-ceiling\cacert.pem.original`; restored exact SHA-256 `AF9C083591DAC8FD9DB3D8C5E405F24D0E7E051B55429D84FAAA55E2BD477A27`, attributes `Archive`.
- No automatic migration was implemented.
- No Roblox binaries, Studio files, hosts entries, firewall rules, or validation autostart task were left changed by the handoff run. The exact package/GDK validation processes were closed after each run; no 58443 listener remained. The working-tree diff contains the focused autostart/GUI, Xbox/GDK Env Proxy, and dashboard-independent hotkey fixes, their regression coverage, the earlier 12 platform-specific test guards, and this report.

## Final blocker

The verdict remains **NO** because the required live ACL/locking sequence and manual-proxy GUI timeout/restore gate were not fully exercised, and the strict GDK ceiling clause requires stopping the owned package Player while the implementation deliberately preserves that activation. Studio is now user-confirmed safe: it opened after Roblox, published successfully, and reported no TLS errors. The autostart and Xbox/GDK environment-injection defects are fixed with native regression coverage; the rebuilt package starts cleanly, the synchronized GDK ceiling run proves two repairs with no third relaunch loop, the live GDK smoke proved environment inheritance, flags, and local TexturePack HTTP serving, and the cache-dialog/hotkey regressions are covered by the native suite.
