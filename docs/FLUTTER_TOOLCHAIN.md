# Governed Flutter Toolchain

## Profile
Flutter ToolchainProfile: pub get, analyze, test, build. Detection via pubspec + sdk: flutter.

## SDK provisioning
Capability `ucip:toolchain.flutter_sdk_provision` (HITL-gated).
Install root: `data/toolchains/flutter`. Official HTTPS allowlist only. No sudo/apt/curl|bash.

API: GET/POST `/api/toolchain/flutter`.

## Platform limits
`flutter build ios` / `macos` require macOS + Xcode.

## Checksums
Pin digests in `data/toolchains/flutter_checksums.json`.

Full design, security boundary, and test coverage:
see `docs/AUDIT_AND_TOOLCHAIN_RESULTS_2026-09-17.md`.

## Universal toolchain contract

`execution/toolchain.py` defines stable status/error codes shared by all profiles:

| Code | Meaning |
|------|---------|
| `toolchain_unavailable` | Host SDK/runtime missing |
| `dependency_install_failed` | Project-local install failed under isolation |
| `build_failed` / `test_failed` / `analyze_failed` | Phase failures |
| `version_incompatible` | Installed SDK does not satisfy pubspec constraints |
| `provisioning_*` | HITL provision lifecycle |

**Trust boundary:** `flutter pub get` / analyze / test / build = **untrusted** isolation.  
`ucip:toolchain.flutter_sdk_provision` = **privileged HITL** into `data/toolchains/flutter` only.

Pubspec `environment.sdk` / `flutter` constraints are parsed; incompatible versions are reported, never silently substituted.
