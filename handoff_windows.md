# Fleasion Env Proxy — Windows final-readiness handoff

## Mission

You are the Windows validation and repair owner for Fleasion's new Roblox Env Proxy path. You have no prior conversation context. Work from the current checkout; it contains the candidate implementation and may contain intentional uncommitted changes.

Your job is to:

1. Test the candidate on a real Windows 10/11 desktop with Roblox Player and Roblox Studio installed.
2. Diagnose and fix every reproducible in-scope problem you find.
3. Add regression tests for fixes whenever practical.
4. Rebuild and repeat the affected live tests after every fix.
5. Write the final results to `report_windows.md` in the repository root.

Do **not** implement automatic migration from Hosts File mode to Env Proxy. That is deliberately the final step after Windows and macOS validation reports are reviewed.

## Safety and repository rules

- Read `AGENTS.md` before working.
- Preserve all existing worktree changes. Never reset, discard, or overwrite changes you did not create.
- Do not commit, push, or open a PR unless the user separately asks.
- Before touching Roblox files, back up the exact files, modes/attributes, ownership, hashes, and relevant Fleasion settings.
- Use a disposable Roblox test account and a harmless test experience. Do not expose cookies, authentication tickets, certificate private keys, usernames, or other secrets in the report.
- Do not remove Fleasion's CA from Roblox or Windows as final cleanup. The intended behavior is to keep installed certificates.
- Do not leave temporary firewall rules, read-only attributes, test modifications, watchers, scheduled tasks, or forced permissions behind.
- An unexpected UAC prompt in Env mode is a failure to investigate. Do not approve it blindly.
- If a destructive or system-wide test is necessary, obtain user approval first.

## Candidate behavior that must remain true

- Env Proxy runs as the ordinary desktop user and binds only the loopback proxy at `127.0.0.1:58443`.
- Env Proxy still patches and verifies Roblox Player's `ssl/cacert.pem`; it does not install a Windows root certificate for Env mode.
- The proxy is considered ready only after bind and TLS self-test.
- Player launch performs CA preparation, then one controlled Env relaunch.
- If Roblox overwrites the CA during startup, Fleasion performs at most **two** automatic CA repair/relaunches. There must never be a third.
- Persistent read-only locking is off by default. Its scope is active modification/FastFlag files, not `cacert.pem`.
- `Close Env-Proxied Roblox Player on Exit` defaults on. It closes only the Player Fleasion owns. The off setting leaves Player open.
- Fleasion restart preserves the exact owned Player process when possible. A different/replacement Player must never be killed as if it were owned.
- Roblox Studio is unsupported by Fleasion interception in Env mode, but fully compatible: no Studio warning, relaunch, environment injection, CA modification, or exit cleanup.
- Run on Boot uses the per-user `Fleasion_Autostart` task with `InteractiveToken` and `LeastPrivilege`.
- Fleasion never installs Windows Firewall rules. Its blocked-connection dialog may open Windows Firewall settings.
- Switching back to Hosts File mode may request UAC and may close Player because the proxy mechanism changes.
- The Env-mode information dialog can be dismissed immediately; there is no ten-second timer.

Relevant implementation areas:

- `src/fleasion/proxy/env_lifecycle.py`
- `src/fleasion/proxy/master.py`
- `src/fleasion/utils/platform_windows.py`
- `src/fleasion/utils/autostart.py`
- `src/fleasion/modifications/manager.py`
- `src/fleasion/app.py`
- `src/fleasion/tray.py`
- `src/fleasion/gui/settings_tab.py`

## Test environment record

Record these at the top of `report_windows.md`:

- Windows edition, version, and OS build (`winver` and `Get-ComputerInfo`).
- CPU architecture.
- Roblox Player version and installation path.
- Roblox Studio version and installation path.
- Fleasion commit/branch and initial `git status --short`.
- Whether testing source, packaged build, or both.
- Whether a legacy elevated `Fleasion_Autostart` task existed.
- Whether the initial Fleasion settings came from a legacy Hosts File installation or a clean test profile.

## Phase 1 — static and automated gates

From a normal, non-elevated PowerShell in the repository root:

```powershell
uv sync --dev
uv run ruff check .
uv run pytest -q
uv run build
```

The pre-handoff baseline was 455 passed and 1 skipped. The exact count may increase if you add tests, but there must be no failures, hangs, fatal Qt errors, or abnormal process exit.

Locate and run the packaged `dist/Fleasion-v*.exe`. Live acceptance must be repeated against the packaged executable; source-only success is insufficient.

## Phase 2 — baseline and backups

1. Back up `%LOCALAPPDATA%\FleasionNT` and record the current `settings.json`.
2. Locate Player and Studio `ssl\cacert.pem` files. Back them up separately and record SHA-256 and file attributes.
3. Record Player and Studio PIDs before each lifecycle test.
4. Record existing Fleasion Task Scheduler XML and Fleasion-named firewall rules.
5. Start with `Lock Roblox Files to Read-Only` off.
6. Explicitly select Env Proxy in Settings. A fresh profile still defaulting to Hosts mode is expected in this candidate; automatic migration is intentionally absent.

Useful checks:

```powershell
Get-FileHash -Algorithm SHA256 'C:\path\to\cacert.pem'
(Get-Item 'C:\path\to\cacert.pem').Attributes
Get-CimInstance Win32_Process -Filter "Name='RobloxPlayerBeta.exe'" |
  Select-Object ProcessId, ExecutablePath, CommandLine
Get-CimInstance Win32_Process -Filter "Name='RobloxStudioBeta.exe'" |
  Select-Object ProcessId, ExecutablePath, CommandLine
Get-NetTCPConnection -LocalPort 58443 -State Listen
```

To inspect CA health using Fleasion's own parser, set `FLEASION_CACERT` to the Player bundle path and run:

```powershell
$env:FLEASION_CACERT = 'C:\path\to\Roblox\ssl\cacert.pem'
uv run python -c "import json,os; from pathlib import Path; from fleasion.proxy.master import _describe_cacert_state,get_ca_pem; from fleasion.utils.paths import PROXY_CA_DIR; p=Path(os.environ['FLEASION_CACERT']); print(json.dumps(_describe_cacert_state(p,get_ca_pem(PROXY_CA_DIR/'ca.crt')),indent=2))"
```

A healthy Player bundle has `healthy: true`, `fleasion_certs: 1`, and `current_fleasion_certs: 1`.

## Phase 3 — mandatory live acceptance matrix

Mark every item PASS, FAIL, or BLOCKED and attach concrete evidence.

### A. Ordinary-user startup and proxy function

1. Run source Fleasion from a non-elevated PowerShell. Confirm no UAC prompt.
2. Confirm port 58443 is listening only on loopback and owned by Fleasion.
3. Launch Roblox Player normally and by a browser/deeplink join.
4. Confirm Fleasion relaunches Player once with proxy environment variables and preserves the requested join target.
5. Confirm Player networking, login, joining, and ordinary asset loading work.
6. Exercise at least one harmless known replacement through Fleasion and verify the replacement in Roblox plus the corresponding Proxy-tab/log traffic.
7. Repeat against the packaged `.exe` from a normal Explorer/PowerShell launch.
8. Confirm the Env information dialog closes immediately without a countdown.

Failure conditions include UAC, a public-interface listener, a lost join request, a relaunch loop, Player starting without proxy variables, TLS errors, or proxy traffic without actual replacement behavior.

### B. CA health, overwrite repair, and hard ceiling

Use recoverable backups. A temporary watcher/script may use Fleasion's `_analyze_and_strip_fleasion_cas` helper to remove only Fleasion CA blocks from the Player bundle. Keep that watcher outside the repository and delete it afterward.

1. Healthy path: verify one current Fleasion CA before and after Player's full startup window, with exactly one normal Env relaunch.
2. One-overwrite path: remove only Fleasion's CA immediately after the Env Player starts. Verify Fleasion detects the unhealthy state, repairs it, relaunches, and reaches a healthy stable Player.
3. Two-overwrite path: remove the CA after the initial Env launch and again after repair relaunch 1. Verify exactly two repair relaunches occur, repair 2 becomes healthy, and no third relaunch occurs. Logs should include `Env Proxy CA repair relaunch 1/2` and `2/2`.
4. Ceiling path: in a separate run, remove the CA after the initial launch and after both repair relaunches. Verify Fleasion makes no third repair attempt, reports the failure, and terminates only the owned unusable Player.
5. Protected-path path: make a backed-up test Player CA unwritable to the ordinary user. Verify Fleasion does not relaunch Player through a CA it cannot verify and shows a useful protected-installation popup. Restore the exact original ACL/attributes afterward.
6. Verify disabling proxy features and exiting Fleasion leaves the valid CA installed.

For every repair test, report Player PID transitions, timestamps, CA health snapshots, and the relevant lifecycle log lines.

### C. Read-only behavior and Roblox update compatibility

1. Confirm both new settings defaults: read-only lock off; close-on-exit on.
2. With the lock off, apply a harmless modification and FastFlag, then verify Fleasion does not set their targets read-only.
3. Trigger a real Roblox update/reinstall while Fleasion remains open and Player is closed. It must complete successfully.
4. Launch updated Player and verify CA repair plus Env interception still work.
5. Enable `Lock Roblox Files to Read-Only`. Verify only active modification/FastFlag targets become read-only. `cacert.pem` must not change attributes because of this toggle.
6. Disable the toggle and verify every tracked target returns to its exact recorded original attributes/mode.
7. Test one-time legacy cleanup using backed-up legacy-read-only Player modification, FastFlag, and CA files. With the new toggle off and migration marker absent, launch once and verify stale locks clear safely; subsequent launches must not broadly rewrite unrelated attributes.
8. Force-close Fleasion once while the toggle is enabled, then relaunch with the setting disabled. Verify `read_only_modes.json` restores exact recorded modes and is cleaned up.

### D. Player ownership, exit, and restart

1. With default close-on-exit enabled, launch an Env-proxied Player, exit Fleasion, and verify that owned Player closes before port 58443 disappears.
2. Disable close-on-exit, repeat, and verify Player remains open while Fleasion and its listener exit.
3. Start Fleasion again and return the setting to its default.
4. With an owned Env Player running, start a replacement Fleasion process with `--kill-others --preserve-env-proxy-player`. Verify the old Fleasion exits, the new one adopts the same Player PID, and Player is neither killed nor relaunched.
5. Replace/close the owned Player outside Fleasion and start a different Player generation. Exit Fleasion and verify it does not kill a process whose ownership token no longer matches.
6. Switch Env to Hosts mode. Verify the intentional mode-switch restart can close Player and requests UAC for Hosts operation.

### E. Roblox Studio isolation

1. Record Studio PID, command line, environment if accessible, and Studio `cacert.pem` hash.
2. Run Studio before and during Env Proxy operation.
3. Launch/close Player and restart/exit Fleasion while Studio stays open.
4. Verify Studio is never relaunched or terminated, receives no Env proxy injection, shows no Fleasion Studio warning, and its CA hash does not change.
5. Verify Studio can open a place and use its network normally throughout.

### F. Run on Boot without elevation

1. If possible, begin with a copy of the legacy highest-available task to test upgrade behavior safely.
2. Toggle Run on Boot on from a non-elevated Fleasion.
3. Export the task XML and verify `InteractiveToken` plus `<RunLevel>LeastPrivilege</RunLevel>`.
4. Confirm the task points at the current source/package launch method.
5. Run the task manually, then perform a real sign-out/sign-in or reboot test. Fleasion must start without UAC.
6. Toggle Run on Boot off and verify the task is actually gone. A failure to delete an inaccessible legacy elevated task must be reported to the user rather than silently claiming success.

```powershell
schtasks /Query /TN Fleasion_Autostart /XML
schtasks /Run /TN Fleasion_Autostart
```

### G. Firewall behavior

1. Snapshot Fleasion-named Windows Firewall rules before testing.
2. Exercise Env Proxy normally and verify no new inbound or outbound Fleasion rules appear.
3. Confirm loopback proxy operation does not depend on a public/private inbound exception.
4. Through a safe mocked UI test or a temporary, approved outbound-block scenario, verify the failure dialog offers Windows Firewall settings and never offers/executes automatic `netsh` rule installation.
5. Remove any temporary block immediately and prove the final firewall state matches the baseline.

## Phase 4 — fix policy

If anything fails:

1. Reproduce it at least twice and capture exact logs/state.
2. Determine whether it is Fleasion, Roblox, packaging, permissions, or test-environment specific.
3. Fix in scope without weakening CA verification, adding broad elevation, touching Studio, increasing the two-repair ceiling, or reintroducing persistent CA locking/firewall mutation.
4. Add a focused regression test.
5. Run the focused tests, `uv run ruff check .`, and `uv run pytest -q`.
6. Rebuild with `uv run build` and repeat the failed live scenario against the package.
7. Document every changed file and why in `report_windows.md`.

Do not paper over a failure by increasing sleeps or retries without state-based evidence.

## Required `report_windows.md` structure

```markdown
# Windows Env Proxy validation report

## Verdict
READY_FOR_AUTOMATIC_MIGRATION: YES | NO

## Environment
...

## Initial repository state
...

## Automated gates
- Ruff: ...
- Pytest: ...
- Package build: ...

## Live acceptance results
| ID | Test | Result | Evidence |
|---|---|---|---|
| A1 | Non-elevated startup | PASS/FAIL/BLOCKED | ... |
...

## Defects found
### DEFECT-WIN-001: ...
- Reproduction
- Root cause
- User impact

## Fixes made
### FIX-WIN-001: ...
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

Only write `READY_FOR_AUTOMATIC_MIGRATION: YES` if every mandatory live section passes on the packaged build, all automated gates are green, all temporary system changes are cleaned up, and no unresolved Env Proxy blocker remains. Otherwise write `NO` and state the exact blocker.
