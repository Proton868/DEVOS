"""Real Flutter execution only when binary exists. Never fabricate PASS."""
from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.real_runtime


@pytest.fixture(scope="module")
def flutter_bin():
    path = shutil.which("flutter")
    if not path:
        # Managed SDK root
        from pathlib import Path
        from execution.flutter_toolchain import DEFAULT_SDK_ROOT
        cand = Path(DEFAULT_SDK_ROOT) / "flutter" / "bin" / "flutter"
        if cand.is_file():
            path = str(cand)
    return path


def test_flutter_missing_is_honest_unavailable(flutter_bin):
    from execution.flutter_toolchain import probe_flutter_runtime
    if flutter_bin:
        pytest.skip("Flutter present — covered by execution tests")
    info = probe_flutter_runtime(prefer_managed=True)
    assert info.flutter_available is False
    assert info.error == "toolchain_unavailable"


def test_flutter_real_version_when_present(flutter_bin):
    if not flutter_bin:
        pytest.skip("prerequisite-unavailable: Flutter SDK not on host")
    from execution.flutter_toolchain import probe_flutter_runtime
    info = probe_flutter_runtime()
    assert info.flutter_available
    assert info.flutter_version
    assert info.flutter_path
