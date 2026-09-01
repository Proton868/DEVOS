"""Regression tests that freeze the governance design."""

def test_package_install_in_registry():
    from governance.capability_registry import CapabilityRegistry
    reg = CapabilityRegistry()
    desc = reg.get("ucip:package.install")
    assert desc is not None, "ucip:package.install must be registered"
    assert desc.slug == "ucip:package.install"
    assert getattr(desc, "requires_network", False) is True

def test_package_install_in_action_map_and_trust():
    from governance.ucip import ACTION_TO_CAP, TRUST_LEVEL_CAPS, TrustLevel
    assert ACTION_TO_CAP.get("install_packages") == "ucip:package.install"
    assert ACTION_TO_CAP.get("package_install") == "ucip:package.install"
    assert "ucip:package.install" in TRUST_LEVEL_CAPS[TrustLevel.OPERATOR]
    assert "ucip:package.install" in TRUST_LEVEL_CAPS[TrustLevel.AUTONOMOUS]

def test_script_runner_canonical_chain_in_source():
    src = open("execution/script_runner.py").read()
    assert "begin_execution_job" in src
    assert "require_authority" in src
    assert "record_path_evidence" in src
    assert "ScriptRun" in src
    assert "authority_snapshot" in src
    assert "PathClass.DURABLE" in src
    assert "PathClass.HUMAN_ONLY" in src
    # job created before executor.run
    assert src.find("begin_execution_job") < src.find("executor.run")

def test_authority_constant_matches_registry():
    from governance.execution_authority import CAP_PACKAGE_INSTALL
    from governance.capability_registry import CapabilityRegistry
    assert CapabilityRegistry().get(CAP_PACKAGE_INSTALL) is not None

def test_no_brain_loop_bypass():
    import pathlib
    allowed = {
        "governance/execution_pipeline.py",
        "workers/runtime.py",
        "cognitive/coordinator.py",
        "core/loop.py",
    }
    for path in pathlib.Path(".").rglob("*.py"):
        if "test_" in path.name or "node_modules" in str(path):
            continue
        text = path.read_text(errors="ignore")
        if "BrainExecutionLoop(" in text:
            assert any(str(path).endswith(a) or a in str(path) for a in allowed), path
