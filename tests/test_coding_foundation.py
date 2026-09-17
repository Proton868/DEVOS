"""Universal coding foundation: inspect, validate, ecosystems."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class _FakeFS:
    def __init__(self, root: Path):
        self.root = root

    def _resolve(self, rel: str) -> Path:
        return (self.root / rel).resolve()


def test_detect_python_project(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    from brain.coding_foundation import inspect_workspace, ProjectEcosystem

    insp = inspect_workspace(_FakeFS(tmp_path))
    assert insp.ecosystem == ProjectEcosystem.PYTHON
    assert insp.blank_project is False
    assert "pyproject.toml" in insp.config_files


def test_detect_nextjs(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "14.0.0"}, "scripts": {"build": "next build", "test": "jest"}})
    )
    (tmp_path / "next.config.js").write_text("module.exports={}")
    from brain.coding_foundation import inspect_workspace, ProjectEcosystem

    insp = inspect_workspace(_FakeFS(tmp_path))
    assert insp.ecosystem == ProjectEcosystem.NEXTJS
    assert any("build" in c for c in insp.suggested_build_commands)


def test_detect_vite(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"vite": "5.0.0"}, "scripts": {"build": "vite build"}})
    )
    (tmp_path / "vite.config.ts").write_text("export default {}")
    from brain.coding_foundation import inspect_workspace, ProjectEcosystem

    assert inspect_workspace(_FakeFS(tmp_path)).ecosystem == ProjectEcosystem.VITE


def test_detect_angular(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"@angular/core": "17.0.0"}})
    )
    (tmp_path / "angular.json").write_text("{}")
    from brain.coding_foundation import inspect_workspace, ProjectEcosystem

    assert inspect_workspace(_FakeFS(tmp_path)).ecosystem == ProjectEcosystem.ANGULAR


def test_detect_typescript(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"typescript": "5.0.0"}})
    )
    (tmp_path / "tsconfig.json").write_text("{}")
    from brain.coding_foundation import inspect_workspace, ProjectEcosystem

    assert inspect_workspace(_FakeFS(tmp_path)).ecosystem == ProjectEcosystem.TYPESCRIPT


def test_detect_html_static(tmp_path: Path):
    (tmp_path / "index.html").write_text("<html></html>")
    from brain.coding_foundation import inspect_workspace, ProjectEcosystem

    assert inspect_workspace(_FakeFS(tmp_path)).ecosystem == ProjectEcosystem.HTML_STATIC


def test_detect_shell_build(tmp_path: Path):
    (tmp_path / "Makefile").write_text("all:\n\t@echo ok\n")
    from brain.coding_foundation import inspect_workspace, ProjectEcosystem

    assert inspect_workspace(_FakeFS(tmp_path)).ecosystem == ProjectEcosystem.SHELL_BUILD


def test_files_alone_do_not_imply_tests():
    from brain.coding_foundation import evaluate_coding_validation

    r = evaluate_coding_validation(
        files_changed=[{"path": "a.py"}],
        require_tests=True,
    )
    assert r["ok"] is False
    assert "tests_not_proven" in r["reasons"]
    assert r["files_do_not_imply_tests_or_build"] is True


def test_command_and_tests_validation():
    from brain.coding_foundation import evaluate_coding_validation, normalize_command_result

    cmd = normalize_command_result(command="pytest -q", exit_code=0, stdout="2 passed")
    assert cmd.ok is True
    r = evaluate_coding_validation(
        files_changed=[{"path": "a.py"}],
        command_results=[cmd.to_dict()],
        tests_passed=True,
        require_tests=True,
    )
    assert r["ok"] is True
    assert "tests_ok" in r["proven"]


def test_failed_command_not_ok():
    from brain.coding_foundation import normalize_command_result

    c = normalize_command_result(command="npm test", exit_code=1, stderr="fail")
    assert c.ok is False
    assert c.exit_code == 1


def test_mission_acceptance_still_fail_closed_on_files_only_artifact_mission():
    from brain.mission_acceptance import evaluate_mission_acceptance

    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=[{"path": "src/app.ts"}],
        ponytail=None,
        evidence_refs=[],
        artifact_producing=True,
    )
    assert acc["ok"] is False


def test_agent_tools_still_require_ucip_caps():
    src = (ROOT / "brain" / "agent_tools.py").read_text()
    assert "run_command" in src
    assert "ucip:" in src or "capability" in src
    assert "MODE_TOOLS" in src


def test_coding_guidance_never_assumes_blank(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("flask==3.0\n")
    from brain.coding_foundation import inspect_workspace, coding_system_guidance

    g = coding_system_guidance(inspect_workspace(_FakeFS(tmp_path)))
    assert "do not assume blank" in g.lower() or "never" in g.lower() or "RULE" in g
