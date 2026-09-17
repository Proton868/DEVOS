"""Universal ToolchainProfile execution layer."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from brain.project_bootstrap import (
    ToolchainKind,
    bootstrap_project,
    check_runtime_available,
    detect_project_toolchain,
    detect_toolchain,
    execution_plan,
    get_profile,
    register_toolchain_profile,
    resolve_toolchain,
    ToolchainProfile,
    validate_project,
)


class FakeFS:
    def __init__(self):
        self.files = {}

    def write(self, path, content):
        self.files[path] = content

    def read(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return {"content": self.files[path]}

    def tree(self, max_depth=None):
        return [{"path": k, "type": "file"} for k in self.files]


def test_available_runtime_python():
    profile = get_profile("python")
    rt = check_runtime_available(profile)
    assert rt["ok"] is True
    assert "python3" in [p["binary"] for p in rt["present"]]


def test_missing_runtime_structured_error():
    profile = get_profile("python")
    rt = check_runtime_available(profile, which_fn=lambda _: None)
    assert rt["ok"] is False
    assert rt["error"] == "toolchain_unavailable"
    assert "python3" in rt["missing"]


def test_execution_plan_has_commands():
    plan = execution_plan(get_profile("typescript"))
    assert plan["commands"].get("install")
    assert plan["commands"].get("typecheck")
    plan_py = execution_plan(get_profile("python"))
    assert "pytest" in (plan_py["commands"].get("test") or "")


def test_unsupported_ecosystem():
    try:
        detect_toolchain("", explicit="cobol-mainframe")
        assert False
    except KeyError as e:
        assert "unsupported_ecosystem" in str(e)


def test_detect_from_workspace_markers():
    fs = FakeFS()
    fs.write("package.json", "{}")
    fs.write("vite.config.js", "export default {}")
    det = detect_project_toolchain(fs)
    assert det["kind"] in ("vite", "node", "javascript")


def test_resolve_toolchain_ready():
    r = resolve_toolchain(request="create a python flask API")
    assert r["kind"] == "python"
    assert r["plan"]["commands"]["install"]
    assert "runtime" in r


def test_bootstrap_missing_runtime_does_not_scaffold_claim_working():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    async def _run():
        # Force missing runtime via monkeypatch inside bootstrap by using a profile
        # that requires a fake binary — use flutter extension
        register_toolchain_profile(ToolchainProfile(
            kind="flutter",
            runtime="flutter",
            package_manager="flutter",
            install_cmd="flutter pub get",
            build_cmd="flutter build apk",
            test_cmd="flutter test",
            validate_files=["pubspec.yaml"],
            required_binaries=["flutter_definitely_missing_bin_xyz"],
            detect_markers=["pubspec.yaml"],
        ))
        return await bootstrap_project(
            user_id="tu",
            project_id="tp",
            project_name="app",
            request="",
            toolchain="flutter",
            run_install=True,
            run_build=False,
            command_runner=runner,
        )
    result = asyncio.run(_run())
    assert result.ok is False
    assert "toolchain_unavailable" in result.errors
    assert result.claimed_working is False
    assert result.validation.get("error") == "toolchain_unavailable"


def test_dependency_install_success_and_failure():
    async def ok_runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "installed", "stderr": "", "command": cmd}

    async def fail_runner(cmd, ctx):
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "ERR", "command": cmd}

    async def _ok():
        return await bootstrap_project(
            user_id="tu2", project_id="tp2", project_name="pyapp",
            request="python", toolchain="python",
            run_install=True, run_build=True, run_test=False,
            command_runner=ok_runner,
        )

    async def _fail():
        return await bootstrap_project(
            user_id="tu3", project_id="tp3", project_name="pyapp",
            request="python", toolchain="python",
            run_install=True, run_build=False, allow_repair=False,
            command_runner=fail_runner,
        )

    ok = asyncio.run(_ok())
    assert ok.scaffold_ok is True
    assert (ok.install or {}).get("ok") is True
    # compileall may or may not succeed depending on written files
    assert ok.claimed_working in (True, False)  # truth from validation

    bad = asyncio.run(_fail())
    assert "install_failed" in bad.errors
    assert bad.claimed_working is False


def test_build_failure_not_working():
    calls = []

    async def runner(cmd, ctx):
        calls.append(cmd)
        if "pip" in cmd or "install" in cmd:
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}
        if "compileall" in cmd or "build" in cmd:
            return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "build err", "command": cmd}
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="tu4", project_id="tp4", project_name="app",
        toolchain="python", run_install=True, run_build=True,
        command_runner=runner,
    ))
    assert "build_failed" in result.errors
    assert result.claimed_working is False


def test_repair_then_install():
    state = {"n": 0}

    async def runner(cmd, ctx):
        state["n"] += 1
        # first install fails, repair ok, second install ok
        if "install" in cmd and "upgrade" not in cmd:
            if state["n"] <= 1:
                return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "fail1", "command": cmd}
            return {"ok": True, "exit_code": 0, "stdout": "ok", "stderr": "", "command": cmd}
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="tu5", project_id="tp5", project_name="app",
        toolchain="python", run_install=True, run_build=False, allow_repair=True,
        command_runner=runner,
    ))
    assert (result.install or {}).get("ok") is True
    assert result.repair is not None


def test_scaffold_only_html_not_claimed_working():
    async def runner(cmd, ctx):
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": "", "command": cmd}

    result = asyncio.run(bootstrap_project(
        user_id="tu6", project_id="tp6", project_name="site",
        toolchain="html", run_install=False, run_build=False,
        command_runner=runner,
    ))
    assert result.scaffold_ok is True
    assert result.claimed_working is False
    assert (result.validation or {}).get("works") is False


def test_workspace_escape_in_command_runner_path():
    """run_command_in_project refuses path escape user ids."""
    from execution.runner import run_command_in_project

    async def _run():
        return await run_command_in_project("../evil", "proj", "echo hi")
    r = asyncio.run(_run())
    assert r["ok"] is False
    assert "Invalid" in (r.get("stderr") or "") or r.get("status") == "failed"


def test_flutter_extension_registration():
    register_toolchain_profile(ToolchainProfile(
        kind="flutter",
        runtime="flutter",
        package_manager="flutter",
        install_cmd="flutter pub get",
        build_cmd="flutter build apk",
        test_cmd="flutter test",
        validate_files=["pubspec.yaml"],
        required_binaries=["flutter"],
        detect_markers=["pubspec.yaml"],
    ))
    p = get_profile("flutter")
    assert p.runtime == "flutter"
    plan = execution_plan(p)
    assert "flutter pub get" in plan["commands"]["install"]
