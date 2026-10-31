# Optional TLS transport providers — contributor handoff

The HTTP feature engine already accepts authenticated plaintext HTTP/1.1 and
HTTP/2 streams through `FleasionProxy.serve_decrypted_http_session()` and
`FleasionProxy.serve_decrypted_h2_session()`. No production listener currently
calls the optional provider registry, and **no platform extractor is present**.
The existing certificate-proxy remains the only production transport.

## The five provider slots

| Backend ID | Source file | Platform target | Current status |
| --- | --- | --- | --- |
| `windows` | `tls_backends/windows.py` | Native Windows Roblox | `not_implemented` |
| `android_community` | `tls_backends/android_community.py` | Cordial, Mocktail, future Nightcap | `not_implemented` |
| `sober` | `tls_backends/sober.py` | Sober Android runtime on Linux | `not_implemented` |
| `macos` | `tls_backends/macos.py` | Native macOS Roblox | `research_pending` |
| `rosemary` | `tls_backends/rosemary.py` | macOS Roblox through Rosemary on Linux | `not_implemented` |

Nightcap is a Mocktail fork but is **not yet supported by Fleasion**. Its name
is a future integration placeholder, not an assertion of operational support.
Sharing the Android-community slot does not imply identical runtime layouts.

## Integration contract

1. Each provider implements `SessionBackend` in `tls_backends/contracts.py`:
   a synchronous `probe()` returning `BackendProbe`, and an asynchronous
   `open_session(hostname)` returning `AuthenticatedSession`
2. The provider must authenticate the upstream peer and maintain the original
   plaintext stream's provenance. It supplies the negotiated ALPN and exactly
   one authenticated, already-established upstream connection
3. `acquire_verified_session()` in `tls_backends/runtime.py` enforces an
   explicit provider readiness check, bounded acquisition time, and session
   metadata validation. Missing, timed-out, or invalid sessions raise
   `BackendUnavailableError`; there is no automatic fallback to a different
   provider or less strict TLS trust source
4. `dispatch_authenticated_session()` routes `http/1.1` and `h2` to the
   existing feature engine, preserving replacements, cache, and request logs
5. The calling transport owns connection shutdown and cleanup. Stream
   cancellation must not leave the game with a half-open tunnel
6. The provider must remain unavailable until actual client compatibility is
   established. A stub or a port-listening test is never a readiness success

This interface passes **streams**, not raw TLS traffic secrets. Individual
provider implementations should not expose secrets to the GUI, request log, or
HTTP feature engine. Code that circumvents client protections, reads live game
process memory, or performs live-service impersonation is not included here.

## Health status

`tls_backends/health.py` distinguishes:

- `provider_unavailable`: the platform implementation is missing
- `provider_only`: the provider claims readiness, but no session is verified
- `session_invalid`: the supplied session failed authentication/metadata checks
- `session_validated`: the session metadata passed, but no HTTP request was tested
- `request_failed`: incomplete, unsuccessful, or mismatched HTTP response
- `end_to_end_verified`: a completed localhost-controlled HTTP exchange matches
  the expected challenge and the session was marked authenticated

An actual end-to-end check must run an HTTP request through the feature engine,
validate completion and its response challenge, and fail closed otherwise. A
synthetic `AuthenticatedSession` only supports tests, not real-client claims.
The HTTP/1 and HTTP/2 localhost integration tests each demonstrate a positive
health check, while negative tests reject fake success conditions.

## Validation before enabling any platform

- Tests: `tests/test_tls_backend_registry.py`, `test_tls_backend_runtime.py`,
  `test_tls_backend_health.py`, `test_decrypted_transport_adapter.py`, and
  `test_decrypted_h2_transport.py`
- Check the target's true Roblox version, architecture, TLS trust source and
  negotiated ALPN independently per platform
- Cover connection loss, TLS renegotiation/update or resumption where relevant,
  stream cancellations, HTTP/2 flow control, and host mismatch rejection
- Confirm actual feature parity: request/response replacement, asset and mesh
  replacement, cache scraping, gamejoin/subplace, flags, and Proxy tab
- Enable a production provider only after real end-to-end verification in that
  environment; the synthetic tests alone are not evidence of compatibility

For protocol limitations and the current 64 MiB HTTP/2 buffer budget, see
`docs/transport-integration.md`.

For cross-protocol feature coverage and the remaining real-client validation
gaps, see `docs/tls-feature-parity.md` and `tests/test_tls_feature_parity.py`.
