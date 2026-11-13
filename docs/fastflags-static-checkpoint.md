# Local Roblox FastVariable static scans

Fleasion provides **read-only offline Player scanning** without launching Roblox, patching installed files, decrypting protected executable code, or using a debugger. The scanner supports installed macOS and Linux Roblox clients as well as Windows. The independent Windows raw-initializer view covers additional 24-byte table records, including those whose type cannot yet be resolved.

## FastFlag browser sources

The FastFlag browser offers **two** sources, with the local client labeled for the current platform or selected installation:

- **Public catalog** (default): Published ClientSettings values and public FastVariables inventories; it is not tied to a single installed Player
- **Client - Windows / Sober / Mocktail / Nightcap / Cordial / macOS**: Detect the installed Roblox Player and scan the platform-appropriate binary. On **Windows**, read the installed `RobloxPlayerBeta.exe` and display the coherent 24-byte name/value records as initialized on-disk storage. Optional `FVariables.txt` matches provide unverified cross-build type hints; ambiguously typed entries remain visible in hexadecimal but cannot be added under invented qualified names. A Windows-specific **Choose EXE** control can select a different installed binary. On **Linux**, inspect the selected Android-client ELF metadata and registration thunks; on **macOS**, inspect Mach-O registrations. Those platforms recover qualified names and native types when available, with remaining types inferred cautiously from public inventories or independently verified client caches

The Linux and macOS dialogs do **not** show a Windows EXE source. The last chosen browsing source (Public catalog or Client) is persisted in Fleasion settings and restored after reopening or relaunching; Client follows the platform and selected installation. The source and family dropdowns explicitly fit their longest visible option plus the arrow/padding space, recalculating when client labels or family counts change so the search box retains the remaining row width.

**Public catalog caching:** The merged public inventory is cached in `fflag_browser.json` for **24 hours**, shared across Fleasion launches and the custom FastFlag editor. A valid cached inventory is loaded directly when the browser opens; it is never downloaded on each opening. Opening the Modifications tab, or enabling custom FastFlags, also triggers a bounded background prefetch **only when custom flags are enabled and the catalog is stale**. Prefetching uses a shared in-process lock, prevents immediate repeated attempts after failures (15-minute backoff), and refreshes unprefixed-name warnings when the inventory becomes available. Explicit **Refresh** forces an update. Cache writes are atomic and logged, and the catalog survives Fleasion's normal object-cache clearing actions on Linux, macOS and Windows.

Unprefixed custom FastFlags are resolved to qualified client families using the **union of the 24-hour public catalog and the selected installed Player’s cached native registrations**. Name lookup and runtime delivery use the same selected-client evidence, without rewriting saved names or rescanning binaries. A single matching qualified type is applied directly; multiple matches are tried with a **white ambiguity indicator**. A native registration without a recoverable type also gets a white warning and tries the possible client types. **Orange indicates that the name is missing from all currently available inventories.** An installed-client scan refreshes the custom editor’s warning indicators immediately.

Native-client scans store immutable snapshots under Fleasion's per-client FastFlag scan cache, validate changed binaries using SHA-256, and reuse a matching scan without another binary read. Source changes invalidate pending results. The explicit **Refresh** action rescans the selected Player. A stale or unsupported format fails conservatively rather than producing invented registrations.

## Offline Windows JSON export

```bash
uv run python scripts/export_windows_fastflags.py \
  --exe '/path/to/RobloxPlayerBeta.exe' \
  --fvariables '/path/to/FVariables.txt' \
  --output './windows-fastvariables.json'
```

The `--fvariables` input is optional. The exported JSON includes `name`, `value_rva`, `raw_u64`, `qualified_hint`, `scalar_type_hint`, and `initial_value_hint`, plus the EXE SHA-256 and an explicit `runtime_defaults_verified: 0`. Neither the binary nor the FVariables input can be overwritten by the export, including by an alias to the same file.

## Scope and verification boundaries

- Windows 0.745–0.747 sample binaries contain more than 13,000 coherent 24-byte records. Earlier Windows builds and unrecognized layouts may yield weaker name evidence or fail closed
- The installed-client scanner preserves the exact type-verification status from native metadata. Public FVariables names and cross-client hints are *not* proof of the installed binary's type; absent or ambiguous qualifiers remain unresolved
- Android and macOS scanners primarily recover registrations and type metadata, not arbitrary initialized C++ values. The Windows raw-slot view is the separate specialized name/initial-storage extractor
- Raw or published values may differ from the actual effective runtime configuration because of C++ constructors, initialization, remote settings, and runtime overrides
- No unfinished Hyperion code-decryption, emulator, QEMU/VM-debugging, protected-branch, or reverse-engineering research modules or experimental scripts are included

The checkpoint is intentionally bounded to tested production-facing static metadata inspection and UI integration, not the unfinished version-independent native type/decryptor project.
