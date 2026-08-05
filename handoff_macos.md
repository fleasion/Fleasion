# Fleasion Env Proxy — macOS release gate (round 2)

## Mission

You are the macOS validation-and-repair owner. You have no prior chat context. Work from the current checkout and decide whether its Roblox Env Proxy implementation is safe for an automatic Hosts File → Env Proxy migration in the following release.

Test real behavior, fix reproducible in-scope defects, add regression coverage, rebuild, and retest. Write the result to `report_macos_round2.md`.

Do **not** implement automatic migration. That is the final change after this report and the Windows report are reviewed.

## Read this before doing anything

- Read `AGENTS.md`, this file, and the current diff. `report_macos.md` is historical evidence, not a substitute for retesting.
- Preserve existing worktree changes. Do not reset or overwrite them.
- Do not commit, push, create a branch, or open a PR.
- Never run the Fleasion GUI with `sudo` or as root.
- Test the rebuilt universal packaged app after source tests. A source-only or architecture-inspection-only pass is insufficient.
- Do not inspect or report cookies, auth tickets, private keys, usernames, or other secrets.
- Keep a backup of every Roblox/Fleasion file you deliberately change until the report is accepted.

## The user explicitly authorizes these controlled tests

These are requested release tests, not reasons to mark the task blocked:

- Start, restart, and close Fleasion, Roblox **Player**, and Roblox Studio.
- Ask the user to sign in, click through Roblox, or join a harmless experience when human interaction is needed. Continue other tests while waiting.
- Toggle Fleasion settings and switch Env/Hosts modes.
- Back up Player `Contents/Resources/ssl/cacert.pem`, then remove **only Fleasion certificate blocks** to simulate a Roblox overwrite. Never delete or truncate the public-root bundle. Restore from backup if the test aborts.
- Add and remove one harmless test modification and custom FastFlag.
- Toggle Fleasion's read-only setting on those managed test files, inspect modes/flags, force-close the test Fleasion process once, and restore exact original state afterward.
- Temporarily stop/restart the existing Fleasion helper or temporarily make the exact Player CA target unwritable when needed to distinguish direct patching from helper fallback. Preserve and restore exact launchd, owner, mode, ACL, flag, and xattr state.
- Create, load, run, inspect, unload, and restore the Fleasion per-user LaunchAgent.
- Approve one administrator prompt when deliberately testing protected-install helper fallback. Ordinary writable-install Env startup must not prompt.

Do not modify Studio files, install system-keychain trust, alter unrelated `/Applications` contents, or reboot/sign out. Those are not required for this gate.

## Required behavior

- The Env Proxy GUI runs as the logged-in user and listens only on `127.0.0.1:58443` and/or `[::1]:58443`.
- Env mode never edits `/etc/hosts` and never binds port 443.
- Player `cacert.pem` receives exactly one current Fleasion CA; the Fleasion CA is trusted in the **login** keychain, not installed into the system keychain by Env mode.
- Direct user-mode Player CA patching is attempted first. The root helper is fallback only when the active Player target is genuinely protected.
- Player is relaunched with the proxy environment. Studio is never relaunched, injected, CA-patched, warned about, or closed.
- A startup CA overwrite permits at most two repair/relaunches. A third overwrite stops safely without another loop.
- `Lock Roblox Files to Read-Only` is off by default and covers only active modification/FastFlag targets, never `cacert.pem` or an app bundle broadly.
- `Close Env-Proxied Roblox Player on Exit` remains a setting and defaults on. It affects Player only.
- Clean Fleasion restarts preserve an owned Env Player when safe. Switching Env → Hosts may close Player because its Env proxy is going away.
- Run on Boot is an unprivileged per-user LaunchAgent.
- Empty-credential manual HTTP CONNECT/SOCKS5 selections revert to Auto after ten seconds so an accidental selection does not leave Fleasion offline.

## Baseline and rollback

Before live testing, record:

- macOS version/build, architecture, Rosetta availability, branch/commit, and initial `git status --short`.
- Source and package paths; Player/Studio versions, bundle/resource paths, owner, mode, ACL, flags, and xattrs.
- Hash/state of Player and Studio `cacert.pem`.
- Fleasion settings backup and current proxy/read-only/close-on-exit/run-on-boot values.
- Existing Fleasion helper/LaunchDaemon and LaunchAgent state.
- Froststrap/AppleBlox paths if installed.
- Player and Studio PIDs before each lifecycle test.

Make a timestamped backup outside the checkout. Restore temporary test changes at the end. Keep valid Fleasion CA/login-keychain trust and an already-installed helper; those are intentional persistent state.

## Gate 1 — automated, universal build, and packaged startup

Run the repository-prescribed dependency, Ruff, full pytest, and build commands.

For the new universal release app, verify both arm64 and x86_64 slices and required helper payloads. Then launch the package normally as the user (Finder and Terminal, one each) in Env mode and verify:

1. No administrator prompt on an ordinary writable Player install.
2. Proxy readiness/TLS self-test and login-keychain trust succeed.
3. Only loopback port 58443 is listening; no public listener or Env-mode port 443.
4. `/etc/hosts` is unchanged.
5. The Env information dialog can be dismissed immediately.

If any command fails, diagnose it. Fix product defects and tests in scope; do not merely list them.

## Gate 2 — direct CA path and protected fallback

Exercise both paths deliberately:

1. On a user-writable Player CA target, stop or bypass the helper and prove direct CA patch/verification succeeds with no administrator prompt.
2. Restore helper state. Make only the exact active Player CA target genuinely unwritable by the user (or use an already-protected real install), preserving its original metadata.
3. Prove direct patch fails first, then the product offers/uses the helper fallback after one approval.
4. Relaunch Fleasion and prove the installed helper is reused without another approval.
5. Restore exact target metadata and verify final CA health.

The GUI must remain the normal user throughout. Do not infer direct-first behavior merely because a helper is installed.

## Gate 3 — real Player traffic, bootstrappers, and startup flags

Using the rebuilt packaged app:

1. Enable Env Proxy and a harmless replacement plus harmless custom FastFlag.
2. Launch Player normally and through a Roblox deeplink. Ask the user to join a harmless experience if needed.
3. Prove Player networking works and the proxy observes real Roblox traffic.
4. Prove the replacement is served or visibly applied.
5. Before Player startup, verify the active custom override and one-second dynamic-reload companion are seeded into Player `Contents/Resources/ClientSettings/ClientAppSettings.json`. Studio must remain unchanged.
6. Disable/change the custom flag and prove stale Fleasion-seeded values are removed or updated.
7. If Froststrap or AppleBlox is installed, launch through each available bootstrapper and prove resource rewrites are reseeded before Player consumes startup settings.

Discovery or architecture checks alone do not pass this gate. Do not read Roblox authentication stores.

## Gate 4 — controlled CA overwrite ceiling

Use the current Fleasion CA and `_analyze_and_strip_fleasion_cas` to remove only Fleasion CA blocks. Use a temporary watcher/script capped at three injections and delete it afterward.

1. Back up Player `cacert.pem`; verify its non-Fleasion certificate count/hash baseline.
2. Start Player through packaged Env Proxy.
3. After each newly launched Player instance reaches the monitored startup window, strip only Fleasion CA blocks:
   - first injection → repair/relaunch 1;
   - second injection → repair/relaunch 2;
   - third injection → no third relaunch; Fleasion stops the owned Player and reports failure.
4. Verify no infinite loop, duplicate Fleasion CA, or loss of unrelated CA entries.
5. Start Player normally again and prove final CA health and networking recover.

If an exact-file privileged write is required, it is authorized after the backup. Never modify the whole app bundle or public-root content.

## Gate 5 — locking, exit ownership, and Studio isolation

1. With read-only locking off, apply the test modification/FastFlag and verify Fleasion does not set them read-only. Verify an unrelated Player file can be atomically replaced while Fleasion is open.
2. Turn locking on. Only active managed targets should become read-only; `cacert.pem`, directories, executables, unrelated files, and Studio must not.
3. Turn it off and verify exact original modes/flags return.
4. Force-close Fleasion once while locking is on, relaunch with it off, and verify persisted-mode recovery clears the locks.
5. With close-on-exit on, normal Fleasion exit closes only the owned Player. With it off, Player remains open.
6. Restart Fleasion from its UI and verify an owned Player is preserved/adopted when no repair/mode switch requires a restart.
7. Keep Studio open through Player launches and Fleasion restart/exit. Studio PID, CA hash/mode, and network operation remain unchanged; no Studio warning appears. Opening a place is enough; publishing is not required.
8. Switch Env → Hosts and confirm required Player cleanup occurs without touching Studio.

An actual Roblox update is useful if naturally available but is not required. The required proof is that default-off locking does not broadly prevent file replacement/update operations.

## Gate 6 — LaunchAgent and manual-proxy fallback

1. Toggle Run on Boot on and verify the plist is user-owned, contains no sudo/root wrapper, and points at the current build/checkout.
2. Load/kickstart it manually, verify a normal-user Fleasion launch, then unload/toggle it off and verify removal. No logout/reboot is required.
3. Select a blank-credential manual HTTP or SOCKS proxy and verify it reverts to Auto after ten seconds. Repeat with credentials and verify that configured selection remains active.

## Fix/retest rule

For every product defect:

1. Capture exact reproduction and logs.
2. Find the root cause.
3. Implement the narrow fix without weakening CA verification, running the GUI as root, touching Studio, raising the two-repair ceiling, or restoring persistent CA locking.
4. Add regression coverage where practical.
5. Re-run focused tests, full pytest, Ruff, universal build, and the affected packaged live test.

If user interaction is the only missing piece, ask for it plainly. Do not replace an available live test with speculation because it feels invasive; the controlled operations above are authorized and backed up.

## Report contract

Create `report_macos_round2.md` with:

- `READY_FOR_AUTOMATIC_MIGRATION: YES` or `NO` as the first verdict.
- Environment, architectures actually live-tested, and exact commit/diff.
- One compact table for Gates 1–6 with PASS/FAIL and evidence.
- Defects, root causes, fixes, regression tests, and packaged retest evidence.
- Any remaining blocker stated precisely. A skipped required gate means `NO`.
- Final worktree diff summary and cleanup/rollback proof.

Say `YES` only if every required gate passes after all fixes.
