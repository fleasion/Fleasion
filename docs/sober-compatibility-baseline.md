# Sober / Roblox Android compatibility baseline

Read-only local inspection on **2026-10-31**. This describes one installed
version, not a general claim about all Sober releases or current Roblox builds.
No Roblox process was attached to, no TLS secrets were read, and Sober was not
launched, reconfigured, or patched.

| Property | Local observation |
| --- | --- |
| Sober Flatpak ID | `org.vinegarhq.Sober` |
| Sober release | `1.8.0` (`stable`, system installation) |
| Flatpak architecture | `x86_64` |
| Sober Flatpak commit | `7b6430c703e8178baa536062099fc5a1e0c7cf213c0789695fa7660b62cb9d26` |
| Flatpak commit date | `2026-10-04` UTC |
| Embedded Roblox Android package | `com.roblox.client` |
| Roblox Android version | `2.741.1061` (versionCode `3212`) |
| Android native library | `split_config.x86_64.apk` contains `lib/x86_64/libroblox.so` |
| APK CA bundle | `assets/ssl/cacert.pem`, 228,725 bytes, 147 PEM certificate blocks |
| CA bundle SHA-256 | `2eba303a7b3c87f0c52fa8f977b703ec6dea7de703f97644546d2d34e53e4cd9` |

The APK's native client binary contains the strings `SSL_CERT_FILE` and
`cacert.pem`. Those strings, and the APK's certificate asset, **do not prove**
which trust path the client uses for any specific HTTP request. No run-time
certificate validation or traffic-interception test was performed.

## Safe follow-up validation

1. Recheck package version and the CA bundle hash after installing newer Sober
   and Roblox Android client releases
2. Determine active client ALPN and HTTP protocol using ordinary diagnostics
   from an authorized local test environment
3. Independently verify whether Roblox's Android build uses the packaged CA
   bundle, a native trust source, or an alternate embedded trust implementation
4. Test authenticated localhost HTTP/1.1 and HTTP/2 feature parity before
   enabling any optional provider
5. Keep `SoberBackend` at `not_implemented` until an end-to-end supported
   session is demonstrated; the presence of a CA bundle is not evidence of
   compatibility

`src/fleasion/proxy/tls_backends/sober.py` remains only a fail-closed
placeholder. See `docs/tls-backend-handoff.md` for its common session contract.
