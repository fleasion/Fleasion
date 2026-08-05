# Fleasion Env Proxy — macOS final-readiness handoff

## Mission

You are the macOS validation and repair owner for Fleasion's new Roblox Env Proxy path. You have no prior conversation context. Work from the current checkout; it contains the candidate implementation and may contain intentional uncommitted changes.

Your job is to:

1. Test the candidate on a real supported macOS desktop with Roblox Player and Roblox Studio installed.
2. Diagnose and fix every reproducible in-scope problem you find.
3. Add regression tests for fixes whenever practical.
4. Rebuild and repeat the affected live tests after every fix.
5. Write the final results to `report_macos.md` in the repository root.

Do **not** implement automatic migration from Hosts File mode to Env Proxy. That is deliberately the final step after Windows and macOS validation reports are reviewed.

## Safety and repository rules

- Read `AGENTS.md` before working.
- Preserve all existing worktree changes. Never reset, discard, or overwrite changes you did not create.
- Do not commit, push, or open a PR unless the user separately asks.
- Never run the Fleasion GUI with `sudo` or as root.
- Before touching Roblox files, back up the exact files, modes, ownership, extended attributes, hashes, and relevant Fleasion settings.
- Use a disposable Roblox test account and a harmless test experience. Do not expose cookies, authentication tickets, certificate private keys, usernames, or other secrets in the report.
- Do not remove Fleasion's CA, login-keychain trust, or an already-installed helper as final cleanup. The intended behavior is to keep installed certificates/helper state.
- Do not leave temporary ACL/mode changes, read-only flags, test modifications, watchers, or LaunchAgent changes behind.
- An administrator prompt during ordinary writable-install Env startup is a failure to investigate. A single approval is permitted only for installing/upgrading the helper after direct CA patching proves the active Roblox installation is protected.
- If Rosetta, root-owned test permissions, logout/reboot, or another system-wide action is needed, obtain user approval first.

## Candidate behavior that must remain true

- Env Proxy GUI runs as the ordinary logged-in user and binds only the loopback proxy at `127.0.0.1:58443`.
- Env Proxy still patches and verifies Roblox Player's `Contents/Resources/ssl/cacert.pem` and trusts the Fleasion CA in the user's login keychain.
- Direct user-mode Player CA patching is attempted first. The privileged helper is only a fallback for a protected active installation.
- The proxy is considered ready only after bind and TLS self-test.
- Player launch performs CA preparation, then one controlled Env relaunch using `open --env`.
- If Roblox overwrites the CA during startup, Fleasion performs at most **two** automatic CA repair/relaunches. There must never be a third.
- Persistent read-only locking is off by default. Its scope is active modification/FastFlag files, not `cacert.pem`.
- `Close Env-Proxied Roblox Player on Exit` defaults on. It closes only the Player Fleasion owns.
- Fleasion restart preserves the exact owned Player process when possible. A different/replacement Player must never be killed as if it were owned.
- Roblox Studio is unsupported by Fleasion interception in Env mode, but fully compatible: no Studio warning, relaunch, environment injection, CA modification, or exit cleanup.
- Run on Boot is an unprivileged per-user LaunchAgent.
- Switching back to Hosts File mode may use the existing helper and may close Player because the proxy mechanism changes.
- The Env-mode information dialog can be dismissed immediately; there is no ten-second timer.

Relevant implementation areas:

- `src/fleasion/proxy/env_lifecycle.py`
- `src/fleasion/proxy/master.py`
- `src/fleasion/utils/platform_macos.py`
- `src/fleasion/utils/macos_proxy_helper.py`
- `src/fleasion/utils/autostart.py`
- `src/fleasion/modifications/manager.py`
- `src/fleasion/app.py`
- `src/fleasion/tray.py`
- `src/fleasion/gui/settings_tab.py`

## Test environment record

Record these at the top of `report_macos.md`:

- macOS version/build (`sw_vers`).
- Hardware model and architecture (`uname -m`, `sysctl -n machdep.cpu.brand_string`).
- Whether Rosetta is installed and which Fleasion slices were built/tested.
- Roblox Player version, app path, owner, and resource path.
- Roblox Studio version, app path, owner, and resource path.
- Whether Froststrap, AppleBlox, or another custom Roblox manager is present.
- Fleasion commit/branch and initial `git status --short`.
- Whether testing source, packaged app, or both.
- Whether the legacy Fleasion helper/LaunchDaemon was already installed.
- Whether the initial settings came from a legacy Hosts File installation or a clean test profile.

## Phase 1 — static, automated, and package gates

From a normal Terminal in the repository root:

```bash
uv sync --dev
uv run ruff check .
uv run pytest -q
uv run build
```

The pre-handoff baseline was 455 passed and 1 skipped. The exact count may increase if you add tests, but there must be no failures, hangs, fatal Qt errors, or abnormal process exit.

On Apple Silicon, the release build is expected to be universal and normally requires Rosetta. Verify every packaged Mach-O has the expected slices using the build's own verification plus `lipo -info`. If Rosetta is unavailable, ask before installing it; otherwise mark universal packaging BLOCKED rather than silently substituting a source-only test.

Run the packaged `dist/Fleasion-v*.app` (and its `dist/Fleasion.app` mirror if present). Live acceptance must be repeated against the packaged app; source-only success is insufficient.

## Phase 2 — baseline and backups

1. Back up `~/Library/Application Support/FleasionNT` and record the current `settings.json`.
2. Locate Player and Studio `Contents/Resources/ssl/cacert.pem`. Back them up separately and record SHA-256, mode, owner/group, and relevant ACL/extended attributes.
3. Record Player and Studio PIDs before each lifecycle test.
4. Record helper/LaunchDaemon state, login-keychain Fleasion CA state, and the autostart LaunchAgent.
5. Start with `Lock Roblox Files to Read-Only` off.
6. Explicitly select Env Proxy in Settings. A fresh profile still defaulting to Hosts mode is expected in this candidate; automatic migration is intentionally absent.

Useful checks:

```bash
shasum -a 256 '/Applications/Roblox.app/Contents/Resources/ssl/cacert.pem'
stat -f '%Lp %Su:%Sg %N' '/Applications/Roblox.app/Contents/Resources/ssl/cacert.pem'
ls -leO@ '/Applications/Roblox.app/Contents/Resources/ssl/cacert.pem'
pgrep -lf 'RobloxPlayer|RobloxStudio|Fleasion'
lsof -nP -iTCP:58443 -sTCP:LISTEN
security find-certificate -a -c 'Fleasion Proxy CA' ~/Library/Keychains/login.keychain-db
launchctl print "gui/$(id -u)/com.fleasion.autostart"
```

To inspect CA health using Fleasion's own parser:

```bash
export FLEASION_CACERT='/Applications/Roblox.app/Contents/Resources/ssl/cacert.pem'
uv run python -c "import json,os; from pathlib import Path; from fleasion.proxy.master import _describe_cacert_state,get_ca_pem; from fleasion.utils.paths import PROXY_CA_DIR; p=Path(os.environ['FLEASION_CACERT']); print(json.dumps(_describe_cacert_state(p,get_ca_pem(PROXY_CA_DIR/'ca.crt')),indent=2))"
```

A healthy Player bundle has `healthy: true`, `fleasion_certs: 1`, and `current_fleasion_certs: 1`.

## Phase 3 — mandatory live acceptance matrix

Mark every item PASS, FAIL, or BLOCKED and attach concrete evidence.

### A. Ordinary-user startup, direct patch, and proxy function

1. Run source Fleasion as the normal user. Confirm the GUI refuses root and that ordinary Env startup does not request administrator approval when Player files are writable.
2. Confirm port 58443 is listening only on loopback and owned by the normal-user Fleasion process.
3. Confirm the login-keychain CA becomes trusted without system-keychain/root installation. Record any normal keychain authorization prompt separately from an administrator/helper prompt.
4. With the helper stopped or merely idle, prove logs show direct Player CA patch success and no helper request for a writable installation.
5. Launch Player normally and by a browser/deeplink join. Confirm exactly one normal Env relaunch through `open --env` and preservation of the requested join target.
6. Confirm Player networking, login, joining, and ordinary asset loading work.
7. Exercise at least one harmless known replacement and verify the replacement in Roblox plus corresponding Proxy-tab/log traffic.
8. Repeat against the packaged universal app.
9. Confirm the Env information dialog closes immediately without a countdown.

Failure conditions include an unnecessary admin prompt, root-owned GUI, public-interface listener, lost join target, relaunch loop, Player without proxy variables, TLS errors, or traffic without actual replacement behavior.

### B. Direct-first helper fallback and protected installations

1. Record whether the helper is installed and ready before the test.
2. On a writable standard Player installation, verify direct patching succeeds even if a helper exists. Merely having a helper installed must not cause it to be used first.
3. Using a backed-up Player installation/path and user approval, make only the active Player `cacert.pem` genuinely unwritable by the normal user while preserving a restoration record.
4. Verify direct patch fails cleanly, Fleasion offers `Install Helper and Retry`, and it does not start/relaunch Player through an unverified CA.
5. Approve helper installation once. Verify retry patches and fully verifies the protected Player CA, then Env Proxy works.
6. Relaunch Fleasion and Player. No repeat approval should be needed while helper version/state remains current.
7. Restore the exact original Player owner/mode/ACL if the test changed them. Keep the valid installed helper and Fleasion CA, as intended.
8. If using Froststrap or AppleBlox, repeat discovery/direct-or-helper behavior for the active managed Player path and verify restore snapshots remain coherent.

### C. CA health, overwrite repair, and hard ceiling

Use recoverable backups. A temporary watcher/script may use Fleasion's `_analyze_and_strip_fleasion_cas` helper to remove only Fleasion CA blocks from the Player bundle. Keep the watcher outside the repository and delete it afterward.

1. Healthy path: verify one current Fleasion CA before and after Player's full startup window, with exactly one normal Env relaunch.
2. One-overwrite path: remove only Fleasion's CA immediately after the Env Player starts. Verify detection, repair, relaunch, and a healthy stable Player.
3. Two-overwrite path: remove the CA after the initial Env launch and again after repair relaunch 1. Verify exactly two repair relaunches, repair 2 becomes healthy, and no third relaunch. Logs must include `Env Proxy CA repair relaunch 1/2` and `2/2`.
4. Ceiling path: in a separate run, remove the CA after the initial launch and after both repair relaunches. Verify no third repair, a useful failure report, and termination of only the owned unusable Player.
5. Verify disabling proxy features and exiting Fleasion leaves the valid CA and login-keychain trust installed.

For every repair test, report Player PID/generation transitions, timestamps, CA health snapshots, and relevant lifecycle logs.

### D. Read-only behavior and Roblox update compatibility

1. Confirm new-setting defaults: read-only lock off; close-on-exit on.
2. With the lock off, apply a harmless modification and FastFlag; verify targets are not changed to read-only by Fleasion.
3. Trigger a real Roblox update/reinstall while Fleasion remains open and Player is closed. It must complete successfully.
4. Launch updated Player and verify CA repair plus Env interception still work.
5. Enable `Lock Roblox Files to Read-Only`. Verify only active modification/FastFlag targets become read-only. `cacert.pem` mode/flags must not change because of this toggle.
6. Disable it and verify every tracked target returns to its exact recorded original mode.
7. Test one-time legacy cleanup using backed-up legacy-read-only modification, FastFlag, and CA files. With the toggle off and migration marker absent, launch once and verify stale locks clear; later launches must not broadly chmod unrelated files.
8. Force-close Fleasion once with locking enabled, relaunch with it disabled, and verify `read_only_modes.json` restores exact modes and is cleaned up.

### E. Player ownership, exit, and restart

1. With default close-on-exit enabled, launch an Env-proxied Player, exit Fleasion, and verify its owned Player closes before port 58443 disappears.
2. Disable close-on-exit, repeat, and verify Player remains open while Fleasion/listener exit.
3. Restart Fleasion and return the setting to default.
4. With an owned Env Player running, start the packaged executable (or source entry point) with `--kill-others --preserve-env-proxy-player`. Verify the old Fleasion exits, the new one adopts the same Player PID/generation, and Player is neither killed nor relaunched.
5. Replace/close the owned Player outside Fleasion and start a different generation. Exit Fleasion and verify it does not kill a Player whose ownership token no longer matches.
6. Switch Env to Hosts mode. Verify the intentional mode-switch restart can close Player and uses the helper only as required by Hosts operation.

### F. Roblox Studio isolation

1. Record Studio PID, command, and Studio `cacert.pem` hash/mode.
2. Run Studio before and during Env Proxy operation.
3. Launch/close Player and restart/exit Fleasion while Studio remains open.
4. Verify Studio is never relaunched or terminated, receives no proxy environment injection, shows no Fleasion Studio warning, and its CA hash/mode does not change.
5. Verify Studio can open a place, publish/use its network, and remain operational throughout.

### G. Run on Boot without elevation

1. Toggle Run on Boot on from the normal-user GUI.
2. Inspect `~/Library/LaunchAgents/com.fleasion.autostart.plist`; it must be user-owned, point to the current launch method, and require no sudo/helper action.
3. Run the LaunchAgent manually, then perform an approved real logout/login or reboot test. Fleasion must start in the user session without an administrator prompt.
4. Confirm any already-installed privileged helper starts independently and is not required merely to launch Env GUI.
5. Toggle Run on Boot off and verify the user LaunchAgent unloads and disappears. Failures must be reported rather than silently claimed as success.

### H. Packaging and architecture

1. Verify the package contains the expected Fleasion executable, helper payloads, Qt/plugins, CA dependencies, and both architectures in a universal build.
2. Launch the packaged app normally through Finder and Terminal.
3. Repeat ordinary Env launch, CA repair, Player exit, Player-preserving restart, and Studio-isolation smoke tests against the package.
4. If only one hardware architecture is available for live testing, state it explicitly. Do not claim the other architecture was live-tested merely because `lipo` reports its slice.

## Phase 4 — fix policy

If anything fails:

1. Reproduce it at least twice and capture exact logs/state.
2. Determine whether it is Fleasion, Roblox, packaging, permissions, helper, keychain, or test-environment specific.
3. Fix in scope without weakening CA verification, running the GUI as root, using the helper before direct patching, touching Studio, increasing the two-repair ceiling, or reintroducing persistent CA locking.
4. Add a focused regression test.
5. Run focused tests, `uv run ruff check .`, and `uv run pytest -q`.
6. Rebuild with `uv run build` and repeat the failed live scenario against the package.
7. Document every changed file and why in `report_macos.md`.

Do not paper over a failure by increasing sleeps or retries without state-based evidence.

## Required `report_macos.md` structure

```markdown
# macOS Env Proxy validation report

## Verdict
READY_FOR_AUTOMATIC_MIGRATION: YES | NO

## Environment
...

## Initial repository state
...

## Automated gates
- Ruff: ...
- Pytest: ...
- Universal/package build: ...

## Live acceptance results
| ID | Test | Result | Evidence |
|---|---|---|---|
| A1 | Normal-user startup | PASS/FAIL/BLOCKED | ... |
...

## Defects found
### DEFECT-MAC-001: ...
- Reproduction
- Root cause
- User impact

## Fixes made
### FIX-MAC-001: ...
- Files changed
- Implementation
- Regression test
- Live retest evidence

## Remaining risks or blockers
...

## Final git diff summary
...

## Cleanup performed
...
```

Only write `READY_FOR_AUTOMATIC_MIGRATION: YES` if every mandatory live section passes on the packaged app, all automated gates are green, all temporary system changes are cleaned up, and no unresolved Env Proxy blocker remains. Otherwise write `NO` and state the exact blocker.
