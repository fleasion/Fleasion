READY_FOR_AUTOMATIC_MIGRATION: NO

# Windows handoff round 2

Date: 2026-08-06  
Base commit: `9efc0e25e0ed561fccd76ffd3aa1d74dcec91c7f`  
Environment: native Windows 11 Pro 10.0.26200, 64-bit, non-administrator process, CPython 3.14.3, native `uv` environment.  
Scope: `handoff_windows.md` executed without implementing automatic migration.

Player/Studio inventory: the Xbox/GDK install contains `C:\XboxGames\Roblox\Content\RobloxPlayerBeta.exe` and `Content\ssl\cacert.pem`; the active GDK process resolves to the corresponding `WindowsApps` package executable. Regular `%LOCALAPPDATA%\Roblox\Versions` and Studio roots were also discovered during the earlier handoff run.  
Initial repository state: clean `main-indev` worktree at the base commit above.  
Tested both the source environment and the packaged executable.  
Initial autostart state: no `Fleasion_Autostart` task; the profile was an existing installation with Env Proxy already selected, not a clean profile.

## Gate summary

| Gate | Result | Evidence / remaining limitation |
|---|---|---|
| 1. Native Env proxy startup | PASS | Native Windows pytest passed; packaged `Fleasion-v2.3.0-Windows.exe --no-dashboard` bound `127.0.0.1:58443` and `[::1]:58443`, logged Env mode and passed the explicit TLS self-test for six intercept hosts. Hosts and Fleasion firewall state were unchanged. |
| 2. Player CA/ownership/relaunch | PASS | Desktop Player relaunch and PID ownership are covered by native tests. Live Xbox/GDK activation reaches the real WindowsApps `RobloxPlayerBeta.exe` with `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, and `FLEASION_PROXY_RELAUNCHED` present; the user confirmed FFlags, grey-sky, and `vagueboypng.png` textures applied. The earlier no-texture observation was stale GDK asset storage, not a proxy-routing failure. |
| 3. Locking/update/exit | PARTIAL | Native regression coverage passed. Packaged cleanup was verified after closing the identified Fleasion process: zero listeners remained on 58443 and settings were restored byte-for-byte. A live Player lock/update/close-on-exit sequence was not available. |
| 4. Studio/autostart/ACL/firewall | PARTIAL | Studio certificate files stayed unchanged; Fleasion firewall rules were 0 before and after; the clean packaged run created the per-user task with `Interactive`/`Limited`, and cleanup removed it. Native tests passed, but live Studio behavior and ACL repair with a running Player were not exercised. |
| 5. Manual proxy fallback | BLOCKED | The code/regression suite passed, but the GUI-only blank-credential timeout and manual-proxy restore path require a human interaction session and were not run. |

## Changes and regression evidence

The changes include Windows-validity guards in 12 platform-specific tests, the normal-user Task Scheduler and mode-aware Run on Boot fixes, and package-aware Xbox/GDK Env Proxy activation with a fail-safe fallback. The production fixes do not implement automatic migration.

Native Windows commands and results:

- `uv run pytest -q` in the native Windows environment — **347 passed, 103 skipped, 21 warnings** in 10.64s.
- Native `uv tool run ruff check src tests` — **All checks passed**.
- `uv run build --clean` — **passed**; PyInstaller produced `dist\\Fleasion-v2.3.0-Windows.exe` (71,336,297 bytes).
- Final package SHA-256: `410B6BF43D25A3EAFD979101DDA822566EB46915C623885870C3E6CD7458DE46`.

The build emitted existing non-fatal packaging warnings for optional OpenGL acceleration, `tzdata`, legacy `MSVCR90.dll` references, and macOS-only framework imports. The Windows build completed successfully.

## Packaged smoke evidence

The packaged executable was started in Env mode against the real local Roblox/Fleasion profile after backing up the profile state. The latest log recorded:

- certificate readiness completed;
- CA already installed in the discovered Player/Studio installs;
- `Roblox Env Proxy mode active; skipping privileged relay startup`;
- explicit proxy TLS self-test passed for six intercept hosts;
- `Fleasion Proxy Active`.

The Player certificate count was 1 and Studio certificate count was 2 both before and after; all recorded SHA-256 values were unchanged. The hosts-file hash was unchanged, and Fleasion firewall-rule count stayed at 0. The test settings were restored byte-for-byte from the backup. After the package process was closed, port 58443 had zero loopback listeners.

The final rebuilt executable was started in Env mode and left `127.0.0.1:58443` and `[::1]:58443` listening; its log recorded the six-host TLS self-test and `Fleasion Proxy Active`. The exact smoke processes were then closed and no test instance remained. A live Store-entry GDK run confirmed that the GameLaunchHelper child does not inherit a caller's scoped Env Proxy variables; the package-aware activation path injected the environment into the actual WindowsApps Player child. The user confirmed successful startup, active custom flags, grey sky, and visible local textures. Returning to an older experience reused stale GDK asset storage until the cache was cleared; the proxy logs still showed the replacement KTX2 being served, so this was cache state rather than a routing defect.

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

The fix now arms the documented package-debugging API with a complete proxy environment block before Store activation, activates the manifest AUMID, and supplies Fleasion's own executable as the dummy debugger so Windows resumes the initial suspended thread. Normal GDK launch monitoring leaves the initial WindowsApps Player untouched; only the explicit CA-repair path can use reactive package activation. Fleasion disarms package debugging during shutdown. Regression tests cover GDK detection, package identity/AUMID derivation, environment-block termination, the no-kill normal GDK path, fallback behavior, cancellation during the startup settle, and desktop relaunch behavior. The observed `0x1` sequence was consistent with the monitor force-closing the first GDK Player during its fragile bootstrap; the fix waits three seconds in the reactive path and the live package-aware launch now completes without the repeated relaunch behavior. Hosts File mode remains the fallback when package-aware activation is unavailable. No matching Windows Application Error event for Roblox, GDK, or `0x1` was present in the last four hours, so the original `0x1` remains confirmed by the user's dialog but not by a Windows crash bucket.

The reported local TexturePack PNG was also traced independently. The live log recorded conversion/cache selection for `vagueboypng.png`, a CDN short-circuit to the generated KTX2, and `HTTP/1.1 200 OK` local serves. The user then confirmed that the texture visibly applies after the stale GDK asset store is cleared; rejoining the older experience reproduced the stale visual because that process reused its cached asset. The unrelated ID-based replacement rules showed separate `401` precheck failures.

### DEFECT-WIN-004: Clear Cache progress window remained open after completion

`src/fleasion/gui/delete_cache.py` appended `Done.` from the completion signal but never closed the modeless dialog. The fix keeps the completion message visible for 500 ms and then calls `QDialog.accept()`. `tests/test_delete_cache_window.py` covers the timer and close callback. The focused native Windows test passed, and the full native suite passed after the fix.

## Backups and cleanup proof

- Test-file backups: `/tmp/FleasionWindowsHandoff-20260806-2048/`, with `manifest.sha256`.
- Autostart source/test backups: `/tmp/FleasionWindowsHandoff-20260806-2048/autostart-investigation/`.
- Xbox/GDK Env Proxy source/test backups: `/tmp/FleasionWindowsHandoff-20260806-2048/xbox-env-bug/`.
- Native profile/certificate/settings backup: `C:\tmp\FleasionWindowsValidation-20260806-2102`.
- No automatic migration was implemented.
- No Roblox binaries, Studio files, hosts entries, firewall rules, or validation autostart task were left changed by the handoff run. One exact active test GDK Player was closed during the controlled relaunch test; the later live smoke started the rebuilt package and left the resulting test Player available for post-fix confirmation. The working-tree diff contains the focused autostart/GUI and Xbox/GDK Env Proxy fixes, their regression coverage, the earlier 12 platform-specific test guards, and this report.

## Final blocker

The verdict remains **NO** because the required live Studio, ACL/locking, and manual-proxy GUI gates were not fully exercised. The autostart and Xbox/GDK environment-injection defects are fixed with native regression coverage; the rebuilt package starts cleanly, the live GDK smoke proved environment inheritance, flags, and local TexturePack HTTP serving, and the cache-dialog regression is covered by a native GUI test.
