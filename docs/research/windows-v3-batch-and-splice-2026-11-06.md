# Roblox V3 batch and Windows TLS-splice compatibility — 2026-11-06

## Incident and evidence

`~/Downloads/broken.log` and `~/Downloads/vm_fleasion.log` were compared for Roblox `version-f0ea5da9412f4e35`. Both the affected Windows client and the working VM recovered TLS traffic secrets and decrypted the six main Roblox asset/config endpoints. The update therefore did **not** globally invalidate the OpenSSL 3.5.0 memory-layout scanner.

The affected log shows wrapped V3 asset delivery (`POST /v3/assets/batch`) and `DEBUG_DIRECT` responses beginning with `{"assets"` rather than individual CDN redirects. The existing V1/V2-only batch path did not recognize V3, so asset replacement and scraper association were skipped. A previously closed Fleasion V3 contribution (PR #121, commit `81f1c01`) contained the basic envelope conversion; the current fix preserves nested metadata, original payloads when unmodified, and compressed-response handling as well.

The affected log also has long Windows key-memory scans (several seconds), waiting queues above 20 seconds, unsupported TLS1.2 negotiation on a Fastly-related host, and many non-feature-host splice attempts. These observations support *traffic overload*, but **do not prove that the user checked Intercept All** or that Froststrap independently terminated Roblox. Both hypotheses require more instrumentation or an affected Windows reproduction.

## Fixes and tests

1. V3 batch request/response envelopes: flatten to the existing V1/V2 add-on hooks, retain unknown outer attributes and per-asset `asset` metadata (including non-ID hash requests), and restore V3 envelopes after ID/URL rewriting. Refuse ambiguous/malformed payloads instead of associating the wrong asset. Unmodified compressed requests/responses remain wire-identical. HTTPS integration tests include ID replacement, gzip/zstd, local replacements, and V3 gzip response rewriting to a synthetic CDN URL. V3 scraper tests associate original IDs with CDN paths.
2. Initially unsupported TLS1.2: preserve the existing genuine handshake and relay it transparently instead of throwing and disconnecting. **Follow-up:** a separate TLS1.2 AES-GCM record path, NSS `CLIENT_RANDOM` 48-byte master-secret support, and a candidate OpenSSL 3.5.0 Windows `SSL_SESSION` memory reader were added. Real TLS1.2 HTTP/1.1 and HTTP/2 tests pass using an NSS key log; Windows master-secret extraction still requires live validation. Unsupported suites and missing/invalid keys retain logged passthrough.
3. Native key scans: cap pending scan tasks at 16, cancel queued scans whose last caller times out, allow already-running threads to complete safely, and avoid the race where a new waiter inherits an earlier canceled queued task. Regression tests exercise 63 simultaneous callers, shared waiters, and replacement after expiration. No TLS timeout increase or OpenSSL struct-layout change was made.
4. Intercept All semantics: all requested HTTPS hosts still enter the interception path, **even during scan overload**. An earlier experimental silent fallback that tunneled diagnostic hosts when the scanner was busy was explicitly removed after user feedback. Failures appear in diagnostics rather than being presented as successful interception.
5. Dashboard state: closing the dashboard with close-to-tray deletes the window but retains the ProxyMaster. The old Proxy tab always initialized its checkbox as off and its auto-intercept text empty, even if both controls were active in the backend. Both the checkbox and auto-intercept filter now initialize from the running session state, and on/off transitions are logged without exposing filter values. The Proxy tab audit found no analogous backend mismatch for Filter/Highlight (view-only) or Preserve/Auto Replace (persisted). English checkbox text now says `Intercept all hosts`. A GUI regression test covers enable, dashboard recreation, and disable against a simulated active backend.

## Scope and remaining validation

These changes were developed locally on `feat/rewrite-ui-v2`. The affected Windows VM was not running during this investigation; no claim of a fresh live Windows validation is made. The prior incident logs cannot establish that Froststrap was responsible for the relaunches, so no speculative Froststrap-specific changes were made. Heavy-game scans can still exceed the existing per-connection deadline if process memory scanning itself takes too long; the fixes bound queue waste, not the cost of a single `NtReadVirtualMemory` scan. Future Windows profiling could justify batching or more precise cached region search.

The final focused proxy/TLS/GUI-state regression run passed **426 tests, 2 skipped, 3 subtests**; changed production and new test files passed Ruff and Pyright with zero errors. A full repo run stopped on a pre-existing GUI collapse-layout failure after 1,138 passes; a subsequent run passed 1,158 tests before an independently reproducible-in-isolation Linux Flatpak override expectation failed. An exclusion run stalled near 65% and timed out. These do not establish full-suite green; the focused proxy/TLS suite and new GUI regression tests are the validation gates for this patch.

## Live OpenSSL session-layout verification — 2026-11-06

A subsequent read-only live probe tested the TLS 1.2 session extraction assumptions against **four actual Roblox processes**. All temporary test clients were closed afterward. The probe never printed or wrote TLS secrets or client-random values; it retained only validation counts and harmless session layout metadata.

| Build / launcher | Validated TLS 1.3 connections during session-pointer probe | Verified `SSL_CONNECTION.session` offset | Session validation |
|---|---:|---:|---|
| Windows Roblox Studio 0.746 through Vinegar/Wine | Multiple (real TLS 1.3 validated with Fleasion's Windows validator) | `0x8F8` | `SSL_SESSION.ssl_version=0x304`, `master_key_length=48` |
| Android Roblox through Mocktail | 37 | `0x900` | `0x304`, 48 bytes |
| Android Roblox through Sober | 42 | `0x900` | `0x304`, 48 bytes |
| macOS Roblox Player through Rosemary/Nutmeg | 59 | `0x900` | `0x304`, 48 bytes |

The Windows session pointer is **eight bytes earlier** than the x86_64 Android/macOS layout. Treating them as interchangeable would be incorrect. To prevent a cross-client regression, `tls12_session.py` now has separate Windows and LP64 layout descriptors; the existing TLS 1.3 decoder and fast scanner path remain intact. The Windows scanner also avoids reading larger structs for ordinary TLS 1.3 candidates. The LP64 TLS 1.2 scanner integrates targeted `CLIENT_RANDOM` lookups through Mocktail/Nightcap/Cordial, Sober and Rosemary without changing process-selection logic or its memory-access methods.

**Important limit:** these were TLS **1.3** connections used to validate the *common OpenSSL session structure*, not observed TLS 1.2 sessions. Session-pointer and length-field evidence is strong, but **native client TLS 1.2 master-secret extraction has not been observed live**. The separate TLS 1.2 authenticated record and HTTP/1.1/2 interception tests use genuine TLS 1.2 traffic and test key logs. The new Linux reader tests use synthetic TLS 1.2 memory. A live TLS 1.2 connection on each client remains the final release verification, including the risk of platform-specific `SSL_SESSION` details.

Vinegar installed the current Studio 0.746 build during launch; no Fleasion profile or Roblox account state was edited. Rosemary displayed an optional MiniDarwin upgrade prompt; **Not Now** was selected, leaving the system prefix unchanged. After the test, the Vinegar Studio, Mocktail, Sober, and Rosemary test sessions were closed. The final broad proxy/TLS and key-scanner regression run passed **530 tests, 5 skipped, 3 subtests**; changed source and tests passed Ruff and strict Pyright checks.
