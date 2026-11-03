# Linux Roblox client support

Fleasion's Linux client registry distinguishes **Sober** (Flatpak) from
**Rosemary** (native macOS Roblox Player via Rosemary/Nutmeg). The settings
selector offers Auto, Sober, and Rosemary; explicitly selecting an unavailable
client does not silently fall back to another.

## Discovery

- **Sober:** Fleasion runs `flatpak info org.vinegarhq.Sober` to confirm the
  installation. Leftover Flatpak data directories do not count as installed.
  Running processes are checked against Sober's Flatpak cgroup.
- **Rosemary:** Fleasion inspects native Rosemary launch arguments in `/proc`,
  identifying a **Roblox.app** bundle that contains
  `Contents/MacOS/RobloxPlayer` and `Contents/Resources/ssl/cacert.pem`.
  Studio, crash handlers, and other Rosemary guests are excluded. Nutmeg's
  desktop entry and version storage are also detected. The executable and
  bundle may be anywhere: enter their full paths in Settings if automatic
  discovery cannot find a stopped custom installation.

Auto prefers a running Rosemary Player, then registered URI handlers, then the
registry's deterministic fallback. Once Env Proxy is armed, that session's
chosen client remains selected until the proxy is stopped or the setting is
changed. This avoids routing requests from one client while an override is
armed for a different client.

## Launch and proxy behavior

Sober launches through `flatpak run`; its Env Proxy is configured using an
app-scoped Flatpak override and removed when Fleasion stops or changes
clients. Rosemary continues to start through Nutmeg or any other normal
launcher. While Env Proxy is active, Fleasion watches for **new** Rosemary
Player processes, captures their exact argv (including one-time join URLs)
and inherited environment, and replays them with Fleasion's proxy settings.
It does not modify Nutmeg or require switching launchers. Studio, helper
processes and Players already open when Fleasion starts are not interrupted.

Nutmeg monitors startup for five seconds. For positively identified Nutmeg
launches, Fleasion suspends the original Player until that grace period
ends, then replays it without making Nutmeg report a spurious startup failure.
If Fleasion exits during that interval, the original Player is resumed.
Direct launches from Fleasion are also supported and receive the proxy
environment without the watcher. No global system proxy settings are changed.

The macOS Player must **honor** the inherited variables for Env Proxy routing
to work. A live Nutmeg/Rosemary test confirmed successful process replay and
proxy variable inheritance, but the Player continued connecting directly
to Roblox over port 443. Rosemary's CFNetwork compatibility functions
report no system proxy, and its native network paths may bypass conventional
proxy variables. Therefore cache scraper/replacer interception in Env Proxy
mode remains unverified until Rosemary's guest networking is adjusted or
another routing method is used. This is distinct from live TLS secret recovery.

Linux hosts-file interception and system trust installation have separate
privilege requirements. Network interception over Rosemary must be verified
with a live launch before treating Env Proxy/TLS behavior as confirmed.

Cordial, Mocktail and Nightcap are **not currently registered Linux launcher
backends**. Their existing TLS backend identifiers are separate from the
application-installation, process, resource and launcher APIs described here.
The live TLS key backend and splice transport are implemented separately.


## Native macOS Player cookies

With Rosemary selected, Fleasion can read Nutmeg's Player cookie file under
the configured prefix's root/Users/<user>/Library/Cookies directory. It
resolves Nutmeg's configured prefix and home mode, including custom prefix
paths and ROSEMARY_PREFIX. The reader accepts only a non-expired secure
.ROBLOSECURITY cookie scoped to roblox.com and refuses symbolic links,
hard links, foreign-owned files, and oversized files. It never writes to
the macOS cookie store or opens Roblox Studio's separate cookie file.
It does not change the file permissions; owners should restrict any
world-readable cookie file.

## TLS splice startup check

The live TLS splice transport uses a CONNECT/upstream TCP startup check,
not a synthetic TLS 1.2 handshake. Real TLS 1.3 session interception
requires secrets from a running Roblox Player, so successful startup
does not prove key recovery or decryption. Validate both in a live
end-to-end test.


## Rosemary cache clearing and account/subplace launches

The Settings and Miscellaneous Clear Cache actions, along with the configured
automatic clear-on-launch/exit routines, use the same Linux delete-cache API.
For Rosemary, this resolves the Nutmeg guest home and deletes only known
Roblox storage entries: rbx-storage.db and its WAL/SHM/journal sidecars,
rbx-storage.id, rbx-storage, rbx-storage-sc, and Player-specific named
directories in Library/Caches. It preserves the cookie store, ClientSettings,
Studio preferences, installed plugins, and Studio autosaves. The Player is
closed first, but deletion aborts if Rosemary Studio is detected because both
may share the same storage database. Symlink targets are refused. No real
user cache is deleted by the regression tests.

In addition to the app bundle and Rosemary executable, Settings now accepts
an optional Rosemary prefix path. This overrides Nutmeg's saved prefix and
ROSEMARY_PREFIX for both cookie discovery and cache deletion; a missing or
unverifiable guest cache root causes a clear-cache abort, not a guessed
deletion. It is particularly important for standalone/non-Nutmeg installations.

Account Manager's "Use for Fleasion launches" selection on Rosemary does not
rewrite the guest Player's local cookie. Subsequent selected-account launches
use the saved account to obtain an authentication ticket. If a ticket cannot
be obtained, the launch fails rather than launching a different account.
The Subplace Joiner uses this selected account (when available), including
for the root-place pre-seed step. Joining a specific Job ID uses an
authentication-ticket URI that preserves the gameId; without a valid ticket,
it fails rather than silently joining a random server. Ordinary joins
without a selected account or Job ID continue using a normal Roblox deeplink.
These paths are covered by synthetic tests but still need live Player
validation. Rosemary multi-instance launching is not implemented.


## Live TLS default and interception routing

For the selected Rosemary client, the proxy now selects live TLS splice at
startup even before the Player process exists. This prevents an accidental
fallback to the older certificate-injection server when Fleasion starts first.
Windows also defaults to splice; FLEASION_SPLICE=0 explicitly enables the
legacy CA path for compatibility/testing. Live TLS still requires the native
session-secret extractor and a real client connection.

With splice active, startup bypasses Roblox cacert.pem modification and
CA trust-store installation. Launch-time certificate repair, monitoring,
and CA-driven restarts are also bypassed. Fleasion currently still prepares
legacy self-signed test certificates during shared proxy construction, but
those are not installed into the Roblox client or used for splice decryption.

Transport and routing are independent:

- Env Proxy routes new launches only when the Player's network libraries
  honor the inherited variables. Fleasion automatically replays new Nutmeg/
  Rosemary launches with those variables; already-running Players are
  unchanged. On current Rosemary, variable inheritance alone did not route
  Roblox asset traffic through Fleasion during the live test.
- Hosts-file routing uses managed DNS entries and a port-443 TCP relay,
  without relying on environment variables. This needs the Linux privileged
  helper. With splice enabled, relay checks verify TCP connectivity instead
  of synthetic TLS handshakes, and do not require CA trust.
- Live TLS splice reads actual client-generated TLS 1.3 secrets and decrypts
  the routed encrypted traffic. It does not redirect traffic itself. A
  log entry for live decrypted application traffic appears only after the
  first authenticated application record is decrypted for a host.

Sober and other Android backends retain their existing detection behavior.
Native macOS key recovery is not implemented by the current Linux/Windows
key provider; this Rosemary-specific default does not establish support for
native macOS.


## Env Proxy delivery and live-session diagnostics

For a Rosemary launch while Fleasion's Env Proxy override is armed,
Fleasion uses the detected native Rosemary executable and Roblox.app directly,
not gtk-launch. The desktop activation handoff may drop the caller's proxy
environment. The direct launch preserves both uppercase and lowercase proxy
variables and avoids inheriting a Nutmeg-specific NO_PROXY list that would
exclude clientsettings hosts. It does not execute Nutmeg's pre-launch update
stage: update the client separately through Nutmeg as needed.

A new Player launched from Nutmeg, a desktop shortcut, or another
application is intercepted and replayed to inherit Fleasion's per-launch
Env Proxy variables. The Linux adopted-Player lifecycle inspects the
selected Rosemary process environment without logging cookies or tokens.
If HTTPS_PROXY is absent or points to another endpoint, it reports an
unrouted Player instead of claiming successful interception. An
already-running process is not restarted automatically.

The explicit-proxy splice listener now waits for a first TLS record before
starting secret recovery. A CONNECT-only startup probe closes immediately
without producing a misleading 20-second TLS splice timeout. Live TLS
decryption is confirmed only after an authenticated application record is
processed. Reading process memory and validating stored TLS secrets alone
does not show that connections were routed through Fleasion.

For a Player whose network stack ignores Env Proxy, Hosts File mode
(plus the Linux privileged port-443 helper) is the available routing
alternative. A local CA is unnecessary when live TLS splice is active.
