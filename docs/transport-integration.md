# Transport integration boundary

`FleasionProxy.serve_decrypted_http_session()` is an HTTP/1.x adapter for
**already authenticated and decrypted** byte streams. It lets a transport
implementation reuse Fleasion's existing asset, gamejoin, profile, request
replacement, response replacement, scraper, and Proxy-tab processing without
creating a second TLS connection for the same session.

It is not an active TLS-splicing backend. In particular, this adapter does not
perform TLS handshakes, obtain traffic secrets, handle TLS records, read process
memory, change a trust store, or implement HTTP/2.

## Ownership and constraints

- The caller must authenticate both peers and verify that the upstream session
  corresponds to the requested host before supplying streams to the adapter
- Only hosts configured for interception are accepted, and the HTTP Host header
  must match the authenticated host
- The upstream streams must be the original already-established connection;
  the adapter will never silently redial it after it has been consumed
- The existing HTTP/1 feature engine owns the accepted upstream connection and
  closes it when the session ends; callers own rejected or unconsumed streams
- HTTP/2 input is rejected rather than misparsed as HTTP/1
- Existing production certificate-proxy behavior remains unchanged

## Requirements before enabling another production transport

1. Verify the server certificate and hostname, negotiated protocol and
   connection provenance before passing streams into the feature engine
2. Independently test HTTP/1 and HTTP/2, multiplexed requests, flow-control,
   early EOF, cancellation, and response framing
3. Confirm feature parity for asset cache/scraper, all replacement types,
   gamejoin/subplace handling, custom FastFlags, and the interactive Proxy tab
4. Make startup checks validate an actual end-to-end request, not merely that
   the local port is listening
5. Preserve the original connection or fail closed when either side is lost;
   never silently downgrade authentication or switch trust sources
6. Do not log session secrets or sensitive headers in diagnostics

`tests/test_decrypted_transport_adapter.py` covers the transport-independent
HTTP/1 behavior with controlled localhost peers. These tests provide no
assurance about third-party client memory access or anti-cheat compatibility.
