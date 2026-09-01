import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from api.routes.extras import BuildRequest
from api.routes.scripts import ScriptCreate
from execution.files import FileService, PathViolation
from execution.terminal import TerminalService, DeniedCommand
from governance.ucip import AgentIdentity, TrustLevel


@pytest.mark.asyncio
async def test_terminal_service_blocks_dangerous_commands():
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        service = TerminalService("user", "proj")
        with pytest.raises(DeniedCommand):
            await service.run("rm -rf /")


def test_file_service_rejects_path_escape():
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        service = FileService("user", "proj")
        with pytest.raises(PathViolation):
            service.read("../secret.txt")


def test_agent_identity_allows_worker_caps_with_star():
    root = AgentIdentity.create("u", "s", TrustLevel.ROOT)
    child = root.delegate(sub_caps={"ucip:execution.python"})
    assert child.has_cap("ucip:execution.python")


def test_trust_level_from_str_is_strict_and_clear():
    assert TrustLevel.from_str("operator") is TrustLevel.OPERATOR
    with pytest.raises(ValueError):
        TrustLevel.from_str("bogus")


def test_model_defaults_use_fresh_lists():
    first = ScriptCreate(name="a", code="print(1)")
    second = ScriptCreate(name="b", code="print(2)")
    assert first.tags == []
    assert second.tags == []
    assert first.tags is not second.tags

    build_a = BuildRequest(name="a", description="desc")
    build_b = BuildRequest(name="b", description="desc")
    assert build_a.features == []
    assert build_b.features == []
    assert build_a.features is not build_b.features
