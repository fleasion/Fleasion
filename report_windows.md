# Windows Env Proxy validation report

## Verdict

READY_FOR_AUTOMATIC_MIGRATION: NO

The Env Proxy startup path is working on this Windows desktop, but the required packaged live matrix is incomplete and the repository-wide test command cannot run cleanly on Windows because Linux-only modules import `pwd`. The legacy `Fleasion_Autostart` task also still requires the user-selected one-time repair path.

## Environment

- OS: Windows 11 Pro, version `10.0.26200`, build `26200`, x64. `Get-ComputerInfo` returned the stale product label “Windows 10 Pro”; CIM reported the authoritative Windows 11 edition.
- Roblox Player: `0.727.0.7271199`, `%LOCALAPPDATA%\Roblox\Versions\version-1a951716f19e4638\RobloxPlayerBeta.exe`.
- Roblox Studio: `0.721.0.7211107`, `%LOCALAPPDATA%\Roblox\Versions\version-792bc2069be7464a\RobloxStudioBeta.exe`.
- Fleasion branch/commit: `main-indev` / `144b7539392e700ee12f0c7b14fe4a736dd22b92`.
- Initial repository status: clean. Testing covered source and the packaged `dist\Fleasion-v2.3.0-Windows.exe`.
- A legacy `Fleasion_Autostart` task existed. Its XML used `InteractiveToken`, `HighestAvailable`, and an old packaged executable path.
- The profile was an existing non-clean profile already set to `proxy_mode: env`; automatic Hosts File migration was not added.

## Initial backup and safety state

- Backup: `C:\tmp\FleasionWindowsValidation-20260805-154351` contains the Fleasion profile copy, Roblox CA copies, and `manifest.json` with hashes/attributes.
- No Roblox CA was removed. No firewall rule, scheduled task, watcher, or read-only lock was added by validation.
- The requested permanent ACL change was applied only to the four failed Player folders under `C:\Program Files (x86)\Roblox\Versions\`.

## Automated gates

- `uv sync --dev`: passed in the isolated PowerShell `uv` environment `C:\tmp\FleasionValidationVenv`; the checkout `.venv` was not used because its stale `lib64` reparse point returned access denied.
- Ruff: passed with `uv run --with ruff ruff check .`.
- Focused Windows suite: `68 passed, 1 failed`. The only failure was the existing Linux-path assertion in `tests/test_autostart.py::test_linux_autostart_prefers_installed_launcher`, caused by exercising mocked Linux desktop output with Windows paths.
- Full `uv run pytest -q`: blocked at collection by five Linux-specific modules importing POSIX-only `pwd` on Windows.
- Package build: passed with PyInstaller; output `dist\Fleasion-v2.3.0-Windows.exe`. Build emitted existing OpenGL/MSVCR90 and missing `tzdata` warnings but exited successfully.
- New focused ACL/startup tests: `8 passed`.

## Live acceptance results

| ID | Test | Result | Evidence |
|---|---|---|---|
| A1 | Non-elevated source startup | PASS | Normal PowerShell/`uv` launch reached Env Proxy startup without requiring elevation for proxy bind. |
| A2 | Loopback listener | PASS | Source and package bound port `58443` on `127.0.0.1` and `::1`; no public-interface listener was observed. |
| A3-A6 | Player join, relaunch target, networking, replacement | BLOCKED | No disposable account/harmless experience session was available for this run. |
| A7 | Packaged startup | PASS | Package process responded; log recorded Env Proxy active and the seven-host TLS self-test passed. |
| A8 | Information dialog dismissal | BLOCKED | No interactive UI acceptance capture was performed. |
| B1 | Healthy CA baseline | PASS | Existing bundle checks and startup logs showed Fleasion CA health/current CA state and successful TLS self-test. |
| B2-B6 | CA overwrite ceiling and protected-CA lifecycle | BLOCKED | No controlled Player startup/overwrite watcher run was performed. |
| C1 | Settings defaults | PASS | Existing settings had read-only locking off and close-on-exit on. |
| C2-C8 | Real modification/update/read-only lifecycle | BLOCKED | Only focused filesystem tests were run; no Roblox update or live modification session was performed. |
| D1-D6 | Player ownership, exit, restart, mode switch | BLOCKED | No live Player process was available for the ownership matrix. |
| E1-E5 | Studio isolation | BLOCKED | Studio was discovered and its CA was not targeted, but no concurrent Studio session was run. |
| F1 | Legacy task diagnosis | FAIL | Existing task was `InteractiveToken`/`HighestAvailable`, pointed to an old package, and non-admin sync logged `schtasks` access denied. |
| F2-F6 | New task migration, sign-in, and disable | BLOCKED | The new one-time admin repair button was unit-tested; it was not selected for the existing task during this run. |
| G1-G3 | Firewall/listener behavior | PASS | No Fleasion-named firewall rules were found; the proxy used loopback only and no firewall mutation code was exercised. |
| G4-G5 | Blocked-connection UI and final firewall proof | BLOCKED | No approved outbound-block scenario was run; validation cleanup confirmed no temporary rule was left. |

## Defects found

### DEFECT-WIN-001: Legacy elevated autostart task is inaccessible to a normal launch

- Reproduction: launch with Run on Boot enabled while the existing task has `HighestAvailable`; `schtasks` update/delete returned access denied.
- Root cause: the old task was created with administrator-level task metadata and referenced an obsolete packaged executable.
- User impact: normal launch cannot silently replace or remove it.

### DEFECT-WIN-002: Four old Player installs denied configured writes

- Reproduction: modifications and FastFlags failed only in four saved `Program Files (x86)\Roblox\Versions\...` Player folders; six AppData installs remained writable.
- Root cause: the protected folders were owned by Administrators and granted standard `Users` read/execute only.
- User impact: configured writes failed even though ordinary Roblox installations normally permit the current user to update their files.

### AUTOMATED-GATE-001: Repository-wide pytest is not Windows-collectable

- Five Linux-specific test modules import `pwd`, which is unavailable on Windows. This is separate from the Env Proxy runtime path.

## Fixes made

### FIX-WIN-001: One-time autostart repair prompt

- `src/fleasion/app.py` now offers `Ignore` or `Relaunch as administrator` when the legacy task cannot be reconciled. Ignore leaves Run on Boot enabled so the error can reappear on a later launch.
- The elevated child performs only the autostart sync and exits; the normal app is not made permanently elevated.
- `src/fleasion/utils/autostart.py` and relaunch code now resolve the checkout root correctly when launched through PowerShell `uv`.

### FIX-WIN-002: Explicit, current-user-only Roblox ACL repair

- `src/fleasion/modifications/manager.py` now records every exact install directory whose modification write fails instead of stopping at the first protected folder.
- `src/fleasion/modifications/fflag_manager.py` returns exact directories whose FastFlag writes fail.
- `src/fleasion/app.py` prompts before any permanent ACL change. Approval stores only those failed install directories and starts a one-shot elevated child.
- `src/fleasion/utils/windows_permissions.py` validates each target as a Roblox Player resource directory, rejects Studio/parent paths, resolves the current user SID, and runs `icacls` with only `*<current-SID>:(OI)(CI)M /T /C`. Existing ACL entries remain intact; no `Users` or all-users grant is used.
- The packaged live run completed with `ok: true` for all four failed folders. ACL inspection showed the current user’s explicit Modify entry and the existing `BUILTIN\Users:(RX)` entry still present. The change is intentionally permanent, as requested.
- Regression coverage includes targeted ACL command construction, path rejection, prompt wiring, one-shot result handling, modification-denial aggregation, and FastFlag-denial reporting.

## Remaining risks or blockers

- Automatic migration remains correctly disabled until the full Windows/macOS live reports are reviewed.
- A real Roblox Player account/experience join, replacement, CA-overwrite ceiling, ownership, update, and Studio-concurrency matrix remains outstanding.
- The legacy autostart task remains `HighestAvailable` until the user chooses the new one-time repair action.
- The Linux-only pytest collection failures should be run under Linux/WSL separately; Linux/Sober support is not inferred from this Windows report.

## Final git diff summary

- Modified: `src/fleasion/app.py`, `src/fleasion/modifications/manager.py`, `src/fleasion/modifications/fflag_manager.py`, `src/fleasion/utils/autostart.py`, and related tests.
- Added: `src/fleasion/utils/windows_permissions.py`, `tests/test_windows_permissions.py`.
- No commit, push, or branch change was performed.

## Cleanup performed

- Source and packaged Fleasion processes were stopped after each live startup check.
- Port `58443` was confirmed unused at handoff.
- Temporary pending/result ACL markers and temporary package/source logs were removed.
- The four requested permanent current-user ACL grants remain; Roblox CA files and the validation backup were retained.
