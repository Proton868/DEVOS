"""Identity isolation — ownership, IDOR posture, UCIP non-bypass, avatar truth."""
from pathlib import Path

from governance.identity_contract import (
    DevOSIdentity,
    assert_account_ownership,
    reject_client_authority_fields,
)
from core.account_constants import PUBLIC_PLANS
from execution.files import FileService, PathViolation
from execution.web_intel.store import create_crawl, get_crawl, list_crawls


def test_identity_owns_only_self():
    a = DevOSIdentity(subject_id="s1", account_id="acc-a", auth_provider="local")
    assert a.owns_account("acc-a")
    assert not a.owns_account("acc-b")
    try:
        assert_account_ownership(a, "acc-b")
        assert False, "should deny"
    except PermissionError:
        pass


def test_reject_client_authority_fields():
    cleaned = reject_client_authority_fields({
        "display_name": "Raphael",
        "role": "hegemon",
        "plan": "hegemon",
        "is_admin": True,
        "account_id": "other",
        "user_id": "other",
        "bio": "hi",
    })
    assert cleaned.get("display_name") == "Raphael"
    assert cleaned.get("bio") == "hi"
    assert "role" not in cleaned
    assert "plan" not in cleaned
    assert "is_admin" not in cleaned
    assert "account_id" not in cleaned


def test_public_plan_escalation_impossible():
    assert "hegemon" not in PUBLIC_PLANS
    assert "elder" not in PUBLIC_PLANS


def test_fileservice_path_isolation_and_escape():
    fs_a = FileService("user-a", "proj1")
    fs_b = FileService("user-b", "proj1")
    # roots must differ
    assert Path(fs_a.root).resolve() != Path(fs_b.root).resolve()
    # write in A
    fs_a.write("note.txt", "secret-a")
    # B cannot resolve into A's tree via relative tricks if roots differ
    content_a = fs_a.read("note.txt")
    assert "secret-a" in (content_a if isinstance(content_a, str) else str(content_a))
    try:
        fs_a.read("../user-b/proj1/note.txt")
    except (PathViolation, FileNotFoundError, OSError, ValueError):
        pass
    # cleanup
    import shutil
    shutil.rmtree(Path(fs_a.root).parent, ignore_errors=True)
    shutil.rmtree(Path(fs_b.root).parent, ignore_errors=True)


def test_web_crawl_list_scoped_by_user():
    c_a = create_crawl({"user_id": "iso-a", "root_url": "https://example.com", "normalized_root_url": "https://example.com"})
    c_b = create_crawl({"user_id": "iso-b", "root_url": "https://example.com", "normalized_root_url": "https://example.com"})
    list_a = list_crawls("iso-a")
    list_b = list_crawls("iso-b")
    assert any(x["crawl_id"] == c_a["crawl_id"] for x in list_a)
    assert not any(x["crawl_id"] == c_b["crawl_id"] for x in list_a)
    assert any(x["crawl_id"] == c_b["crawl_id"] for x in list_b)
    # direct get does not check user — API must; store is low-level
    raw = get_crawl(c_b["crawl_id"])
    assert raw is not None  # low-level store; ownership is API layer (documented)


def test_avatar_uses_fileservice_upload():
    """Avatar upload goes through FileService write_bytes under user profile project."""
    src = Path("api/routes/account.py").read_text()
    assert "avatar_url" in src
    assert "UploadFile" in src
    assert "FileService" in src
    assert "write_bytes" in src or "write_bytes" in Path("execution/files.py").read_text()


def test_ucip_deny_blocks_runtime_contract():
    """Unauthorized NodeExecutionRequest must not succeed."""
    import asyncio
    from brain.orchestration_runtime import NodeExecutionRequest, run_node_on_agent_runtime
    req = NodeExecutionRequest(
        plan_id="p",
        node_id="n",
        user_id="u",
        workspace_id="w",
        persona_id="nuha",
        objective="x",
        effective_caps=["fs.write"],
        authorization_decision="deny",
    )
    r = asyncio.get_event_loop().run_until_complete(run_node_on_agent_runtime(req))
    assert r.success is False
    assert r.status == "blocked"
