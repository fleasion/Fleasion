# Linux clients: Mocktail and Sober

Fleasion recognizes the system Sober Flatpak (org.vinegarhq.Sober) and native
Linux Mocktail (space.bigrat.mocktail, executable mocktail). Both appear in
Settings with Rosemary and Auto. Auto prefers a running Rosemary or Mocktail,
then the registered desktop URI handler. Explicit selection pins the chosen
client, proxy configuration, and TLS key source.

## Sober

Sober keeps its existing Flatpak-isolated paths and per-app HTTP/HTTPS/ALL
proxy environment override. Fleasion removes this user-scoped override on
shutdown. The live TLS splice transport is selected before Player starts,
and the scanner targets only the Sober engine. The engine's library can be
mapped through anonymous memfd rather than as a named libroblox.so.

Live test on Sober 1.8.0: 40 validated TLS sessions with the required
application-traffic secrets, followed by decrypted application requests to
apis.roblox.com and assetdelivery.roblox.com. The asset remover logged a
successful removal.

## Mocktail

Mocktail uses its native RPM executable and desktop entry
space.bigrat.mocktail.desktop; joins use mocktail --launch-uri URI.
Its persistent paths are different from Sober:
- ~/.config/mocktail/config.yaml — settings
- ~/.local/share/mocktail/ — managed versioned payloads and Android data
- ~/.local/share/mocktail/android/data/ — Android storage
- ~/.cache/mocktail/ — cache and ClientSettings
- ~/.local/share/mocktail/auth/roblox.cookie — session cookie

Clear Cache targets Roblox storage and cache while preserving managed
payloads, auth cookies and ClientSettings. Symlinked cache directories
or parent components are refused rather than followed outside the
selected client's storage roots.

Account Manager can now switch Mocktail's saved account while Mocktail
is closed. It reads only the expected private plaintext cookie file and
preserves other cookies during the update. The writer uses Mocktail's
own `.roblox.cookie.mocktail-writer.lock` advisory lock, validates the
source and its permissions, writes a temporary 0600 file, and renames
it atomically after checking for concurrent changes. It does not
update an active in-memory Player session; close Mocktail first.

If Mocktail's `auth.use_libsecret` configuration, an overriding cookie
environment variable, or an unsupported cookie format makes the
plaintext file non-authoritative, Fleasion refuses the switch.
Account Manager reports that limitation without selecting a different
account. Browser-based account import continues to work as before.

With Env Proxy enabled, Fleasion automatically intercepts new Mocktail
Players started through the normal desktop launcher, captures the original
command line, working directory and environment, and replays with proxy
variables. To avoid confusing its helper for the engine, identification
requires the exact executable, process name Main, and mapped libroblox.so.
The original same-executable helper holds Mocktail's single-instance lock,
so it must exit before the original engine is replaced. PID identity,
parentage and ownership are checked before signaling; unrelated programs
and already-running sessions are excluded.

Live test on Mocktail 1.0.4: 44 validated TLS sessions with the application
secrets. After fixing the single-instance lifecycle, a normal external
launch was replayed, connected to Fleasion, and decrypted assetdelivery,
apis, clientsettings, contentdelivery and other configured hosts. Initial
sessions timed out during bootstrap, then later sessions decrypted.

## Scope

The Linux key provider is pinned to the selected client, so a running
Rosemary Player cannot divert Sober or Mocktail key recovery. The explicit
FLEASION_SPLICE=0 environment setting retains legacy TLS transport behavior.

The first Mocktail startup probe inadvertently cleared Android rbx-storage
because the user's separate Clear Cache on Launch toggle was enabled.
Subsequent probes disabled both automatic cache clearing options.
At completion, the original Fleasion settings were restored byte-for-byte,
Sober's Flatpak proxy override was disarmed, and test clients were closed.
Synthetic unit tests do not delete actual user caches.
