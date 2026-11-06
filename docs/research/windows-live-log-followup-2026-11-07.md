# Windows live log follow-up — November 7, 2026

## Observations in the supplied `fleasion.log`

The Windows 11 client successfully decrypted all six selected Roblox feature hosts, fetched V3-era asset data, populated the cache, and served a local CDN replacement with HTTP 200. No key-recovery timeout or failed TLS record authentication appears in the captured session. The client reported that the previous compatibility problem was fixed.

Remaining issues were distinct:

- **Windows memory scanning:** 11 slow key lookups succeeded. The worst reached 7.907 seconds overall (3.419 seconds queued and 4.487 seconds scanning). Several other scans took 3.9–4.2 seconds. There are already two scan worker slots and a bounded pending queue, but individual full-memory scans can still be expensive.
- **Unrelated CONNECT hosts:** `silver.roblox.com` and `pulsar.roblox.com` each failed twice, spending ~10 seconds on one IPv4 address. The log does not establish whether those remote services were reachable from that Windows system without Fleasion. Since both hosts had one reported candidate, changing the fallback order or shortening the existing 10-second budget would not create an alternative route, and could regress genuine slow networks. No tunnel behavior was changed here.
- **Diagnostic OpenGL readback:** OBJ and animation viewers both successfully initialized their OpenGL 4.6 AMD contexts and presented raster frames, but their separate diagnostic `glReadPixels` calls raised `GL_INVALID_OPERATION`. Both were drawing into a multisampled Qt offscreen framebuffer, which cannot be directly pixel-read without resolving first. The real Qt `toImage()` path already resolves it.
- **Cache I/O and metadata:** Canonical TexturePack slots were repeatedly promoted to higher resolution. The old code read the entire existing KTX2 file even when 28 bytes of header were sufficient for resolution comparison. Roblox asset type 60 was labeled `Unknown(60)` in two maps, although community-maintained asset type listings identify it as `LocalizationTableTranslation`. The official public AssetType enum omits several internal asset types, so this name is a compatibility mapping, not an exposed enum guarantee.
- **Expected launch/shutdown traffic:** `WinError 64` splice resets immediately followed intentional Roblox termination. TLS keys and decrypted application data were already present on those connections. The log alone does not justify changing error handling to suppress all resets.
- **Unmatched CDN requests:** Four `[CDN UNKNOWN]` canary messages remain. Direct/previously cached CDN URLs may legitimately occur without a freshly observed batch; suppressing these diagnostics would hide potential mapping regressions. No scraper association inference was added.

## Local changes

1. Add a bounded **256 KiB nearby-connection scan** for targeted Windows TLS client randoms, based on validated OpenSSL structure addresses. If there is no matching key, the original prioritized full-region scan always runs. Cache only addresses and region metadata, not TLS secrets or remote memory buffers. This reduces the read size for a successful hinted window relative to an ordinary 4 MiB chunk by 16x; actual wall-clock performance on the friend's Windows system remains unmeasured. Existing TLS 1.2 and 1.3 validators are unchanged.
2. Avoid the invalid OpenGL diagnostic pixel read from a multisampled framebuffer in both preview widgets. Continue the normal Qt framebuffer-resolve and presentation path unchanged. Diagnostic text explicitly states that sampling was skipped and why.
3. Read only the first **28 bytes** of an existing canonical KTX2 to choose resolution rank. Read full bytes only when comparing equal-resolution payloads; retain every distinct archived raw pack, canonical upgrades and the ordering lock.
4. Consistently label internal asset type 60 `LocalizationTableTranslation` in the cache manager and asset replacement maps.

Regression tests cover nearby-hint hits, exhaustive fallback, stale-region handling, both viewer diagnostic paths, resolution upgrade/downgrade/equal-byte comparison and asset-type naming. The focused proxy/TLS/cache/Qt suite passed **581 tests, 5 skipped, 3 subtests**, with zero changed-file Ruff/Pyright errors, before a final standalone asset-type mapping test was added and passed.

## Validation not yet done

Only synthetic Windows `NtReadVirtualMemory` testing was available for the new nearby scan; the user's friend's Windows process was not accessible from this machine. The improvement has not yet been timed on the affected Windows hardware. Neither the OpenGL diagnostic change nor the external CONNECT timeouts was tested on that AMD Windows PC. The change remains local until reviewed or approved for push.
