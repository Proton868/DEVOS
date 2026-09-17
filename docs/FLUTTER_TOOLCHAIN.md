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
