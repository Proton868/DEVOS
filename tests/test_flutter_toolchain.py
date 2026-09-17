"""Governed Flutter toolchain unit tests (offline)."""
from __future__ import annotations
import io, json, tarfile, threading
from pathlib import Path
import pytest
from execution.flutter_toolchain import (
    CAP_FLUTTER_SDK_PROVISION, ProvisionRequest, assert_no_system_package_manager,
    detect_flutter_project, flutter_command_plan, platform_build_limits,
    probe_flutter_runtime, provision_flutter_sdk, resolve_expected_checksum,
    safe_extract_tar, validate_download_url,
)

class FakeFS:
    def __init__(self, files=None): self.files = dict(files or {})
    def read(self, path):
        if path not in self.files: raise FileNotFoundError(path)
        return {"content": self.files[path]}
    def tree(self, max_depth=None):
        out = []
        for k in self.files:
            out.append({"path": k, "type": "file"})
            parts = k.split("/")
            for i in range(len(parts)-1):
                out.append({"path": "/".join(parts[:i+1]), "type": "dir"})
        return out

def test_detect_flutter():
    fs = FakeFS({"pubspec.yaml": "name: app\ndependencies:\n  flutter:\n    sdk: flutter\n", "lib/main.dart": "void main(){}"})
    assert detect_flutter_project(fs).is_flutter_project

def test_pure_dart():
    assert not detect_flutter_project(FakeFS({"pubspec.yaml": "name: pure\nenvironment:\n  sdk: '>=3.0.0'\n"})).is_flutter_project

def test_bootstrap_detect():
    from brain.project_bootstrap import detect_project_toolchain, detect_toolchain, get_profile
    fs = FakeFS({"pubspec.yaml": "name: x\ndependencies:\n  flutter:\n    sdk: flutter\n", "lib/main.dart": "x"})
    assert detect_project_toolchain(fs)["kind"] == "flutter"
    assert detect_toolchain("flutter app") == "flutter"
    assert get_profile("flutter").install_cmd == "flutter pub get"

def test_runtime_available():
    info = probe_flutter_runtime(which_fn=lambda n: f"/bin/{n}" if n in ("flutter","dart") else None,
                                 run_fn=lambda a: (0, "Flutter 3.24.5 • channel stable\nDart SDK version: 3.5.4", ""), prefer_managed=False)
    assert info.flutter_available and info.flutter_version == "3.24.5"

def test_runtime_missing():
    assert probe_flutter_runtime(which_fn=lambda _: None, prefer_managed=False).error == "toolchain_unavailable"

def test_plan():
    assert flutter_command_plan(upgrade=True)["commands"]["install"] == "flutter pub get"

def test_mission_fail_closed():
    import asyncio
    from brain.project_bootstrap import bootstrap_project, register_toolchain_profile, ToolchainProfile, ERR_TOOLCHAIN_UNAVAILABLE
    register_toolchain_profile(ToolchainProfile(kind="flutter", runtime="flutter", package_manager="flutter",
        install_cmd="flutter pub get", build_cmd="flutter build apk --debug", test_cmd="flutter test",
        validate_files=["pubspec.yaml"], required_binaries=["flutter_missing_xyz"], requires_install=True))
    async def runner(cmd, ctx): return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}
    r = asyncio.run(bootstrap_project(user_id="u", project_id="p", project_name="a", toolchain="flutter", run_install=True, run_build=True, command_runner=runner))
    assert r.claimed_working is False
    assert r.error_code == ERR_TOOLCHAIN_UNAVAILABLE or ERR_TOOLCHAIN_UNAVAILABLE in r.errors

def test_auth_required(tmp_path):
    assert provision_flutter_sdk(ProvisionRequest(authorized=False, sdk_root=tmp_path/"s")).error_code == "authorization_required"

def test_human_gated():
    from governance.agency_evolution import ALWAYS_HUMAN_GATED
    assert CAP_FLUTTER_SDK_PROVISION in ALWAYS_HUMAN_GATED

def test_url():
    assert validate_download_url("https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_3.24.5-stable.tar.xz")[0]
    assert not validate_download_url("https://evil.example/x.tar.xz")[0]

def test_checksum(tmp_path):
    r = provision_flutter_sdk(ProvisionRequest(authorized=True, sdk_root=tmp_path/"s", download_fn=lambda u,d: d.write_bytes(b"X"*4096), checksum_fn=lambda u: "0"*64))
    assert r.error_code == "checksum_failure"

def test_traversal(tmp_path):
    dest = tmp_path/"e"; dest.mkdir(); archive = tmp_path/"e.tar"
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo(name="../escape.txt"); data=b"x"; info.size=len(data); tf.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="traversal|escape"):
        safe_extract_tar(archive, dest)

def test_symlink(tmp_path):
    dest = tmp_path/"e"; dest.mkdir(); archive = tmp_path/"s.tar"
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo(name="link"); info.type=tarfile.SYMTYPE; info.linkname="/etc/passwd"; tf.addfile(info)
    with pytest.raises(ValueError, match="symlink"):
        safe_extract_tar(archive, dest)

def test_partial(tmp_path):
    def dl(u, d):
        with tarfile.open(d, "w") as tf:
            info=tarfile.TarInfo(name="readme.txt"); data=b"nope"; info.size=len(data); tf.addfile(info, io.BytesIO(data))
    root = tmp_path/"sdk"
    r = provision_flutter_sdk(ProvisionRequest(authorized=True, sdk_root=root, download_fn=dl))
    assert not r.ok and not (root/"flutter"/"bin"/"flutter").exists()

def test_idempotent(tmp_path):
    root = tmp_path/"sdk"; b = root/"flutter"/"bin"; b.mkdir(parents=True)
    f=b/"flutter"; f.write_text("#!/bin/sh\necho Flutter 3.24.5\n"); f.chmod(0o755)
    (b/"dart").write_text("#!/bin/sh\necho version: 3.5.0\n"); (b/"dart").chmod(0o755)
    r = provision_flutter_sdk(ProvisionRequest(authorized=True, sdk_root=root, download_fn=lambda u,d: (_ for _ in ()).throw(AssertionError())))
    assert r.ok and r.evidence.get("idempotent")

def test_concurrent(tmp_path):
    root = tmp_path/"sdk"; started, release = threading.Event(), threading.Event()
    def slow(u, d): started.set(); release.wait(5); d.write_bytes(b"x"*100)
    t = threading.Thread(target=lambda: provision_flutter_sdk(ProvisionRequest(authorized=True, sdk_root=root, download_fn=slow))); t.start()
    assert started.wait(3)
    r2 = provision_flutter_sdk(ProvisionRequest(authorized=True, sdk_root=root, download_fn=slow))
    assert r2.error_code == "concurrent_install"; release.set(); t.join(5)

def test_no_sudo():
    assert assert_no_system_package_manager("flutter pub get")
    assert not assert_no_system_package_manager("sudo apt install flutter")

def test_success(tmp_path):
    import hashlib
    root = tmp_path/"sdk"; staging = tmp_path/"arch"/"flutter"/"bin"; staging.mkdir(parents=True)
    (staging/"flutter").write_text("#!/bin/sh\necho Flutter 3.24.5\n"); (staging/"flutter").chmod(0o755)
    (staging/"dart").write_text("#!/bin/sh\necho version: 3.5.0\n"); (staging/"dart").chmod(0o755)
    archive = tmp_path/"sdk.tar"
    with tarfile.open(archive, "w") as tf: tf.add(tmp_path/"arch"/"flutter", arcname="flutter")
    data = archive.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    r = provision_flutter_sdk(ProvisionRequest(
        authorized=True, sdk_root=root,
        download_fn=lambda u, d: d.write_bytes(data),
        checksum_fn=lambda u: digest,  # match synthetic archive (pins would otherwise reject)
    ))
    assert r.ok and Path(r.flutter_path).is_file()

def test_platform_limits():
    assert any("ios" in x.lower() or "macos" in x.lower() for x in platform_build_limits())

def test_pins(tmp_path, monkeypatch):
    digest = "a"*64; url = "https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_3.24.5-stable.tar.xz"
    pins = tmp_path/"pins.json"; pins.write_text(json.dumps({url: digest}))
    monkeypatch.setattr("execution.flutter_toolchain.CHECKSUMS_PATH", pins)
    assert resolve_expected_checksum(url) == digest

def test_isolation():
    from execution.isolation import UNTRUSTED_MIN_STRENGTH, IsolationStrength
    assert IsolationStrength.RESTRICTED in UNTRUSTED_MIN_STRENGTH

def test_pubspec_constraints_parsed():
    from execution.flutter_toolchain import parse_pubspec_constraints, constraint_compatible
    c = parse_pubspec_constraints(
        "name: x\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\n  flutter: '>=3.16.0'\n"
    )
    assert c["dart_sdk"] and "3.0.0" in c["dart_sdk"]
    assert constraint_compatible("3.5.0", ">=3.0.0 <4.0.0") is True
    assert constraint_compatible("4.0.0", ">=3.0.0 <4.0.0") is False
    assert constraint_compatible("3.16.0", "^3.16.0") is True


def test_flutter_status_unavailable_when_missing():
    from execution.flutter_toolchain import flutter_project_status, FlutterRuntimeInfo
    class FS:
        def read(self, path):
            return {"content": "name: app\ndependencies:\n  flutter:\n    sdk: flutter\n"}
        def tree(self, max_depth=None):
            return [{"path": "pubspec.yaml", "type": "file"}, {"path": "lib/main.dart", "type": "file"}]
    info = FlutterRuntimeInfo(False, False, error="toolchain_unavailable")
    st = flutter_project_status(FS(), runtime_info=info)
    assert st["detected"] is True
    assert st["available"] is False
    assert st["status"] == "unavailable"
    assert st["error"] == "toolchain_unavailable"
    assert st["trust_boundary"]["sdk_provision"] == "privileged_hitl"


def test_api_provision_always_hitl_no_self_auth():
    """API module must not expose authorized=True bypass for ordinary clients."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "api" / "routes" / "toolchain.py").read_text()
    assert "authorized: bool = False" not in src or "require_hitl" in src
    assert "request_sdk_provision_hitl" in src
    # No direct provision_flutter_sdk(..., authorized=True) from API without HITL
    assert "authorized=True" not in src


def test_universal_error_codes():
    from execution.toolchain import (
        TOOLCHAIN_UNAVAILABLE, DEPENDENCY_INSTALL_FAILED, map_bootstrap_error,
    )
    assert map_bootstrap_error("install_failed") == DEPENDENCY_INSTALL_FAILED
    assert map_bootstrap_error("toolchain_unavailable") == TOOLCHAIN_UNAVAILABLE


def test_profiles_include_flutter():
    from execution.toolchain import list_registered_profiles
    from brain import project_bootstrap  # noqa: F401 — register defaults
    kinds = {p.get("kind") for p in list_registered_profiles()}
    assert "flutter" in kinds
    assert "python" in kinds
