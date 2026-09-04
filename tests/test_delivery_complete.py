import os
import pytest
from execution.artifacts import write_folder_files, ArtifactError
from execution.files import FileService
from execution.delivery_dag import build_delivery_dag, DELIVERY_NODE_TYPES
from execution.github_client import GitHubClient
from execution.isolation_runtime import isolation_available, wrap_command
from execution.deploy import get_adapter
from execution.deploy.base import DeploymentStatus


def test_folder_upload_nested_and_traversal():
    fs = FileService("fu", "fw1")
    metas = write_folder_files(fs, [("src/a.txt", b"a"), ("src/nested/b.txt", b"b")])
    assert len(metas) == 2
    with pytest.raises(ArtifactError):
        write_folder_files(fs, [("../x.txt", b"x")])


def test_delivery_dag_nodes():
    g = build_delivery_dag("deploy to vercel and github")
    ids = [n["id"] for n in g["nodes"]]
    assert "build" in ids and "deploy" in ids and "github_push" in ids
    assert set(DELIVERY_NODE_TYPES)


@pytest.mark.asyncio
async def test_github_mock_whoami():
    os.environ["DEVOS_GITHUB_MOCK"] = "1"
    u = await GitHubClient().whoami()
    assert u["login"] == "mock-user"
    del os.environ["DEVOS_GITHUB_MOCK"]


def test_isolation_wrap():
    info = isolation_available()
    cmd = wrap_command(["echo", "hi"], cwd="/tmp", net=False)
    assert isinstance(cmd, list)
    assert cmd[-1] == "hi"


@pytest.mark.asyncio
async def test_vercel_needs_root_for_deploy():
    ad = get_adapter("vercel")
    r = await ad.deploy(project_path="x", meta={}, credentials={"VERCEL_TOKEN": "fake"})
    assert r.status == DeploymentStatus.FAILED
    assert "workspace_root" in str(r.evidence)
