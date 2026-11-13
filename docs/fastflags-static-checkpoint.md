# Local Roblox FastVariable static scans

Fleasion provides **read-only offline Player scanning** without launching Roblox, patching installed files, decrypting protected executable code, or using a debugger. The scanner supports installed macOS and Linux Roblox clients as well as Windows. The independent Windows raw-initializer view covers additional 24-byte table records, including those whose type cannot yet be resolved.

## FastFlag browser sources

The FastFlag browser offers three explicit sources:

- **Public catalog** (default): Published ClientSettings values and public FastVariables inventories; it is not tied to one installed Player
- **Windows EXE · initial slots**: Read a selected `RobloxPlayerBeta.exe` and display every coherent name/value/length table record. The 64-bit values are **initial on-disk storage**, not verified compiled constructor or runtime defaults. An optional `FVariables.txt` download supplies **unverified cross-build type hints**, accepted only when one qualified name matches the local suffix and the raw storage is compatible with a bool or signed 32-bit integer. Unknown types remain visible as hexadecimal data, but cannot be added under fabricated qualified names. The browser can select the newest installed Windows Player automatically, or users can choose another EXE
- **Installed client · registrations**: Detect the currently selected local Roblox Player, read its structured registrations, and recover qualified names and types where the *selected binary itself* contains sufficient evidence. Sober, Mocktail, Nightcap, and Cordial are supported via Android ELF metadata and x86-64 registration thunks; native macOS and Rosemary-selected macOS Player bundles use Mach-O metadata; Windows uses PE records and qualified log labels. Other types can be **inferred** from public inventories or independently verified cached clients, but are not marked native-verified. Displayed values are from the published catalog and may be absent; they are not claimed to be initialized or effective local defaults

The installed-client source stores immutable snapshots under Fleasion's per-client FastFlag scan cache, detects changed binaries using SHA-256, and can reuse a matching scan without another binary read. Selecting a different source does not overwrite a pending source's results. The browser's explicit **Refresh** action rescans the currently selected Player. A stale or unsupported format fails conservatively rather than producing invented registrations.

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
