# TLS transport feature-parity test matrix

`tests/test_tls_feature_parity.py` exercises Fleasion's **existing feature engine**
through both `http/1.1` and `h2` authenticated-stream dispatch paths. These
are localhost-only synthetic transactions; they do not connect to Roblox,
read client process memory, or simulate a live session-secret extractor.

| Feature engine path | HTTP/1.1 | HTTP/2 | What the tests prove |
| --- | --- | --- | --- |
| Request and response Auto Replace | Pass | Pass | Modified bytes reach the synthetic upstream/client |
| Query parameter and request header rules | Pass | Pass | The forwarded request contains the rewritten path/header |
| Proxy-tab request log | Pass | Pass | Host, path, status and intercepted flag are recorded |
| Replacement with Proxy-tab logging off | Pass | Pass | A disabled log does not disable always-on replacement rules |
| Local CDN mesh-file response | Pass | Pass | The supplied local file is returned without an upstream request |
| CDN cache-scraper callback | Pass | Pass | Scraper sees the delivered synthetic bytes |
| SolidModel response transformation dispatch | Pass | Pass | A stand-in transform callback receives the upstream model and replacement path |
| Batch asset request/response hooks | Pass | Pass | The modified batch request and matching response reach their callbacks |
| Batch cache-scraper callback | Pass | Pass | The scraper receives the modified request used upstream |
| Direct asset scrape | Pass | Pass | The direct response body, status, and asset path reach the scraper |
| Gamejoin request/response module interceptor | Pass | Pass | The module can modify both request and response payloads |
| Gamejoin locally dropped request | Pass | Pass | The local response is returned without contacting upstream |
| Proxy-tab manual request/response editing | Pass | Pass | An intercepted request pauses for editing; the edited request and response are forwarded |
| Proxy-tab dropped request/response | Pass | Pass | Dropped requests never reach upstream; dropped responses do not reach the client |
| ClientSettings modification and acknowledgment | Pass | Pass | The modified response and delivery acknowledgment reach the client side |

These cases deliberately use controlled test addons or module callbacks.
**A passing callback test does not verify that Fleasion's actual Mesh/SolidModel
conversion, archive parsing, binary decoding, or module-specific content rules
produce correct real Roblox content.** Those implementations have separate
unit tests and need real-client integration verification for a release.

## Still required for full real-client parity

- Real Roblox certificate/transport compatibility by platform and build
- A genuine authenticated Roblox request traversing the new provider backend
- Complete asset replacement and mesh decode/encode with real asset fixtures
- Real subplace-join behavior and service-specific module transformations
- HTTP/2 trailers, informational responses, streaming and CONNECT support
- TLS resumption, key updates, upstream connection failure and restart recovery
- Production Qt Proxy-tab pause/edit/drop UI wiring and actual user interactions;
  the synthetic test submits decisions through the same Python methods
- Server-side push and any protocol extensions the live Roblox client requires
- User-facing recovery on unsupported or unverified backends without downgrading trust

The five unfinished provider placeholders live in `src/fleasion/proxy/tls_backends/`.
They are **not** enabled by these tests. See `docs/tls-backend-handoff.md` for
provider implementation contracts and `docs/transport-integration.md` for
protocol limitations.
