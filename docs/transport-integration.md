# Decrypted-stream transport integration

The existing Fleasion feature engine can be driven by streams for which another
component has **already established authenticated TLS**. The adapters do not
acquire TLS traffic secrets, access client process memory, change a CA bundle,
or intercept the Roblox client by themselves.

The two transport-independent entry points are:

- `FleasionProxy.serve_decrypted_http_session()` for HTTP/1.x
- `FleasionProxy.serve_decrypted_h2_session()` for HTTP/2

A five-slot optional provider registry and synthetic readiness checks are now
available under `tls_backends/`. All real providers remain inactive and
unimplemented. See `docs/tls-backend-handoff.md` for the platform matrix,
interface contract, and contributor requirements.

Both use the existing HTTP feature engine for request/response replacements,
asset scraping, gamejoin and profile modifications, custom FastFlags, and
Proxy-tab request logging. Neither entry point is called by the existing
production listeners; the certificate-proxy transport remains unchanged.

## Caller responsibilities

- Verify both TLS peers, server hostname and ALPN before delivering decrypted
  streams to an adapter
- Ensure the upstream stream pair belongs to the actual authenticated session;
  never silently redial a session after it is lost
- Pass only approved hosts and maintain the same host identity for each request
- Handle terminal exceptions and close both sides without downgrading TLS
- Never log TLS keys, credentials, or sensitive headers

## HTTP/1.x adapter

- Reuses the complete feature engine with an already established upstream
  connection, which the engine closes when it finishes
- Validates HTTP Host against the session host, including keep-alive requests
- Handles `Content-Length` and close-delimited upstream response bodies
- Rejects HTTP/2 input; no speculative retry on a new upstream TLS session

## HTTP/2 adapter

`h2_transport.py` uses `hyper-h2` to adapt each complete HTTP/2 request to an
isolated HTTP/1 feature-engine session and translate the output back to HTTP/2.
It preserves multiplexing and HTTP/2 stream IDs, applies flow-control to
outbound bodies, and handles stream resets and connection termination.

Important limitations of this experimental adapter:

- It buffers full request/response bodies with a 64 MiB aggregate
  connection-level budget and caps active request streams at 64 per connection;
  excess streams are reset rather than dispatched to additional feature tasks.
  Large streaming and server-sent events are not supported
- Discarded request DATA explicitly restores HTTP/2 connection flow-control
  credit; local and remote stream resets wake blocked outbound body senders
- HTTP/2 request/response trailers, unsupported CONNECT and interim
  informational responses cause stream resets instead of being silently
  discarded; these features are not translated by the HTTP/1 engine
- HTTP/2 server push and extended CONNECT are not supported
- No TLS 0-RTT integration or transport negotiation is implemented here
- When the upstream connection ends, active request tasks have a one-second
  drain grace period, after which the bridge cancels tasks still blocked on
  flow-control or incomplete responses instead of hanging indefinitely
- Resets and GOAWAY are handled on a best-effort, fail-closed basis; detailed
  behavior for every cancellation race is not yet proven. Malformed initial
  HTTP/2 bytes also enter cleanup, and partial uploads are discarded on EOF
- The tested GOAWAY/EOF shutdown cases and remaining limits are recorded in
  `docs/tls-recovery-matrix.md`
- It has not been exercised against actual Roblox live traffic, protected
  process memory, or anti-cheat checks

## Required verification before a production transport

1. Authenticate both peers, enforce negotiated ALPN, and establish session
   provenance before handing streams to either adapter
2. Verify HTTP/1 and HTTP/2 feature parity, including large bodies, parallel
   requests, cancellations, trailers, streaming, and existing request modifiers
3. Confirm asset cache, all replacement types, gamejoin/subplace handling,
   custom FastFlags, and interactive Proxy-tab behavior
4. Verify that startup probes validate a real end-to-end request rather than
   just opening a local listener socket
5. Enforce reasonable memory bounds and guarantee cleanup on upstream failure
6. Test current client versions separately for each supported platform

`tests/test_decrypted_transport_adapter.py` and
`tests/test_decrypted_h2_transport.py` use controlled localhost peers,
including locally authenticated TLS/ALPN in the HTTP/2 tests. They do not
establish real-client compatibility or anti-cheat detection risk.
