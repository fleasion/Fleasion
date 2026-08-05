# Fleasion Env Proxy — Windows release gate (round 2)

## Mission

You are the Windows validation-and-repair owner. You have no prior chat context. Work from the current checkout and decide whether its Roblox Env Proxy implementation is safe for an automatic Hosts File → Env Proxy migration in the following release.

Test real behavior, fix reproducible in-scope defects, add regression coverage, rebuild, and retest. Write the result to `report_windows_round2.md`.

Do **not** implement automatic migration. That is the final change after this report and the macOS report are reviewed.

## Read this before doing anything

- Read `AGENTS.md`, this file, and the current diff. `report_windows.md` is historical evidence, not a substitute for retesting.
- Preserve existing worktree changes. Do not reset or overwrite them.
- Do not commit, push, create a branch, or open a PR.
- Test the packaged build after source tests. A source-only pass is insufficient.
- Do not inspect or report cookies, auth tickets, private keys, usernames, or other secrets.
- Keep a backup of every Roblox/Fleasion file you deliberately change until the report is accepted.

## The user explicitly authorizes these controlled tests

These are requested release tests, not reasons to mark the task blocked:

- Start, restart, and close Fleasion, Roblox **Player**, and Roblox Studio.
- Ask the user to sign in, click through Roblox, or join a harmless experience when human interaction is needed. Continue other tests while waiting.
- Toggle Fleasion settings and switch Env/Hosts modes.
- Back up Player `ssl\cacert.pem`, then remove **only Fleasion certificate blocks** from that file to simulate a Roblox overwrite. Never delete or truncate the Mozilla/root bundle. Restore from backup if the test aborts.
- Add and remove one harmless test modification and custom FastFlag.
- Toggle Fleasion's read-only setting on those managed test files, inspect attributes, force-close the test Fleasion process once, and restore exact original attributes afterward.
- Create, run, inspect, and remove/recreate the Fleasion per-user scheduled task.
- Accept one UAC prompt when deliberately testing the narrowly scoped legacy-task or protected-install repair. An ordinary Env launch must not request UAC.

Do not create firewall rules, alter unrelated ACLs, modify Studio files, create Windows users, or reboot/sign out. Those are not required for this gate.

## Required behavior

- Env Proxy runs as the desktop user and listens only on `127.0.0.1:58443` and/or `[::1]:58443`.
- Env mode never edits the system hosts file and never needs an inbound public/private firewall exception.
- Player `cacert.pem` still receives and retains exactly one current Fleasion CA. Env mode does not depend on persistent CA read-only locking.
- Player is relaunched with the proxy environment. Studio is never relaunched, injected, CA-patched, warned about, or closed.
- A startup CA overwrite permits at most two repair/relaunches. A third overwrite must stop safely without another relaunch loop.
- `Lock Roblox Files to Read-Only` is off by default and covers only active modification/FastFlag targets, never `cacert.pem` or the entire installation.
- `Close Env-Proxied Roblox Player on Exit` remains a setting and defaults on. It affects Player only.
- Clean Fleasion restarts preserve an owned Env Player when safe. Switching Env → Hosts may close Player because its Env proxy is going away.
- Run on Boot uses `InteractiveToken` + `LeastPrivilege` for the original desktop user.
- Empty-credential manual HTTP CONNECT/SOCKS5 selections revert to Auto after ten seconds so an accidental selection does not leave Fleasion offline.

## Baseline and rollback

Before live testing, record:

- Windows version, account privilege level, branch/commit, and initial `git status --short`.
- Source and packaged Fleasion paths; Player and Studio versions/paths.
- Hash and attributes of Player and Studio `cacert.pem`.
- Fleasion settings backup and current proxy/read-only/close-on-exit/run-on-boot values.
- Existing `Fleasion_Autostart` task XML and Fleasion-named firewall rules.
- Player and Studio PIDs before each lifecycle test.

Make a timestamped backup outside the checkout. Restore temporary test changes at the end. Keep Fleasion CA installation itself; certificates are intentionally persistent.

## Gate 1 — automated and packaged checks

Run the repository-prescribed dependency, Ruff, full pytest, and build commands. Do not accept collection failures as “Linux-only”; the current tree is intended to collect on Windows and skip POSIX-only tests cleanly.

Then launch the newly built package non-elevated in Env mode and verify:

1. No UAC prompt.
2. Proxy readiness/TLS self-test succeeds.
3. Only loopback port 58443 is listening; nothing public and no port 443 listener from Env mode.
4. No hosts-file or firewall-rule mutation.
5. The Env information dialog can be dismissed immediately.

If any command fails, diagnose it. Fix product defects and tests in scope; do not merely list them.

## Gate 2 — real Player traffic and startup flags

Using the packaged build:

1. Enable Env Proxy and a harmless replacement plus a harmless custom FastFlag.
2. Launch Player normally, then once through a Roblox deeplink. Ask the user to join a harmless experience if needed.
3. Prove Player networking works and the proxy observes real Roblox traffic.
4. Prove the replacement is served or visibly applied.
5. Before Player consumes startup settings, prove the Windows flag cache contains the active custom override. Confirm the intercepted ClientSettings response also contains it.
6. Disable the custom flag and confirm Fleasion does not keep re-inserting it.

Discovery or startup logs alone are not enough for Player networking. Do not read Roblox authentication stores.

## Gate 3 — controlled CA overwrite ceiling

Use the current Fleasion CA and the repository's `_analyze_and_strip_fleasion_cas` helper to remove only Fleasion CA blocks. Use a temporary watcher/script capped at three injections and delete it afterward.

1. Back up Player `cacert.pem`; verify its non-Fleasion certificate count/hash baseline.
2. Start Player through packaged Env Proxy.
3. After each newly launched Player instance reaches the monitored startup window, strip only Fleasion CA blocks:
   - first injection → repair/relaunch 1;
   - second injection → repair/relaunch 2;
   - third injection → no third relaunch; Fleasion stops the owned Player and reports failure.
4. Verify no infinite loop, no duplicate Fleasion CA, and no loss of unrelated CA entries.
5. Start Player again normally and prove final CA health and networking recover.

If the file is protected, use only the product's explicit targeted repair path or one exact-file elevated write after the backup. Never grant broad access to `Roblox`, `Program Files`, or all users.

## Gate 4 — locking, updates, and exit ownership

1. With read-only locking off, apply the test modification/FastFlag and verify Fleasion does not set their files read-only. Verify an unrelated Player file remains writable/replaceable while Fleasion is open.
2. Turn locking on. Only active managed targets should become read-only; `cacert.pem`, directories, executables, and unrelated files must not.
3. Turn it off and verify exact original attributes return.
4. Force-close Fleasion once while locking is on, relaunch with it off, and verify persisted mode recovery cleans up the locks.
5. With close-on-exit on, normal Fleasion exit closes only the Player owned by this Env lifecycle.
6. With it off, Player remains open; record that it can no longer depend on the stopped local proxy.
7. Restart Fleasion from its own UI and verify an owned Player is preserved/adopted when no CA repair or mode switch requires a Player restart.
8. Switch Env → Hosts and confirm required cleanup occurs without touching Studio.

An actual Roblox update is useful if one is naturally available, but it is not required. The required proof is that default-off locking does not broadly prevent file replacement/update operations.

## Gate 5 — Studio, autostart, ACL identity, and firewall

1. Keep Studio open while starting/stopping Player and restarting/exiting Fleasion. Studio PID and CA hash must remain unchanged, no Studio warning may appear, and Studio networking must continue normally. Opening a place is enough; publishing is not required.
2. Toggle Run on Boot on. Inspect XML for the original desktop user, `InteractiveToken`, and `LeastPrivilege`; manually run the task, then toggle it off and verify removal. No sign-out is required.
3. If a legacy elevated task exists, exercise the one-time repair. If UAC uses a different administrator credential, verify the task still targets the original desktop user.
4. If a protected Player folder triggers the optional ACL repair, verify only explicitly failed Player install directories receive `Modify` for the original desktop-user SID. Existing ACLs remain, Studio is rejected, and the credential administrator is not granted instead.
5. Confirm Fleasion creates no firewall rule. A connection-failure action may open Windows Firewall settings, but must not mutate policy itself.
6. Select a blank-credential manual HTTP or SOCKS proxy and verify it reverts to Auto after ten seconds. Repeat with credentials and verify that configured selection remains active.

## Fix/retest rule

For every product defect:

1. Capture exact reproduction and logs.
2. Find the root cause.
3. Implement the narrow fix without weakening CA verification, widening elevation/ACL scope, touching Studio, raising the two-repair ceiling, or restoring persistent CA locks.
4. Add regression coverage where practical.
5. Re-run focused tests, full pytest, Ruff, build, and the affected packaged live test.

If user interaction is the only missing piece, ask for it plainly. Do not replace an available live test with speculation because it feels invasive; the controlled operations above are authorized and backed up.

## Report contract

Create `report_windows_round2.md` with:

- `READY_FOR_AUTOMATIC_MIGRATION: YES` or `NO` as the first verdict.
- Environment and exact commit/diff tested.
- One compact table for Gates 1–5 with PASS/FAIL and evidence.
- Defects, root causes, fixes, regression tests, and packaged retest evidence.
- Any remaining blocker stated precisely. A skipped required gate means `NO`.
- Final worktree diff summary and cleanup/rollback proof.

Say `YES` only if every required gate passes after all fixes.
