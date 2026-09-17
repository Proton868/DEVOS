"""Structured build/test/debug — detect, run, parse, repair, exhaust."""
from __future__ import annotations

import asyncio
from pathlib import Path

from brain.check_runner import (
    CheckKind,
    CheckCommand,
    debug_check_loop,
    detect_checks,
    parse_failure,
    run_check,
    select_check,
)
from brain.project_bootstrap import bootstrap_project, ToolchainKind
from execution.files import FileService


def test_parse_pytest_failure_files():
    out = """
tests/test_foo.py::test_bar FAILED
E       AssertionError: boom
tests/test_foo.py:12: AssertionError
"""
    f = parse_failure("python_pytest", out, "", 1)
    assert "tests/test_foo.py" in f.likely_files
    assert "pytest" in f.summary.lower() or "FAILED" in out


def test_parse_tsc_errors():
    err = "src/app.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'.\n"
    f = parse_failure("typescript", "", err, 2)
    assert "src/app.ts" in f.likely_files
    assert any("TS2322" in c for c in f.error_codes)


def test_detect_checks_python_project():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    asyncio.get_event_loop().run_until_complete(bootstrap_project(
        user_id="chkuser",
        project_id="pydetect",
        project_name="P",
        toolchain="python",
        run_install=False,
        run_build=False,
        command_runner=runner,
    ))
    fs = FileService("chkuser", "pydetect")
    checks = detect_checks(fs)
    kinds = {c.kind for c in checks}
    assert CheckKind.TEST in kinds
    assert CheckKind.BUILD in kinds
    test_cmd = select_check(checks, CheckKind.TEST)
    assert test_cmd and "pytest" in test_cmd.command


def test_detect_checks_vite_project():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    asyncio.get_event_loop().run_until_complete(bootstrap_project(
        user_id="chkuser",
        project_id="vitedetect",
        project_name="V",
        toolchain="vite",
        run_install=False,
        run_build=False,
        command_runner=runner,
    ))
    fs = FileService("chkuser", "vitedetect")
    checks = detect_checks(fs)
    build = select_check(checks, CheckKind.BUILD)
    assert build is not None
    assert "build" in build.command
    assert build.adapter in ("vite", "node_npm")


def test_successful_build():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "ok", "stderr": "", "command": cmd}

    check = CheckCommand(kind=CheckKind.BUILD, command="npm run build", adapter="vite")
    result = asyncio.get_event_loop().run_until_complete(run_check(check, runner=runner))
    assert result.ok is True
    assert result.failure is None


def test_failed_build_parses():
    async def runner(cmd, ctx):
        return {
            "ok": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": "ERROR in src/main.js\nModule not found\nnpm ERR! code ELIFECYCLE\n",
            "command": cmd,
        }

    check = CheckCommand(kind=CheckKind.BUILD, command="npm run build", adapter="vite")
    result = asyncio.get_event_loop().run_until_complete(run_check(check, runner=runner))
    assert result.ok is False
    assert result.failure is not None
    assert "ELIFECYCLE" in result.failure.error_codes or result.failure.likely_files


def test_debug_loop_success_after_repair():
    calls = {"n": 0}

    async def runner(cmd, ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "ok": False,
                "exit_code": 1,
                "stdout": "FAILED tests/test_main.py::test_x\ntests/test_main.py:5: AssertionError\n",
                "stderr": "",
                "command": cmd,
            }
        return {"ok": True, "exit_code": 0, "stdout": "1 passed", "stderr": "", "command": cmd}

    async def edit_hook(files, failure):
        return {"ok": True, "files_changed": files or ["tests/test_main.py"]}

    async def runner_wrap(cmd, ctx):
        return await runner(cmd, ctx)

    # minimal fs with py markers
    async def boot_runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    asyncio.get_event_loop().run_until_complete(bootstrap_project(
        user_id="chkuser", project_id="repairloop", project_name="R",
        toolchain="python", run_install=False, run_build=False, command_runner=boot_runner,
    ))
    fs = FileService("chkuser", "repairloop")
    loop = asyncio.get_event_loop().run_until_complete(debug_check_loop(
        fs=fs,
        kind=CheckKind.TEST,
        runner=runner_wrap,
        edit_hook=edit_hook,
        max_attempts=3,
    ))
    assert loop.ok is True
    assert loop.attempts == 2
    assert loop.exhausted is False
    assert "tests/test_main.py" in loop.files_touched or loop.files_touched


def test_debug_loop_exhausted():
    async def runner(cmd, ctx):
        return {
            "ok": False,
            "exit_code": 1,
            "stdout": "FAILED tests/test_main.py::test_x\ntests/test_main.py:1: assert 0\n",
            "stderr": "",
            "command": cmd,
        }

    async def edit_hook(files, failure):
        return {"ok": True, "files_changed": ["tests/test_main.py"]}

    async def boot_runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    asyncio.get_event_loop().run_until_complete(bootstrap_project(
        user_id="chkuser", project_id="exhaustloop", project_name="E",
        toolchain="python", run_install=False, run_build=False, command_runner=boot_runner,
    ))
    fs = FileService("chkuser", "exhaustloop")
    loop = asyncio.get_event_loop().run_until_complete(debug_check_loop(
        fs=fs,
        kind="test",
        runner=runner,
        edit_hook=edit_hook,
        max_attempts=2,
    ))
    assert loop.ok is False
    assert loop.exhausted is True
    assert loop.attempts == 2


def test_nextjs_angular_adapters_detected():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    asyncio.get_event_loop().run_until_complete(bootstrap_project(
        user_id="chkuser", project_id="nextchk", project_name="N",
        toolchain="nextjs", run_install=False, run_build=False, command_runner=runner,
    ))
    fs = FileService("chkuser", "nextchk")
    checks = detect_checks(fs)
    build = select_check(checks, CheckKind.BUILD)
    assert build is not None
    assert build.adapter in ("nextjs", "node_npm")
