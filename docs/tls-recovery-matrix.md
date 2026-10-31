# Optional TLS HTTP transport — connection recovery matrix

This matrix covers the **inactive, already-decrypted HTTP/2 bridge** using
synthetic localhost peers. It does not verify a platform extractor, TLS secret
acquisition, game client compatibility, or any production rollback path.

| Failure or shutdown condition | Expected behavior | Regression |
| --- | --- | --- |
| Upstream GOAWAY after stream 1 completed but stream 3 was truncated | Forward complete stream 1, reset stream 3, never send truncated body | `test_upstream_goaway_or_eof_never_completes_an_incomplete_response[goaway]` |
| Abrupt upstream EOF with one complete and one truncated response | Forward completed response, reset incomplete response | `test_upstream_goaway_or_eof_never_completes_an_incomplete_response[eof]` |
| Client GOAWAY while a request is stuck inside feature handling | Cancel inflight work, exit promptly, empty active request maps | `test_client_goaway_cancels_inflight_request_without_waiting_for_upstream` |
| Client EOF during a partial HTTP/2 upload | Release buffered body bytes and clear all request/stream accounting | `test_client_disconnect_releases_partially_uploaded_request_bytes` |
| Malformed initial HTTP/2 preface | Reject input and terminate both peers, even before pump startup | `test_invalid_initial_http2_preface_still_terminates_bridge` |
| Upstream disconnect with a feature task permanently waiting | Bound drain grace to one second, then cancel the task | `test_upstream_disconnect_cancels_a_stalled_request_within_deadline` |
| Upstream disconnect with a feature task completing promptly | Let task finish within grace period | `test_upstream_disconnect_allows_quickly_finishing_tasks_to_exit_normally` |
| More than 64 active HTTP/2 streams | Reset excess streams without ending the whole connection | `test_connection_rejects_excessive_http2_streams_without_terminating_peer` |
| Stream reset during blocked HTTP/2 DATA send | Wake sender and fail instead of waiting indefinitely | `test_reset_terminates_sender_waiting_on_http2_window` |
| Rejected DATA on an invalid request stream | Restore connection flow-control credit | `test_rejected_h2_request_restores_connection_flow_control` |
| Provider returns a session with invalid authentication metadata | Reject and close both acquired stream writers | `test_backend_cannot_return_unauthenticated_session` |
| Provider acquisition timeout | Report failure without silent fallback | `test_backend_open_times_out_without_fallback` |
| Provider session dispatch succeeds, fails, or is cancelled | Close both stream writers using bounded waits | `test_managed_dispatch_always_closes_both_stream_pairs`, `test_cancelled_managed_session_closes_both_writers` |
| Writer does not finish closing after rejection | Bound `wait_closed()` and return original validation error | `test_rejected_session_close_does_not_wait_forever` |

## Current behavior and limits

- The upstream EOF drain grace is one second, after which unfinished feature
  tasks are cancelled. Responses which never reached `END_STREAM` are not
  considered complete and are reset rather than delivered as successes.
- The bridge accepts up to 64 concurrent request streams and uses a 64 MiB
  aggregate request/response body budget. It buffers complete bodies and is
  not designed for streaming or server-sent events.
- No connection retry, certificate downgrade, fail-open behavior, or automatic
  switch between platform backends is permitted. The main certificate proxy
  remains unchanged and the optional provider registry remains inactive.
- Graceful GOAWAY handling with `last_stream_id` replay semantics, HTTP/2
  trailers, interim responses, server push, and extended CONNECT are **not**
  supported. These tests prove fail-closed behavior, not transparent replay.
- To assess actual rollback or restart behavior, a real, authenticated
  provider and platform-specific process lifecycle integration will be needed.

Run these focused tests with:

```sh
uv run --no-sync pytest -q tests/test_h2_connection_failures.py \
  tests/test_h2_bridge_recovery.py tests/test_h2_peer_shutdown.py \
  tests/test_decrypted_h2_transport.py tests/test_tls_backend_runtime.py
```
