from __future__ import annotations
import pytest
from execution.flutter_toolchain import probe_flutter_runtime, flutter_command_plan
@pytest.fixture(scope="module")
def flutter_info():
    info = probe_flutter_runtime()
    if not info.flutter_available: pytest.skip("Flutter SDK not available")
    return info
def test_real_flutter_version(flutter_info):
    assert flutter_info.flutter_version and flutter_info.flutter_path
def test_real_plan(flutter_info):
    assert flutter_command_plan()["commands"]["install"] == "flutter pub get"
