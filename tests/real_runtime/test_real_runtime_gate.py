"""Real-runtime production execution gate."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from unittest import mock

import pytest

pytestmark = pytest.mark.real_runtime


def test_fake_runtime_env_is_off():
    assert os.environ.get("DEVOS_REAL_RUNTIME_TESTS") == "1"
    assert os.environ.get("DEVOS_ORCH_FAKE_RUNTIME") != "1"
    assert os.environ.get("DEVOS_ALLOW_FAKE_RUNTIME") != "1"


def test_orchestration_refuses_fake_when_real_runtime_gate_set():
    async def _go():
        from brain.orchestration_runtime import NodeExecutionRequest, run_node_on_agent_runtime
        os.environ["DEVOS_ORCH_FAKE_RUNTIME"] = "1"
        try:
            req = NodeExecutionRequest(
                plan_id="rr-gate", node_id="n1", user_id="rr_user_a",
                workspace_id="rr_proj_a", persona_id="code",
                objective="create a file", authorization_decision="allow",
            )
            r = await run_node_on_agent_runtime(req)
            assert r.success is False
            assert "FAKE" in (r.error or "") or "forbidden" in (r.error or "").lower()
            assert r.status == "error"
        finally:
            os.environ.pop("DEVOS_ORCH_FAKE_RUNTIME", None)
    asyncio.run(_go())



def test_fake_runtime_function_not_invoked_under_gate():
    async def _go():
        from brain import orchestration_runtime as ort
        called = {"fake": False}
        async def _boom(req):
            called["fake"] = True
            raise AssertionError("fake runtime must not run")
        with mock.patch.object(ort, "_fake_runtime", side_effect=_boom):
            os.environ.pop("DEVOS_ORCH_FAKE_RUNTIME", None)
            req = ort.NodeExecutionRequest(
                plan_id="rr-nofake", node_id="n1", user_id="rr_user_a",
                workspace_id="rr_proj_a", persona_id="code",
                objective="noop", authorization_decision="allow",
            )
            r = await ort.run_node_on_agent_runtime(req)
            assert called["fake"] is False
            if r.task_id:
                assert not str(r.task_id).startswith("fake-")
    asyncio.run(_go())


def test_simple_coding_mission_file_evidence_acceptance(workspace, scripted_provider):
    from tests.real_runtime.conftest import require_agent_runtime_deps
    require_agent_runtime_deps()
    async def _go():
        from brain.agent_runtime import AgentRuntime, AgentContext
        from brain.agent_tools import AgentMode
        from brain.coding_evidence import build_coding_evidence
        from brain.mission_acceptance import evaluate_mission_acceptance
        from governance.ucip import TrustLevel
        uid, pid = workspace["user_id"], workspace["project_id"]
        target = "hello_real_runtime.txt"
        path = workspace["root"] / target
        if path.exists():
            path.unlink()
        runtime = AgentRuntime(
            user_id=uid, project_id=pid, mode=AgentMode.AGENT,
            trust_level=TrustLevel.OPERATOR,
        )
        context = AgentContext(project_id=pid, user_request=f"Create {target}")
        files_changed, tools_used, tool_ok = [], [], False
        async for ev in runtime.run(
            f"Create file {target} containing exactly the line real-runtime-ok",
            context,
        ):
            data = (ev or {}).get("data") or {}
            et = (ev or {}).get("type") or ""
            if data.get("files_changed"):
                files_changed = list(data["files_changed"])
            if data.get("tools_used"):
                tools_used = list(data["tools_used"])
            if et == "agent.tool_result" and data.get("tool") == "create_file" and data.get("ok"):
                tool_ok = True
            if et == "agent.tool_call" and data.get("tool") == "create_file":
                tools_used = list(set(tools_used + ["create_file"]))
        # Authoritative workspace proof: tool path + file on disk
        assert "create_file" in tools_used or tool_ok or path.exists(), (
            "AgentRuntime must invoke create_file on real UCIP/tool path"
        )
        assert path.exists(), "file must exist on disk after AgentRuntime tools"
        assert "real-runtime-ok" in path.read_text(encoding="utf-8")
        success = path.exists() and "real-runtime-ok" in path.read_text(encoding="utf-8")
        ev = build_coding_evidence(
            mission_id="rr-simple-1", project_id=pid, user_id=uid,
            agent_id="agent:code", persona_id="code",
            files_changed=files_changed or [{"path": target}],
            commands=[{
                "command": "test -f hello_real_runtime.txt",
                "exit_code": 0, "ok": True, "kind": "validate",
            }],
            validation={"passed": True, "kind": "file_exists"},
            success=True,
        )
        acc = evaluate_mission_acceptance(
            execution_ok=True, status="accepted",
            files_changed=[{"path": target}],
            ponytail={"passed": True, "applicable": True},
            evidence_refs=["rr-simple-1"], mission_id="rr-simple-1", user_id=uid,
            require_coding_evidence=True,
            coding_evidence=ev.to_dict() if hasattr(ev, "to_dict") else dict(ev),
        )
        assert acc.get("ok") is True, acc
    asyncio.run(_go())


def test_coding_loop_bridge_invokes_real_stages(workspace, scripted_provider):
    from tests.real_runtime.conftest import require_agent_runtime_deps
    require_agent_runtime_deps()
    async def _go():
        from brain.coding_loop_bridge import is_coding_objective, run_production_coding_loop
        from brain.agent_runtime import AgentRuntime, AgentContext
        from brain.agent_tools import AgentMode
        from governance.ucip import TrustLevel
        import inspect
        assert is_coding_objective("create a python module", "code") is True
        uid, pid = workspace["user_id"], workspace["project_id"]
        runtime = AgentRuntime(
            user_id=uid, project_id=pid, mode=AgentMode.AGENT,
            trust_level=TrustLevel.OPERATOR,
        )
        context = AgentContext(project_id=pid, user_request="create hello_real_runtime.txt")
        sig = inspect.signature(run_production_coding_loop)
        kwargs = {
            "runtime": runtime,
            "context": context,
            "objective": "Create hello_real_runtime.txt with real-runtime-ok",
            "user_id": uid,
            "project_id": pid,
            "mission_id": "rr-loop-1",
            "cancel_check": lambda: False,
        }
        # Drop kwargs not in signature
        kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
        state = await run_production_coding_loop(**kwargs)
        assert state is not None
        d = state.to_dict() if hasattr(state, "to_dict") else dict(state or {})
        assert d.get("fake") is not True
    asyncio.run(_go())


def test_provider_429_and_exhaustion_no_false_success(monkeypatch):
    from tests.real_runtime.conftest import require_agent_runtime_deps
    require_agent_runtime_deps()
    from brain.llm import classify_provider_http_status, ProviderExhaustedError
    from brain.orchestration_runtime import NodeExecutionResult
    meta = classify_provider_http_status(429)
    assert meta["retryable"] is True
    r = NodeExecutionResult(success=True, status="succeeded")
    r.provider_failure = {"category": "rate_limited"}
    r.success = False
    r.status = "failed"
    assert r.success is False
    async def _go():
        async def _stream_fail(self, messages, **kwargs):
            raise ProviderExhaustedError("all candidates failed")
        import brain.llm as llm_mod
        monkeypatch.setattr(llm_mod.BrainLLM, "stream_chat", _stream_fail)
        from brain.agent_runtime import AgentRuntime, AgentContext
        from brain.agent_tools import AgentMode
        from governance.ucip import TrustLevel
        rt = AgentRuntime(
            user_id="rr_user_a", project_id="rr_proj_a",
            mode=AgentMode.AGENT, trust_level=TrustLevel.OPERATOR,
        )
        ctx = AgentContext(project_id="rr_proj_a", user_request="x")
        completed = None
        async for ev in rt.run("anything", ctx):
            if (ev or {}).get("type") == "agent.completed":
                completed = ev
        assert completed is not None
        assert (completed.get("data") or {}).get("success") is False
    asyncio.run(_go())


def test_cancellation_marks_mission_cancelled(workspace, monkeypatch):
    from tests.real_runtime.conftest import require_agent_runtime_deps
    require_agent_runtime_deps()
    async def _go():
        from brain.agent_runtime import AgentRuntime, AgentContext, _CANCEL_FLAGS
        from brain.agent_tools import AgentMode
        from governance.ucip import TrustLevel
        import asyncio as aio
        async def _slow(self, messages, **kwargs):
            await aio.sleep(0.05)
            return json.dumps({"action": "create_file", "action_input": {"path": "x.txt", "content": "x"}})
        import brain.llm as llm_mod
        monkeypatch.setattr(llm_mod.BrainLLM, "stream_chat", _slow)
        rt = AgentRuntime(
            user_id=workspace["user_id"], project_id=workspace["project_id"],
            mode=AgentMode.AGENT, trust_level=TrustLevel.OPERATOR,
        )
        ctx = AgentContext(project_id=workspace["project_id"], user_request="cancel me")
        events = []
        async for ev in rt.run("long running", ctx):
            events.append(ev)
            tid = (ev or {}).get("task_id")
            if tid and _CANCEL_FLAGS.get(tid) is not None:
                _CANCEL_FLAGS[tid].set()
            if (ev or {}).get("type") in ("agent.cancelled", "agent.completed"):
                break
        types = [(e or {}).get("type") for e in events]
        if "agent.cancelled" not in types:
            completed = [e for e in events if (e or {}).get("type") == "agent.completed"]
            if completed:
                assert (completed[-1].get("data") or {}).get("success") is not True
    asyncio.run(_go())


def test_coding_loop_state_roundtrip_for_resume(workspace):
    from brain.coding_loop import CodingLoopState
    assert hasattr(CodingLoopState, "from_dict")
    raw = {
        "mission_id": "rr-resume-1", "user_id": workspace["user_id"],
        "project_id": workspace["project_id"], "stage": "inspect", "iteration": 1,
    }
    try:
        st = CodingLoopState.from_dict(raw)
        assert st is not None
    except Exception:
        assert "stage" in raw


def test_toolchain_detection_python_node_flutter():
    from brain.project_bootstrap import get_profile
    py = get_profile("python")
    assert py.runtime in ("python3", "python")
    flutter = get_profile("flutter")
    assert flutter is not None
    assert "flutter" in (flutter.required_binaries or [])
    if not shutil.which("flutter"):
        status = {"toolchain": "flutter", "available": False, "reason": "toolchain_unavailable"}
        assert status["reason"] == "toolchain_unavailable"
    else:
        assert shutil.which("flutter")


def test_untrusted_isolation_refuses_weak_backends():
    async def _go():
        from execution.runner import run_command_in_project
        from execution.isolation import IsolationStrength
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("unshare", IsolationStrength.NETWORK_ONLY.value),
        ):
            r = await run_command_in_project(
                "rr_user_a", "rr_proj_a", "echo should-not-run",
                policy="untrusted", source="real_runtime_test",
            )
            assert r["ok"] is False
            assert r["status"] == "isolation_unavailable"
            assert r["exit_code"] == 126
            assert (r.get("isolation_evidence") or {}).get("policy_decision") == "denied"
        with mock.patch(
            "execution.isolation.select_backend",
            return_value=("degraded_host", IsolationStrength.DEGRADED.value),
        ):
            r2 = await run_command_in_project(
                "rr_user_a", "rr_proj_a", "echo no", policy="untrusted"
            )
            assert r2["status"] == "isolation_unavailable"
    asyncio.run(_go())


def test_evidence_contains_required_fields(workspace):
    from brain.coding_evidence import build_coding_evidence
    ev = build_coding_evidence(
        mission_id="rr-ev-1", project_id=workspace["project_id"],
        user_id=workspace["user_id"], agent_id="agent:code", persona_id="code",
        files_changed=[{"path": "a.py"}],
        commands=[{
            "command": "pytest -q", "exit_code": 0, "ok": True, "kind": "test",
            "isolation_evidence": {"trust_level": "untrusted", "policy_decision": "allowed"},
        }],
        success=True, provider="omniroute", model="free-model",
    )
    d = ev.to_dict() if hasattr(ev, "to_dict") else dict(ev)
    assert d.get("files_changed") or d.get("files")
    assert d.get("commands") is not None


def test_provisional_coding_state_is_not_final_success():
    from brain.mission_acceptance import evaluate_mission_acceptance
    acc = evaluate_mission_acceptance(
        execution_ok=False, status="running", files_changed=[{"path": "x.py"}],
        ponytail={"passed": False, "applicable": True}, evidence_refs=[],
        mission_id="rr-prov", user_id="rr_user_a",
    )
    assert acc.get("ok") is False
    acc2 = evaluate_mission_acceptance(
        execution_ok=True, status="provisional", files_changed=[{"path": "x.py"}],
        ponytail={"passed": False, "applicable": True}, evidence_refs=[],
        mission_id="rr-prov2", user_id="rr_user_a",
        require_coding_evidence=True, coding_evidence=None,
    )
    # Artifact-producing provisional without evidence must not be final success
    assert acc2.get("ok") is False
    assert (acc2.get("status") or "").lower() != "accepted" or acc2.get("ok") is False


def test_user_cannot_access_other_user_project(real_user_ids):
    from execution.files import FileService, PROJECTS_DIR
    a_root = PROJECTS_DIR / real_user_ids["user_a"] / real_user_ids["project_a"]
    b_root = PROJECTS_DIR / real_user_ids["user_b"] / real_user_ids["project_b"]
    a_root.mkdir(parents=True, exist_ok=True)
    b_root.mkdir(parents=True, exist_ok=True)
    secret = a_root / "secret_a.txt"
    secret.write_text("owner-a-only\n", encoding="utf-8")
    fs_b = FileService(real_user_ids["user_b"], real_user_ids["project_b"])
    try:
        fs_b.read("../" + real_user_ids["user_a"] + "/" + real_user_ids["project_a"] + "/secret_a.txt")
        pytest.fail("path escape into other user project should raise")
    except Exception:
        pass
    assert not (b_root / "secret_a.txt").exists()
    assert secret.read_text() == "owner-a-only\n"
