# macOS Env Proxy validation report

## Verdict

READY_FOR_AUTOMATIC_MIGRATION: NO

The Env Proxy path works in the exercised scenarios, but automatic migration is not yet approved. The complete mandatory live matrix was not completed, and the newly fixed macOS custom-FastFlag startup path has not yet had its rebuilt packaged-app live retest. The exact blockers are documented below: controlled CA-overwrite/ceiling tests, read-only/update-toggle tests, ownership/restart/mode-switch tests, logout/reboot testing, browser/deeplink joining, harmless in-game replacement verification, Studio place/publish testing, and post-fix packaged custom-FastFlag verification. A live Player session was user-confirmed as joined; no cookies or authentication files were read or changed.

## Environment

- macOS 26.5, build 25F71; hardware MacBook Pro Mac14,7 with Apple M2; `uname -m`: `arm64`.
- Rosetta is installed and `arch -x86_64 /usr/bin/true` succeeded. The release build produced and verified the arm64 and x86_64 slices; live execution was arm64 only.
- Roblox Player baseline: `0.732.0.7321040` at `/Applications/Roblox.app`, owner `local-user:staff`, resources at `/Applications/Roblox.app/Contents/Resources`.
- Roblox Studio: `0.732.19.7321043` at `/Applications/RobloxStudio.app`, owner `local-user:staff`, resources at `/Applications/RobloxStudio.app/Contents/Resources`.
- Player `cacert.pem` baseline: mode `644`, owner `root:staff`, SHA-256 `fa52826098977026721b9977c807ede2420bf47e274c491fa3897e821f504b79`.
- Studio `cacert.pem` baseline: mode `644`, owner `local-user:staff`, SHA-256 `2eba303a7b3c87f0c52fa8f977b703ec6dea7de703f97644546d2d34e53e4cd9`.
- Froststrap and AppleBlox are present. The active Froststrap Player and restore snapshot were discovered and included in CA validation; Studio was excluded from Env interception.
- Fleasion branch `main-indev`, baseline commit `2b52cc4`; the worktree was clean at the beginning of the original validation. Subsequent remediation changes are listed in the final diff summary and were preserved.
- Both source and packaged app paths were tested. The rebuilt universal artifact is `dist/Fleasion-v2.3.0.app` and its universal zip; `dist/Fleasion.app` is an intermediate x86_64 build output, not the universal release artifact.
- The legacy macOS helper was already installed and running before testing: `com.fleasion.proxy-helper` LaunchDaemon plus `/Library/PrivilegedHelperTools/com.fleasion.proxy-helper`.
- An existing, user-owned LaunchAgent was present at `~/Library/LaunchAgents/com.fleasion.autostart.plist` and points to the checkout launcher. The existing profile already selected Env Proxy (`proxy_mode=env`); this was not a clean profile and no Hosts-to-Env automatic migration was added.
- Initial settings had read-only locking off by default. Final relevant settings remained Env Proxy enabled, run-on-boot enabled, read-only locking disabled, and close-on-exit enabled.

## Initial repository state

The worktree was clean at the start of the original validation. No existing changes were overwritten or reset. The handoff and repository instructions were read before changes were made. The current worktree intentionally contains the remediation changes listed under Final git diff summary.

## Automated gates

- `uv sync --dev`: PASS.
- Ruff: BLOCKED for the post-fix run. Ruff is not included in the synced dev environment; the earlier isolated `uvx ruff check .` baseline passed, but the post-fix retry could not fetch Ruff because the sandbox had no package-index/DNS access. No lint failure was observed.
- Pytest: PASS, `470 passed, 5 warnings` in the current environment. The focused custom-FastFlag/Env lifecycle tests passed `29 passed`.
- The first complete run exposed two foreign-platform test-simulation failures. They were corrected, then the focused and complete suites passed with no test failures.
- Universal/package build: PASS. The rebuilt `uv run build` completed with an isolated PyInstaller cache, produced the arm64 and x86_64 builds, universal artifacts, and the universal zip/extraction verification.
- Explicit package checks: PASS for `dist/Fleasion-v2.3.0.app`. Its main executable is a Mach-O universal binary containing `x86_64` and `arm64`; the packaged helper payloads are executable for both slices. The generic `dist/Fleasion.app` intermediate is x86_64-only and was not treated as the release artifact.

## Live acceptance results

| ID | Test | Result | Evidence |
|---|---|---|---|
| A1 | Normal-user startup | PASS | Source and packaged Fleasion ran as the logged-in user; no GUI was run as root and no new administrator prompt was needed for startup. |
| A2 | Loopback-only listener | PASS | `lsof` showed Fleasion listening on `127.0.0.1:58443` and `::1:58443`, owned by the normal-user process; no public-interface listener was observed. |
| A3 | Login-keychain trust | PASS | Startup log reported macOS login-keychain CA trust installed/current; no system-keychain installation was performed. |
| A4 | Direct patch before helper | BLOCKED | The active `/Applications/Roblox.app` bundle was protected during the first run and the helper was already installed; direct-success with the helper stopped was not isolated. |
| A5 | Normal and deeplink Player launch | BLOCKED | Normal `open -a Roblox` launch and one controlled Env relaunch were observed; a browser/deeplink join target was not exercised. |
| A6 | Player networking/login/join/assets | PASS | The user confirmed the live Player was already joined; it stayed running with a healthy CA while proxy logs showed CustomFFlags processing and successful local TexturePack HTTP 200 traffic. No cookie or authentication-file access was performed. |
| A7 | Harmless replacement verification | BLOCKED | Proxy logs show custom FastFlag processing and local TexturePack responses; an in-game replacement was not manually verified in a disposable account. |
| A8 | Packaged ordinary Env path | BLOCKED | The earlier packaged Env smoke passed, and the rebuilt universal artifact passes architecture checks, but the packaged live startup/visual retest for the new custom-FastFlag seed has not yet been repeated. |
| A9 | Immediate Env information dialog dismissal | BLOCKED | The no-dashboard smoke path did not provide a visible dialog interaction. |
| B1 | Helper baseline | PASS | The legacy helper and LaunchDaemon were installed and ready before testing. |
| B2 | Writable direct-first behavior | BLOCKED | A writable-install run with the helper deliberately stopped was not isolated. |
| B3 | Protected active CA setup | BLOCKED | No temporary ownership/mode mutation was made to the live Player bundle. |
| B4 | Protected-install fallback prompt | BLOCKED | The protected fallback was observed, but the controlled prompt-and-approval path was not repeated as a separate mutation test. |
| B5 | Helper retry verification | BLOCKED | No new helper installation/upgrade approval was requested. |
| B6 | Relaunch without repeat approval | PASS | Existing helper state was reused across source and packaged runs without another approval. |
| B7 | Restore exact protected-install state | BLOCKED | No temporary protected-install mutation was made, so there was no such test restoration to validate. |
| B8 | Froststrap/AppleBlox discovery | PASS | Player, active Froststrap Player, Froststrap restore snapshot, and Studio were discovered; Env CA patching handled the two Player installs and left Studio out. |
| C1 | Healthy CA before/after startup | PASS | Player CA health was `healthy=true`, `fleasion_certs=1`, `current_fleasion_certs=1`, `total_certs=148` after startup and launch. |
| C2 | One-overwrite repair | BLOCKED | Deliberate removal of only the live Fleasion CA was not performed. |
| C3 | Two-overwrite repair ceiling | BLOCKED | Deliberate two-overwrite testing was not performed. |
| C4 | No-third-relaunch ceiling | BLOCKED | Deliberate ceiling testing was not performed. |
| C5 | Disable features and exit | BLOCKED | Feature-disable/exit persistence was not separately exercised. |
| D1 | Read-only and close-on-exit defaults | PASS | Final settings showed read-only locking disabled and close-on-exit enabled. |
| D2 | Modification/FastFlag with lock off | PASS | Startup wrote normal modification/FastFlag targets; no persistent read-only lock was enabled, and Player traffic showed the custom-settings path. |
| D3 | Controlled Roblox update/reinstall | BLOCKED | A Player update occurred during testing, but a separately controlled update/reinstall scenario was not initiated. |
| D4 | Updated Player CA/Env path | PASS | Player advanced to `0.733.0.7330989`; its CA was repaired and remained healthy, with Env relaunch and proxy traffic observed. |
| D5 | Enable read-only scope | BLOCKED | The read-only toggle was not enabled. |
| D6 | Disable read-only restoration | BLOCKED | No read-only mutation was made. |
| D7 | Legacy read-only cleanup | BLOCKED | No legacy read-only test files were intentionally created. |
| D8 | Force-close restoration | BLOCKED | No read-only force-close scenario was run. |
| E1 | Default close-on-exit ownership | BLOCKED | Player was closed explicitly during cleanup rather than by a normal Fleasion exit. |
| E2 | Close-on-exit disabled | BLOCKED | Not exercised. |
| E3 | Restart and restore default | BLOCKED | Not exercised as a lifecycle matrix item. |
| E4 | Player-preserving restart | BLOCKED | Not exercised. |
| E5 | Ownership-token replacement safety | BLOCKED | Not exercised. |
| E6 | Env-to-Hosts switch | BLOCKED | Not exercised. |
| F1 | Studio baseline | PASS | Studio PID, command, CA hash, and mode were recorded; final CA hash remained `2eba303a7b3c87f0c52fa8f977b703ec6dea7de703f97644546d2d34e53e4cd9` and mode was restored to `644`. |
| F2 | Studio during Env operation | PASS | Studio launched while source Env Proxy was active and remained operational; the log reported Studio was left untouched. |
| F3 | Studio during Player/Fleasion lifecycle | BLOCKED | Studio and Player were launched together, but a full restart/exit sequence while deliberately leaving Studio open was not isolated. |
| F4 | Studio isolation | PASS | No Studio warning, relaunch, proxy environment injection, or CA content change was observed; Studio CA health correctly remained non-Fleasion. |
| F5 | Studio place/publish/network operation | BLOCKED | Studio isolation and normal-networking behavior were tested; place/publish was omitted as agreed because it was not needed to validate Env Proxy isolation. |
| G1 | Toggle Run on Boot on | BLOCKED | The pre-existing setting was already on; it was not toggled through the GUI. |
| G2 | User LaunchAgent | PASS | The LaunchAgent remained user-owned, pointed to the checkout launcher, used `--no-dashboard`, and required no sudo/helper action to exist. |
| G3 | Manual agent plus logout/login | BLOCKED | Logout/reboot testing was not performed. |
| G4 | Helper independent of GUI startup | BLOCKED | The already-running helper was observed, but it was not stopped and restarted independently. |
| G5 | Toggle Run on Boot off/unload | BLOCKED | Not exercised. |
| H1 | Universal package contents | PASS | Build verification and explicit `file`/`lipo -info` checks confirmed the app executable, helper payloads, Qt/plugins, CA dependencies, and both architecture slices. |
| H2 | Packaged Finder and Terminal launch | BLOCKED | Terminal launch passed; Finder launch was not separately tested. |
| H3 | Packaged lifecycle/isolation smoke tests | BLOCKED | Earlier packaged Env startup, Player launch/traffic, and clean local quit passed; the rebuilt package's post-fix custom-FastFlag startup, packaged Studio isolation, and Player-preserving restart were not repeated. |
| H4 | Architecture live-test scope | PASS | The machine live-tested arm64 only; x86_64 was built and slice-verified through Rosetta, not live-exercised as a separate architecture. |

## Defects found

### DEFECT-MAC-001: Legacy `proxy_ca` directory was owned by root

- Reproduction: `~/Library/Application Support/FleasionNT/proxy_ca` was `755 root:staff`; its CA/key files were `644 root:staff`.
- Root cause: consistent with the user-confirmed extremely old development build creating this configuration through a privileged/root process. Unix ownership follows the creating process; mode `644` permits reads but only the owner can write, and a root-owned `755` directory also prevents normal-user create/delete/rename operations.
- User impact: the current normal-user app could not use or update the configured directory, so it fell back to `proxy_ca_user` and needed helper fallback for protected Player CA files.
- Repair: ownership was restored to the logged-in user’s `staff` group with a narrow administrator-authorized `chown -R` on this exact directory. The ownership repair itself preserved the existing modes and file contents; the subsequent normal startup regenerated/updated the expected host-specific CA material in that directory. The repaired directory is now `755` for the directory and `644` for its CA/key files.

### DEFECT-PORT-001: Foreign-platform launch-path test exposed POSIX `Path.resolve()` behavior

- Reproduction: the Windows launch-info test mocked `sys.platform='win32'` on macOS; `Path.resolve()` converted `C:\Tools\uv.exe` into a POSIX checkout path.
- Root cause: the returned `shutil.which()` path was unnecessarily resolved by the host platform.
- User impact: no macOS runtime impact; it made the complete test gate fail and weakened cross-platform testability.

### DEFECT-MAC-002: macOS custom FastFlags arrived after the startup loader

- Reproduction: in the AppleBlox/Env run, the Player local `ClientAppSettings.json` contained only the ordinary local flags before startup. Roblox logged `fastflag_load_success` at approximately `18:14:39`; Fleasion did not inject the custom flags until the later ClientSettings response at approximately `18:18:41`. The dynamic reloader then delivered the custom response, which explains why the flag appeared after a delay.
- Root cause: the custom modifier previously relied on the intercepted ClientSettings response for macOS. The normal macOS Roblox startup loader reads the local Player settings file before that response arrives, so startup-only custom flags were absent from the initial load. This was separate from the Linux-only Sober certificate-pinning delay; the observed delay here was the missing macOS local seed.
- User impact: custom flags such as the broad-phase colored-outline diagnostic could be absent at launch and appear only after a later refresh. AppleBlox itself located the Player and opened the Roblox deeplink, but its bootstrapper logged exit code `9`, so the run does not establish a clean AppleBlox-owned startup injection path.

## Fixes made

### FIX-MAC-001: Restore legacy CA-directory ownership

- Files changed: user configuration only, `~/Library/Application Support/FleasionNT/proxy_ca`.
- Implementation: restored ownership to the logged-in user and preserved the existing `755/644` modes, CA material, and extended attributes.
- Regression test: not practical as a repository test because it requires administrator-owned user configuration; exact post-repair ownership/mode/hash verification was performed.
- Live retest evidence: source startup selected the repaired directory, patched protected Player/Froststrap CA files through the existing helper, passed the 7-host TLS self-test, and Player remained CA-healthy through launch.

### FIX-PORT-001: Preserve `shutil.which()` launch paths

- Files changed: `src/fleasion/utils/autostart.py` and `tests/test_windows_permissions.py`.
- Implementation: `_get_launch_info()` now keeps the executable path returned by `shutil.which()` intact. Windows permission tests now patch the shared Roblox-directory platform/constants when simulating Windows on another host.
- Regression test: focused autostart/Windows-permission tests passed `7 passed`; the full suite passed `465 passed`.
- Live retest evidence: the universal macOS build passed, and the packaged application launched and exited through its normal local control path. The earlier baseline Ruff run passed; the post-fix Ruff retry was unavailable because the sandbox could not fetch the tool.

### FIX-MAC-002: Pre-seed startup custom FastFlags on both desktop platforms

- Files changed: `src/fleasion/proxy/addons/custom_fflags.py`, `src/fleasion/proxy/master.py`, `src/fleasion/proxy/env_lifecycle.py`, `src/fleasion/modifications/macos_bootstrapper_bridge.py`, `src/fleasion/app.py`, plus `tests/test_custom_fflags.py`, `tests/test_env_proxy_lifecycle.py`, and `tests/test_macos_bootstrapper_bridge.py`.
- Implementation: macOS now atomically merges active custom flags and the non-persisted one-second dynamic-reload companion into each discovered Player `Contents/Resources/ClientSettings/ClientAppSettings.json`; Studio is excluded. The launch-preparation hook runs before the initial Env launch and each CA-repair relaunch, and AppleBlox resource rewrites trigger reseeding. Windows keeps its existing binary flag-cache writer, now reached through the same platform-specific startup-seed dispatcher and relaunch hook.
- Regression test: the focused custom-FFlag/Env lifecycle tests passed `29 passed`; the complete suite passed `470 passed, 5 warnings`.
- Live retest evidence: the source logs reproduced the defect, and the rebuilt universal package passed architecture/content checks. A fresh packaged Player launch proving immediate visual FastFlag application is still pending, so this fix is not yet a migration gate pass.

## Remaining risks or blockers

- The complete automatic-migration decision remains `NO` because the mandatory matrix has blocked items, especially deliberate CA-overwrite/ceiling testing, read-only toggle/update compatibility, ownership-token/restart/mode-switch behavior, and logout/reboot.
- The intended end state remains automatic migration of existing Hosts users to Env Proxy. This report deliberately does not enable or approve that migration until the mandatory packaged live matrix passes, including the new macOS startup-seed retest and a Windows packaged pre-seeding check.
- Windows pre-seeding is covered by source wiring and regression tests, but Windows packaged live execution was not part of this macOS run and remains a separate validation item.
- Player networking, the user-confirmed join, and local asset/FastFlag traffic were observed; no harmless in-game replacement was manually verified, and no cookies or authentication files were accessed.
- Studio remained isolated and its CA content was unchanged; place/publish was intentionally omitted per the user’s testing scope.
- The Roblox Player updated from the baseline `0.732` build to `0.733` during testing. The updated Player CA was repaired and healthy; the original pre-update Player backup was retained only during validation and was not restored over the newer Roblox installation.
- Test processes were stopped after the smoke tests; the installed helper, login-keychain trust, repaired user CA directory, and valid Player CA remain installed as required.

## Final git diff summary

- `src/fleasion/app.py`, `src/fleasion/modifications/macos_bootstrapper_bridge.py`, `src/fleasion/proxy/addons/custom_fflags.py`, `src/fleasion/proxy/env_lifecycle.py`, and `src/fleasion/proxy/master.py`: pre-seed platform-specific startup custom FastFlags and re-arm them for Env relaunches, including AppleBlox rewrites.
- `tests/test_custom_fflags.py`, `tests/test_env_proxy_lifecycle.py`, and `tests/test_macos_bootstrapper_bridge.py`: cover macOS seeding, Windows dispatch compatibility, relaunch preparation, and the AppleBlox reseed callback.
- `src/fleasion/utils/autostart.py`: preserve `shutil.which()` paths without host-platform resolution.
- `tests/test_windows_permissions.py`: make foreign-platform simulation patch the shared directory helpers/constants.
- `report_macos.md`: this validation report.
- Existing uncommitted changes were preserved; no commit, push, PR, reset, or unrelated worktree overwrite was made.

## Cleanup performed

- Closed the Player and Studio instances opened for testing.
- Stopped source and packaged Fleasion through the local control path; the final packaged instance exited with code `0`.
- Confirmed no Fleasion/Roblox test processes remained and no port `58443` listener remained.
- Restored active Player and Studio `cacert.pem` mode to `644`; no CA content was removed. The current Player owner is the normal user because Roblox’s update replaced the old protected app content.
- Left the installed helper/LaunchDaemon, login-keychain trust, valid Env CA, and user LaunchAgent in place.
- Removed the private temporary validation backup directory after final verification; no credentials, cookies, private keys, or request contents were copied into this report.
- Removed the task-local temporary PyInstaller/uv validation directories from `/private/tmp`; the protected global PyInstaller cache was not modified.
