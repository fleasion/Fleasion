# Synthetic TLS 1.3 record layer foundation

`src/fleasion/proxy/tls_records.py` contains **post-handshake** TLS 1.3
application-record primitives. It is not connected to a production listener,
secret extractor, certificate proxy, Roblox process, or any platform-specific
provider. It assumes a caller has already established and authenticated the
session and provided traffic secrets from a **controlled test session**.

## Implemented and tested

- TLS 1.3 AEAD suites: AES-128-GCM/SHA-256, AES-256-GCM/SHA-384, and
  ChaCha20-Poly1305/SHA-256
- RFC 8446 `HKDF-Expand-Label` for `key`, `iv`, and `traffic upd`, with key and
  IV derivation checked against the public RFC 8448 example values
- TLSCiphertext framing over arbitrarily fragmented TCP chunks, including
  coalesced records and strict length/version limits
- Per-direction sequence counters and nonces, additional authenticated data,
  record padding, message fragmentation into bounded TLS records, and AEAD
  authentication with fail-closed rejection of altered/replayed ciphertext
- Post-handshake KeyUpdate rotation and sequence reset per direction. A
  requested update is surfaced to the duplex session, which requires an
  outbound response before further application data
- Bounded record-length checks and explicit incomplete-record errors at EOF
- A controlled loopback integration test where generated TLS record traffic
  traverses the existing Fleasion HTTP/1 feature engine and is encrypted again
  on the way back. All three cipher suites pass the same replacement flow

## Current API

- `CipherSuite`: supported TLS 1.3 ciphers and hash functions
- `EncryptedRecordFramer.feed(bytes)`: decode complete TLSCiphertext frames
- `TLS13RecordCipher(secret, suite)`: one **unidirectional** application epoch;
  `encrypt_record`, `decrypt_record`, `encrypt_application`, `send_key_update`
- `TLS13RecordSession(inbound_secret=..., outbound_secret=..., suite=...)`:
  pair of separate direction-specific cipher states, with `receive`,
  `send_application`, and `send_key_update`

**Never reuse the same cipher instance for both traffic directions.** A
KeyUpdate request received using the inbound secret requires a response
encrypted using the independent outbound secret. TLS 1.3 secrets must not be
logged, cached in application settings, or exposed in GUI request logs.

## Not yet supported

- TLS handshake negotiation, certificate verification, ALPN selection, or
  generation of application traffic secrets
- Initial handshake/early-data records, 0-RTT, post-handshake NewSessionTicket,
  or fragmented/coalesced KeyUpdate handshake messages: only complete
  post-handshake KeyUpdate records are currently parsed, and other encrypted
  handshake messages fail closed
- Automatic handshake-to-application key epoch switching or traffic-secret
  provenance checks; callers must not use this codec before the application
  epoch is authenticated and active
- A production stream shim that maps live encrypted records into the existing
  plaintext HTTP transport adapters, session/lifecycle routing, or recovery
  from protocol changes in a real client
- Live platform compatibility, anti-cheat behavior, or real-client end-to-end
  tests. All five provider placeholders remain explicitly unavailable

The synthetic record tests are **not** a claim that a live interception
transport is operational. The current production proxy is unchanged. This
module is a separately testable building block for protocol correctness.

Run tests with:

```sh
uv run --no-sync pytest -q tests/test_tls_records.py tests/test_tls_record_loopback.py
```

References: RFC 8446 sections 4.6.3, 5.2–5.3 and 7.1–7.3; RFC 8448 section 3.
