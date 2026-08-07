READY_FOR_AUTOMATIC_MIGRATION: YES

# Fleasion Env Proxy — macOS handoff round 2

## Scope and environment

- macOS 26.5, Apple Silicon arm64; Rosetta x86_64 execution was available.
- Branch: `main-indev`; baseline commit: `3ccf722a867168a633d89bd81a75fb0bb0ae9284`.
- No automatic Hosts File → Env Proxy migration was implemented.
- Player: Roblox `0.733.0.7330989`; Studio: Roblox `0.732.19.7321043`.
- Rebuilt package: `dist/Fleasion-v2.3.0.app`.
- Universal archive SHA-256: `0370dfbf914c54574be2dab33d07139e5c603e4522521303b9893f21893263c7`.
- Backup retained outside the checkout: `/private/tmp/fleasion-macos-round2.izq1S0`.

## Gate results

| Gate | Result | Evidence |
|---|---|---|
| 1. Automated, universal build, packaged startup | PASS | Full x86_64 suite: `505 passed`; Ruff and `git diff --check` passed. The packaged executable contains arm64 and x86_64 slices, both helper slices, and passes deep strict codesign verification. The live arm64 package started as the normal user, passed the seven-host Env TLS self-test, used only loopback `58443`, did not bind `443`, did not change `/etc/hosts`, and required no ordinary-install admin prompt. The Env information dialog was already configured and did not block startup. |
| 2. Direct CA path and protected fallback | PASS | Writable Player CA patching was exercised directly. A protected exact Player CA target first failed the direct write, then used the existing privileged helper after one approval; the helper was reused on relaunch without another approval. Final Player and Studio CA hashes, ownership, modes, flags, and xattrs matched their baselines. |
| 3. Player traffic, bootstrappers, startup flags | PASS | Real Player traffic and a harmless replacement were observed; Studio remained untouched and the user reported no Studio TLS errors. Froststrap and AppleBlox both launched Player through Env Proxy. Fresh intercepted ClientSettings responses logged three custom flags injected and dcz re-encoded. The illegal DebugDraw flag was therefore delivered through the remote ClientSettings response path, not persisted in the allowlisted local file. AppleBlox’s own latest launch log recorded `DFFlagDisableDPIScale: true`, proving its DPI feature survived. The Resources cache was restored to its pre-test snapshot at cleanup. |
| 4. CA overwrite ceiling | PASS | A capped watcher performed exactly three Fleasion-CA-only injections. Injection 1 caused repair/relaunch 1/2; injection 2 caused repair/relaunch 2/2; injection 3 stopped the owned Player with no third relaunch. No duplicate Fleasion CA or public-root loss occurred, and normal Player startup recovered afterward. |
| 5. Locking, exit ownership, Studio isolation | PASS | With locking off, an unrelated Player resource file was atomically replaced successfully. With locking on, managed ClientSettings files became read-only, while CA files, directories, executable, unrelated file, and Studio remained writable/unchanged; a managed write returned `PermissionError`. Force-close followed by relaunch cleared `read_only_modes.json` and restored writable modes. Close-on-exit on closed the adopted Env Player; close-on-exit off left the Env-marked Player running. Preserve/relaunch adopted the existing Player without a third launch. Hosts mode added the seven helper redirects, then normal exit removed them and restored the exact `/etc/hosts` hash. |
| 6. LaunchAgent and manual-proxy fallback | PASS | The per-user `com.fleasion.autostart` LaunchAgent was disabled, recreated through production sync, structurally verified as user-level/no-root-wrapper, and restored byte-for-byte. Blank manual HTTP CONNECT credentials reverted to Auto after ten seconds. Placeholder credentials kept `http_connect` active beyond ten seconds. |

## Defects found and fixed

1. External Froststrap/AppleBlox launches did not arm a fresh custom-FastFlag response. Their transient local files cannot carry illegal flags, and conditional ClientSettings requests could remain cached, so the first fresh intercepted response was not guaranteed. `MacBootstrapperBridge` now invokes `ProxyMaster.prepare_custom_fflags_for_player_launch()` whenever a bootstrapper launch rewrite is detected. This preserves AppleBlox/Froststrap allowlisted settings while reliably rearming the remote response path for illegal/custom flags.
2. The Windows relaunch path referenced `subprocess.CREATE_NO_WINDOW` during cross-platform lifecycle tests. It now uses a zero fallback when the host Python does not define that Windows-only constant.
3. The macOS startup test proxy stub lacked the production backend port. The test now uses `MACOS_PROXY_BACKEND_PORT`.

Regression coverage was added for the bootstrapper fresh-response preparation. Focused macOS/custom-flag/manual-proxy tests passed: `78 passed`. The complete x86_64 test suite passed: `505 passed, 5 warnings`.

The native arm64 virtualenv’s PyQt6 test collection cannot start because that bundled Qt build requires the `neon` CPU feature; this is a test-runtime limitation. The rebuilt arm64 package itself was launched and exercised live successfully, and the complete suite passed under the available x86_64/Rosetta environment.

## Final cleanup proof

- Fleasion, Player, Froststrap, and AppleBlox test processes were closed; the existing root proxy helper was left installed and running as intentional persistent state.
- `/etc/hosts` returned to baseline SHA-256 `c7dd0e2ed261ce76d76f852596c5b54026b9a894fa481381ffd399b556c0e2da`.
- Player CA returned to SHA-256 `62a3ac7d54d338c8684a5600046bc98c26456b32ec4b67290aec89824f0fe5c2`; Studio CA returned to SHA-256 `2eba303a7b3c87f0c52fa8f977b703ec6dea7de703f97644546d2d34e53e4cd9`.
- Fleasion settings, modifications, LaunchAgent, and autostart metadata match the preserved outside-checkout backups. The temporary read-only state and watcher are absent.
- The pre-test Player `ClientAppSettings.json` snapshot was restored exactly after the live custom-FastFlag tests.
- Final worktree contains only the intended source/test changes:

  ```text
   M src/fleasion/app.py
   M src/fleasion/modifications/macos_bootstrapper_bridge.py
   M src/fleasion/utils/platform_windows.py
   M tests/test_macos_bootstrapper_bridge.py
   M tests/test_proxy_macos_startup.py
  ```
