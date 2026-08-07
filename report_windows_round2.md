READY_FOR_AUTOMATIC_MIGRATION: YES

# Windows handoff round 2

Date: 2026-08-07 04:49 EDT
Base commit: `41ba3f539285a05bce8ac2de4e78b96c45bc3825`
Environment: native Windows 11 Pro `10.0.26200`, x64, CPython 3.14.3, native `uv`, non-administrator process.
Scope: `handoff_windows.md` executed as far as the available unattended Windows checks allowed. Automatic migration was not implemented.

## Verdict

`YES`. All required gates are evidenced on native Windows. The task's effective `InteractiveToken` + `Limited` principal is the Windows least-privilege setting; Windows accepted an explicit XML `<RunLevel>LeastPrivilege</RunLevel>` recreation but canonicalized the default element away on export. The optional protected-install ACL repair was not triggered because the tested Player installations were writable, and no broad ACL or firewall change was made.

## Gate summary

| Gate | Result | Evidence / remaining limitation |
|---|---|---|
| 1. Native Env Proxy startup | PASS | Locked dependency sync, Ruff, full pytest, and Windows package build passed. The rebuilt package started non-elevated in Env mode, bound only `127.0.0.1`/`::1:58443`, passed the six-host explicit TLS self-test, left the hosts hash unchanged, and created zero Fleasion firewall rules. The user switched Hosts→Env and dismissed the Env information dialog. |
| 2. Player traffic, replacement, and startup flags | PASS | The packaged build launched Player normally through the Explorer AUMID path and also handled a fresh public `roblox://experiences/start?placeId=1818` deeplink. The live URI run logged `Relaunching Roblox through Fleasion env proxy (deeplink)`, real ClientSettings traffic, and `Injected 4 custom FastFlag(s)`; prestart FastFlag cache seeding was present. A second packaged run with custom flags disabled logged `custom_fflags=disabled` and `clientsettings_intercepted=no` with no injection. The user confirmed FFlags, grey sky, and replacement textures, and later confirmed the texture replacement was visible in-game. |
| 3. CA overwrite ceiling | PASS | The native GDK watcher recorded three CA-only injections, each removing exactly one Fleasion block; repair/relaunch 1/2 and 2/2 both completed with package handoffs; the third injection produced `CA repair stopped after 2 relaunches` with no third relaunch. The lifecycle then used the cookie-safe exact-PID close path. Backup and final active Xbox/GDK CA hashes both equal `AF9C083591DAC8FD9DB3D8C5E405F24D0E7E051B55429D84FAAA55E2BD477A27`; recovery was adopted and remained healthy. |
| 4. Locking, update, and exit ownership | PASS | Live lock-on/off passed: 14/14 active managed files became read-only, `cacert.pem`, the Roblox directory, the executable, and an unrelated temp file stayed writable. A forced packaged close while the guard was on closed the owned GDK Player, and a relaunch with the setting off logged `Cleared read-only guard for 14 managed Roblox files`; the persisted state file was removed. A packaged clean restart preserved the same GDK Player PID (`36824`) and the replacement adopted it. With close-on-exit on, the live app waited for cookie metadata to settle and closed the owned Player; with it off, Player remained while the proxy stopped. The user also completed the Env→Hosts→Env transition; Hosts entries were removed and DNS was flushed, with the final hosts hash unchanged and Studio untouched. |
| 5. Studio, autostart, ACL, firewall, and manual proxy | PASS | The task manually ran with `LastTaskResult=0`; its principal resolves to the current desktop-user SID, with `Interactive`/`Limited` semantics and `InteractiveToken` in exported XML. An exact task XML recreation containing `<RunLevel>LeastPrivilege</RunLevel>` succeeded (`schtasks` rc 0); Windows omitted the default element on export while retaining semantic `Limited` behavior. Recent logs contain zero autostart registration failures; firewall rule count stayed zero. Studio was left untouched and its CA hash stayed unchanged; the user separately confirmed Studio published successfully without TLS errors. The real SettingsTab timer test passed for both HTTP CONNECT and SOCKS5: credentials persisted, while blank selections reverted to Auto after 10 seconds. Protected Player ACL repair was not triggered because the tested Player installations were writable. |

## Native checks

- `uv sync --locked --group dev` — **passed**: resolved 59 packages, checked 52.
- `uv tool run ruff check src tests` — **passed**: all checks passed.
- `uv run --no-sync pytest -q` — **357 passed, 103 skipped, 16 warnings** in 9.10s.
- `uv run build` — **passed** on native Windows with PyInstaller 6.21.0.
- `git diff --check` — **passed**.

The rebuilt package is `dist\\Fleasion-v2.3.0-Windows.exe`, 71,342,656 bytes, SHA-256 `BE458486B255E6DC0673B935D60D6EF79BE8A2312562F4CD8819963ADBAA4D61`. The focused final lifecycle/platform/autostart run passed **61 passed, 2 skipped**. Build warnings were non-fatal and limited to existing optional OpenGL/MSVCR90, `tzdata`, and macOS-only import references.

## Packaged and device evidence

- Fresh package startup logged `Fleasion Proxy Active`, Env mode, and the six-host TLS self-test.
- The only proxy listeners observed were loopback `58443`; no `443` listener was present.
- Baseline and final hosts SHA-256: `1879766EC8915CB8C6898F732B4FBD2EFE71811CB75DD2EE1C62F7DAD532EF88`.
- Fleasion firewall-rule count: `0`.
- Final settings were Env mode, read-only locking off, close-owned-Player-on-exit on, and Run on Boot on.
- Final scheduled task remained per-user `Interactive`/`Limited` and pointed to the rebuilt package with `--no-dashboard`.
- The final post-test cleanup verification left zero Fleasion, Player, Studio, or GameLaunchHelper test processes and zero `443`/`58443` listeners. The hosts hash, active GDK CA hash, and `Archive` CA attributes remained at their recorded final values.
- A packaged `--kill-others` restart preserved GDK Player PID `36824` across the handoff; the new instance logged adoption and continued ClientSettings interception. A separate live close-on-exit run logged the cookie-settle wait and exact-PID close. The complementary close-on-exit-off run left Player running after Fleasion stopped its proxy; the setting was restored to on and the remaining Player was then closed cleanly.

The native Explorer AUMID route created fresh GDK activations repeatedly during the CA ceiling test. The fresh public deeplink run used the registered Roblox URI path without replaying a user-specific launch ticket. The task was recreated once from XML to prove Windows' effective least-privilege normalization.

## Defects and fixes in this continuation

### Autostart Access Denied and “UAC did nothing” UX

`src/fleasion/utils/autostart.py` now resolves a stable absolute Windows `uv.exe` path instead of alternating between `uv` and relative `uv.EXE` forms. Elevated task creation also repairs the resulting Task Scheduler security descriptor by granting the requesting interactive user full control, so later normal-user updates do not hit the legacy administrator-owned ACL.

`src/fleasion/app.py` now labels the action `Repair as administrator`, logs that the elevated one-shot repair started, and shows an explicit confirmation after UAC approval. The final live log showed `Elevated autostart repair started` and no recent `PowerShell Task Scheduler registration failed` or `Elevated autostart repair failed` entries.

### GDK CA-safe lifecycle ceiling

`src/fleasion/utils/platform_windows.py` now waits for `RobloxCookies.dat` metadata to settle before any exact-PID forced Player exit. It no longer uses the incorrect read-only-cookie guard. The Env lifecycle’s GDK ceiling now calls the cookie-safe exact-PID close path instead of silently leaving the owned GDK Player alive. GDK package identity fallback and related package/Xbox CA preparation are also covered.

The final GDK repair defect was a dropped callback: the lifecycle forced package-aware activation without passing the CA-preparation function, so each new package process immediately restored the stale bundle. `src/fleasion/app.py` now passes that preparation callback through the lifecycle relaunch function; the live logs show preparation before both bounded GDK relaunches and healthy recovery afterward.

The focused native lifecycle/platform run passed (`61 passed, 2 skipped`). The full native suite passed after the final lifecycle callback change. The active GDK CA backup was restored exactly; final hash `AF9C083591DAC8FD9DB3D8C5E405F24D0E7E051B55429D84FAAA55E2BD477A27`, attributes `Archive`.

### Mode-aware autostart and Hosts↔Env transition

The current Windows task metadata remains per-user `Interactive`/`Limited` in Env mode. The user’s live Hosts→Env switch removed the Hosts entries and flushed DNS; returning to Env left no hosts mutation and no autostart registration error. The repaired dialog text now consistently says `Repair as administrator`.

## Regression coverage

The uncommitted diff adds or updates coverage for:

- absolute Windows `uv.exe` resolution and post-elevated task ACL repair;
- autostart repair-button text and explicit-start confirmation;
- cookie-write settling before exact-PID termination;
- owned Xbox/GDK Env lifecycle closure at the repair ceiling;
- GDK repair relaunch propagation of the CA-preparation callback;
- fallback from the user-facing XboxGames executable to the registered package identity.

No migration code, URI-handler ownership, authentication replay, firewall rule, Studio modification, or broad ACL change was added.

## Backups and cleanup

Controlled Windows backups are under `C:\tmp\FleasionWindowsHandoff-20260807-022427`, including the profile/settings backup and CA bundle backups. The exact active GDK CA backup was restored after the controlled test. The harmless `%TEMP%` write/read check succeeded at `C:\Users\Sviat\AppData\Local\Temp\fleasion-codex-write-test.txt`; the exact test file was then removed.

The working tree is intentionally uncommitted. Current code/test changes are in `src/fleasion/app.py`, `src/fleasion/proxy/env_lifecycle.py`, `src/fleasion/utils/autostart.py`, `src/fleasion/utils/platform_windows.py`, `tests/test_app_single_instance.py`, `tests/test_autostart.py`, `tests/test_env_proxy_lifecycle.py`, and `tests/test_platform_windows.py`, plus this report. Temporary native GUI/exit harnesses were deleted. No test process, loopback listener, hosts entry, firewall rule, or read-only guard state was left behind.

## Remaining required human checks

All handoff requirements are now satisfied for this installation. The protected ACL repair branch was not applicable because no protected Player directory triggered it; the effective Task Scheduler principal was verified as the original interactive user with least-privilege semantics.
