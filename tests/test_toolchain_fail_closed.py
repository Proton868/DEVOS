"""Authoritative fail-closed rules for ToolchainProfile bootstrap."""
from __future__ import annotations

import asyncio

from brain.project_bootstrap import (
    ERR_BUILD_FAILED,
    ERR_INSTALL_FAILED,
    ERR_SYSTEM_INSTALL_BLOCKED,
    ERR_TOOLCHAIN_UNAVAILABLE,
    ERR_UNSUPPORTED_ECOSYSTEM,
    ToolchainProfile,
    apply_fail_closed_claims,
    bootstrap_project,
    check_runtime_available,
    get_profile,
    is_system_level_install_command,
    register_toolchain_profile,
    guarded_command_runner,
)
from execution.runner import run_command_in_project


def test_missing_binary_toolchain_unavailable():
    register_toolchain_profile(ToolchainProfile(
        kind="missingrt",
        runtime="no_such_runtime_bin_xyz",
        package_manager="none",
        install_cmd=None,
        build_cmd=None,
        test_cmd=None,
        validate_files=["README.md"],
        required_binaries=["no_such_runtime_bin_xyz"],
        requires_install=False,
    ))
    result = asyncio.run(bootstrap_project(
        user_id="fc1", project_id="p1", project_name="x",
        toolchain="missingrt", run_install=False, run_build=False,
        command_runner=lambda c, x: asyncio.sleep(0, result={"ok": True}),
    ))
    # command_runner may not be async lambda correctly - use proper async
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="fc1", project_id="p1", project_name="x",
        toolchain="missingrt", run_install=False, run_build=False,
        command_runner=runner,
    ))
    assert ERR_TOOLCHAIN_UNAVAILABLE in result.errors
    assert result.error_code == ERR_TOOLCHAIN_UNAVAILABLE
    assert result.claimed_working is False
    assert (result.validation or {}).get("works") is False
    assert result.files_written == []  # no silent progress without runtime


def test_unsupported_ecosystem():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}
    result = asyncio.run(bootstrap_project(
        user_id="fc2", project_id="p2", project_name="x",
        toolchain="cobol_mainframe_xyz", command_runner=runner,
    ))
    assert ERR_UNSUPPORTED_ECOSYSTEM in result.errors
    assert result.error_code == ERR_UNSUPPORTED_ECOSYSTEM
    assert result.claimed_working is False


def test_install_failure_claimed_working_false():
    async def runner(cmd, ctx):
        if "pip" in cmd or "install" in cmd:
            return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "nope", "command": cmd}
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="fc3", project_id="p3", project_name="py",
        toolchain="python", run_install=True, run_build=False, allow_repair=False,
        command_runner=runner,
    ))
    assert ERR_INSTALL_FAILED in result.errors
    assert result.claimed_working is False
    assert (result.validation or {}).get("works") is False


def test_build_failure_claimed_working_false():
    async def runner(cmd, ctx):
        if "compileall" in cmd or "build" in cmd:
            return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "build fail", "command": cmd}
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="fc4", project_id="p4", project_name="py",
        toolchain="python", run_install=True, run_build=True,
        command_runner=runner,
    ))
    assert ERR_BUILD_FAILED in result.errors
    assert result.claimed_working is False


def test_html_never_runtime_proven():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="fc5", project_id="p5", project_name="site",
        toolchain="html", run_install=False, run_build=False,
        command_runner=runner,
    ))
    assert result.scaffold_ok is True
    assert result.claimed_working is False
    assert (result.validation or {}).get("works") is False
    assert (result.validation or {}).get("runtime_proven") is False
    assert (result.validation or {}).get("scaffold_only") is True


def test_path_escape_blocked_by_runner():
    async def _run():
        return await run_command_in_project("../escape", "p", "echo hi")
    r = asyncio.run(_run())
    assert r["ok"] is False
    assert "Invalid" in (r.get("stderr") or "") or "escape" in (r.get("stderr") or "").lower()


def test_system_install_blocked():
    assert is_system_level_install_command("sudo apt-get install python3") is True
    assert is_system_level_install_command("python3 -m pip install -r requirements.txt") is False

    async def inner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "should not run", "stderr": "", "command": cmd}

    r = asyncio.run(guarded_command_runner("apt-get install -y nodejs", {"user_id": "u", "project_id": "p"}, inner=inner))
    assert r["ok"] is False
    assert r.get("error") == ERR_SYSTEM_INSTALL_BLOCKED


def test_bootstrap_blocks_system_install_command():
    async def inner(cmd, ctx):
        # If system install leaked through, mark success (test should still fail closed)
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    # Register profile that wrongly asks for apt (should be blocked by guard)
    register_toolchain_profile(ToolchainProfile(
        kind="badpkg",
        runtime="python3",
        package_manager="apt",
        install_cmd="apt-get install -y foobar",
        build_cmd=None,
        test_cmd=None,
        validate_files=["README.md"],
        required_binaries=["python3"],
        requires_install=True,
    ))
    result = asyncio.run(bootstrap_project(
        user_id="fc6", project_id="p6", project_name="x",
        toolchain="badpkg", run_install=True, run_build=False, allow_repair=False,
        command_runner=inner,
    ))
    assert result.claimed_working is False
    assert ERR_SYSTEM_INSTALL_BLOCKED in result.errors or ERR_INSTALL_FAILED in result.errors


def test_apply_fail_closed_html_invariant():
    from brain.project_bootstrap import BootstrapResult
    profile = get_profile("html")
    r = BootstrapResult(
        ok=True,
        toolchain="html",
        profile=profile.to_dict(),
        scaffold_ok=True,
        files_written=["index.html"],
        validation={"structure_ok": True, "works": True, "scaffold_only": False},
        claimed_working=True,
    )
    r = apply_fail_closed_claims(r, kind_value="html", profile=profile, run_install=False, run_build=False, run_test=False)
    assert r.claimed_working is False
    assert r.validation["works"] is False
