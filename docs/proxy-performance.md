# Proxy performance checks

Run the local microbenchmarks with the project's Python environment:

```sh
uv run python scripts/benchmark_proxy.py --iterations 10000 --include-file --include-batch --include-scraper --include-json
uv run python scripts/benchmark_h2_loopback.py --requests 500
uv run python scripts/benchmark_h2_loopback.py --requests 1000 --parallel 32
uv run python scripts/benchmark_h2_loopback.py --buffered --requests 10
```

The script reports median wall-clock and process CPU time over five runs. It
uses synthetic inputs and a temporary local file; it does not contact Roblox or
read credentials. File results depend on the operating system's cache and the
filesystem. These measurements isolate individual paths, not actual end-to-end
proxy latency, throughput, UI responsiveness, or idle CPU usage. Compare the
same machine, environment, workload, and build, and repeat measurements with a
quiet system. Run the script on Windows and macOS as well as Linux when those
platforms are available.

For behavior and protocol coverage, run the focused tests alongside the
benchmarks:

```sh
uv run pytest -q tests/test_http_framing_fail_closed.py tests/test_proxy_server.py tests/test_proxy_local_range.py tests/test_proxy_request_preview.py tests/test_h2_message_writer_copying.py tests/test_h2_buffer_isolation.py tests/test_decrypted_h2_transport.py tests/test_tls_records.py tests/test_tls_splice.py tests/test_splice_disconnect_cleanup.py tests/test_live_key_nearby_scan.py tests/test_windows_scan_locality.py tests/test_sober_scan_coverage.py tests/test_linux_clients.py
```

For Qt traffic-table coverage on a headless Linux machine, set
`QT_QPA_PLATFORM=offscreen` and run `tests/test_proxy_traffic_refresh.py` and
`tests/test_proxy_tab_layout.py`. Native desktop testing is still needed for
platform-specific rendering and window behavior.

In the broad Linux run, `tests/test_certificate_flags.py` and
`tests/test_tab_reveal_regression.py` were excluded: together they have the
same 11 failures against both the untouched baseline and this working tree.
The timing-sensitive `test_tab_transition_survives_rapid_switch_and_theme_change`
was run separately after intermittently failing in the full offscreen suite.

To compare incremental and full traffic-table refresh work, run
`uv run python scripts/benchmark_proxy_ui.py --rows 1000 --iterations 20`
with `QT_QPA_PLATFORM=offscreen`. The timed section updates table items without
processing paint events, so it measures model/update work rather than frame
rate or rendering cost.

The loopback benchmark drives 1 KiB CDN responses or simulated 1 MiB
feature-engine responses through the actual HTTP/2 bridge and validates each
response body. Its upstream and proxy feature configuration are synthetic; it
does not include TLS, a real CDN, or replacement processing. `--parallel` opens
up to 32 concurrent streams per batch.
Use it as a regression signal alongside the HTTP/2 integration tests and
representative real-world measurements.

## Example comparison

On Linux with Python 3.14.10 and a Ryzen 7 9700X, alternating runs against
commit `fa65852` and the modified working tree produced these approximate
median process CPU times:

| Workload | Baseline | Modified |
| --- | ---: | ---: |
| TLS 1.3 encryption, 16 KiB | 2.2 µs | 1.7–1.8 µs |
| HTTP/2 target validation | 1.1 µs | 0.6 µs |
| HTTP/1 headers and 64 KiB body | 17.7–18.1 µs | 14.1–15.0 µs |
| 128-byte range from cached 16 MiB file | 179–189 µs | about 7.9 µs |
| Direct HTTP/2 loopback response | 341–343 µs | 336–337 µs |
| Buffered 1 MiB HTTP/2 loopback response | 3.09–3.15 ms | 3.04–3.07 ms |
| Unmodified 1,000-item asset batch | 6.2–6.3 ms | about 1.4 ms |
| Track 3,000 asset IDs sharing one CDN URL | 139–152 ms | 2.9–3.2 ms |
| Track 3,000 asset IDs with distinct CDN URLs | 5.69 ms | 2.95 ms |
| Decode 2,000-item proxy JSON | 6.28 ms | 0.58–0.69 ms |

The loopback differences are small relative to system noise. The file benchmark
uses a sparse temporary file with a warm cache. The microbenchmarks are not
representative of every request and do not establish native Windows or macOS
performance. Local range and ordinary HEAD requests below 256 KiB use the
original full-read path; this also handles common virtual
files whose reported size does not match their content.
In a longer run of 100,000 verified direct responses per version, CPU time was
approximately 337 µs per response at the baseline and 333 µs after the changes.
For 1,000 buffered 1 MiB responses per version, the corresponding values were
approximately 3.15 ms and 3.08 ms.
A further modified-build run completed 500,000 verified responses in batches
of up to 32 concurrent streams.

When the Proxy tab is hidden and traffic preservation is disabled, its timer
does not copy the request log. Showing the tab takes a fresh snapshot; with
preservation enabled, hidden polling continues so scheduled saves stay current.
Log notifications also avoid starting a timer thread when there are no
subscribers; subscribers joining during an active batch still receive it. The
log viewer copies only entries added since its previous refresh, rather than
copying its entire history on every poll. Log rotation checks file metadata
once per append, and a removed log directory is recreated on the next write.
JSON-based proxy operations, cache previews, and the FastFlag cache reader
reuse the decoder's values without recursively validating the same data again.

## Windows runtime under Wine

A separate Windows Python 3.14.9 environment under Wine 11.20 on the same Linux
host passed 601 tests covering proxy, HTTP/2, TLS, asset replacement, logging,
and offscreen Qt behavior; 40 tests were skipped. Additional focused tests
covered the Windows scanner. Another 34 tests passed with the minimum
supported HTTP/2 version, 4.2.0.
In baseline and modified runs, median wall times were:

| Workload | Baseline | Modified |
| --- | ---: | ---: |
| TLS 1.3 encryption, 16 KiB | 2.90 µs | 2.35 µs |
| HTTP/2 target validation | 1.60 µs | 0.80 µs |
| HTTP/1 headers and 64 KiB body | 22.79 µs | 19.17 µs |
| Unmodified 1,000-item asset batch | 7.07 ms | 1.64 ms |
| Track 3,000 asset IDs sharing one CDN URL | 162–167 ms | 3.5–5.4 ms |
| Track 3,000 asset IDs with distinct CDN URLs | 6.91 ms | 3.44 ms |
| Decode 2,000-item proxy JSON | 7.36 ms | 0.64 ms |
| 128-byte range from cached 16 MiB file | 2.76 ms | 0.20 ms |
| Direct HTTP/2 loopback response | 41.88 ms | 41.85 ms |

Wine's process CPU clock was too coarse for the smallest operations. These
results exercise Windows Python and Windows-compatible libraries but do not
establish performance or GUI behavior on a native Windows installation. The
loopback latency in particular includes Wine's socket behavior and should not be
interpreted as native Windows network latency.

In an offscreen Qt run under Wine, updating a 1,000-row traffic table while one
row was added and another evicted took approximately 2.9 ms per incremental
refresh versus 18.0 ms with full rebuilding. This does not measure visible frame
rates or native desktop rendering.

## Studio reference

The user-provided macOS x86-64 Studio 0.735.19.7351131 executable has SHA-256
`6b01d19b7fdfab0528621680e0b8760aa10c54fc917c79bf1f1f6eb92c373b6d`.
Its symbol index identifies `AssetProvider::createBatchAssetRequestEntry` at
`0x1034a95c8` and `BatchAssetDeliveryClientV2::getResponse` at `0x1034ac5a4`.
Static inspection of the entry writer shows it emits string `assetType` and
`requestId` fields, with `accept` and `usageContext` under conditions. In
`BatchAssetApiV2ResponseState::next`, the code at `0x1034c2e84` reads the
response's `requestId`, parses a base-10 integer, checks it against a vector's
bounds, and uses it to access a vector entry. The same build's asset-delivery
parameter table includes mappings such as `id` to `assetId` and `placeid` to
`placeId`, as well as representation-related fields. These observations are
specific to this Studio build; they do not establish current client behavior or
every response path. The proxy continues preserving request fields and uses the
protocol tests and captured fixtures for behavior checks.
