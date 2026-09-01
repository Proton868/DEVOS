"""Peripheral execution surfaces must declare PathClass / UCI authority."""

def test_execution_layer_requires_path_class_param():
    import inspect
    from execution.runner import ExecutionLayer
    sig = inspect.signature(ExecutionLayer.run)
    assert "path_class" in sig.parameters
    sig2 = inspect.signature(ExecutionLayer.install_packages)
    assert "path_class" in sig2.parameters
    assert "capability" in sig2.parameters

def test_search_web_requires_uci_flag():
    src = open("execution/search.py").read()
    assert "uci_authorized" in src
    assert "requires prior UCI authorization" in src

def test_loop_passes_uci_authorized_to_search():
    src = open("core/loop.py").read()
    assert "uci_authorized=True" in src
    assert "network_allowed_for_capability" in src

def test_marketplace_install_uses_job_and_capability():
    src = open("api/routes/marketplace.py").read()
    assert "CAP_PACKAGE_INSTALL" in src or "package.install" in src
    assert "begin_execution_job" in src
    assert "record_path_evidence" in src
    assert "require_authority" in src

def test_autoresearch_uses_job_and_authority():
    src = open("brain/autoresearch.py").read()
    assert "begin_execution_job" in src
    assert "require_authority" in src
    assert "allow_network=False" in src
    assert "tenant_id" in src

def test_script_runner_classifies_path():
    src = open("execution/script_runner.py").read()
    assert "PathClass.HUMAN_ONLY" in src
    assert "PathClass.DURABLE" in src
    assert "require_authority" in src

def test_path_class_enum_complete():
    from governance.execution_pipeline import PathClass
    names = {p.value for p in PathClass}
    assert {"durable", "non_durable", "read_only", "human_only"} <= names

def test_authority_module_exports():
    from governance.execution_authority import (
        require_authority, recent_authority_log, CAP_PACKAGE_INSTALL, CAP_SEARCH_WEB,
    )
    assert CAP_PACKAGE_INSTALL.startswith("ucip:")
