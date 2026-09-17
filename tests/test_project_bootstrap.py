"""Governed project bootstrap — structure ≠ working claim."""
from __future__ import annotations

import asyncio
import pytest

from brain.project_bootstrap import (
    ToolchainKind,
    bootstrap_project,
    detect_toolchain,
    validate_project,
    PROFILES,
    scaffold_project,
)
from execution.files import FileService


@pytest.mark.parametrize(
    "text,expected",
    [
        ("create a next.js app", ToolchainKind.NEXTJS),
        ("vite react spa", ToolchainKind.VITE),
        ("angular dashboard", ToolchainKind.ANGULAR),
        ("python fastapi service", ToolchainKind.PYTHON),
        ("node express api", ToolchainKind.NODE),
        ("static html landing page", ToolchainKind.HTML),
    ],
)
def test_detect_toolchain(text, expected):
    assert detect_toolchain(text) == expected


def test_html_scaffold_not_claimed_working_without_proof():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "ok", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="testuser",
        project_id="htmlproj1",
        project_name="Landing",
        request="static html page",
        toolchain="html",
        run_install=False,
        run_build=False,
        command_runner=runner,
    ))
    assert result.scaffold_ok is True
    assert "index.html" in result.files_written
    assert result.validation["structure_ok"] is True
    assert result.claimed_working is False
    assert result.validation.get("works") is False


def test_python_install_failure_not_working():
    async def runner(cmd, ctx):
        if "pip install" in cmd:
            return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "network error", "command": cmd}
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="testuser",
        project_id="pyfail1",
        project_name="Svc",
        request="python project",
        toolchain="python",
        run_install=True,
        run_build=False,
        allow_repair=False,
        command_runner=runner,
    ))
    assert result.scaffold_ok is True
    assert result.claimed_working is False
    assert "install_failed" in result.errors


def test_python_success_path():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "ok", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="testuser",
        project_id="pyok1",
        project_name="Svc",
        request="python",
        toolchain="python",
        run_install=True,
        run_build=True,
        run_test=False,
        command_runner=runner,
    ))
    assert result.scaffold_ok is True
    assert (result.install or {}).get("ok") is True
    assert (result.build or {}).get("ok") is True
    assert result.validation["structure_ok"] is True
    assert result.claimed_working is True
    assert result.ok is True


def test_vite_template_files():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="testuser",
        project_id="vite1",
        project_name="Web",
        toolchain="vite",
        run_install=True,
        run_build=False,
        command_runner=runner,
    ))
    for f in ("package.json", "index.html", "vite.config.js", "src/main.js"):
        assert f in result.files_written


def test_repair_then_install():
    calls = []

    async def runner(cmd, ctx):
        calls.append(cmd)
        if "pip install -r" in cmd and calls.count(cmd) == 1:
            return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "fail", "command": cmd}
        return {"ok": True, "exit_code": 0, "stdout": "ok", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="testuser",
        project_id="pyrepair1",
        project_name="Svc",
        toolchain="python",
        run_install=True,
        run_build=False,
        allow_repair=True,
        command_runner=runner,
    ))
    assert result.repair is not None
    assert (result.install or {}).get("ok") is True
    assert result.claimed_working is True


def test_scaffold_alone_validation_marks_scaffold_only():
    fs = FileService("testuser", "struct1")
    written, err = scaffold_project(fs=fs, kind=ToolchainKind.NODE, project_name="n1")
    assert not err
    from brain.project_bootstrap import get_profile
    profile = get_profile(ToolchainKind.NODE)
    v = validate_project(fs, profile, written)
    assert v["structure_ok"] is True
    assert v["scaffold_only"] is True
    assert v["works"] is False


def test_nextjs_and_angular_detect_and_scaffold():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    r1 = asyncio.run(bootstrap_project(
        user_id="testuser", project_id="next1", project_name="N",
        toolchain="nextjs", run_install=False, run_build=False, command_runner=runner,
    ))
    assert "app/page.jsx" in r1.files_written
    r2 = asyncio.run(bootstrap_project(
        user_id="testuser", project_id="ang1", project_name="A",
        toolchain="angular", run_install=False, run_build=False, command_runner=runner,
    ))
    assert "angular.json" in r2.files_written
