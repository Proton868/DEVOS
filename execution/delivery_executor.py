"""Execute delivery DAG nodes via existing Application Runtime + adapters (no second scheduler)."""
from __future__ import annotations

import os
import time
from typing import Any, Optional

from execution.delivery_dag import build_delivery_dag
from execution.app_runtime import ApplicationRuntime, AppRuntimeSpec, get_runtime
from execution.app_detect import detect_application
from execution.files import FileService
from execution.deploy import get_adapter
from execution.durable_store import save_deployment, new_id
from execution.log_stream import publish_log
from execution.saga import (
    create_saga, begin_step, complete_step, fail_step, complete_saga, fail_saga, compensate_saga,
)
from observability.tracing import start_span, new_trace, set_current_trace, get_current_trace


async def execute_delivery_plan(
    *,
    user_id: str,
    project_id: str,
    goal: str = "preview",
    provider: Optional[str] = None,
    cancel_check=None,
    plan_id: Optional[str] = None,
) -> dict[str, Any]:
    plan = build_delivery_dag(goal)
    evidence: list[dict] = []
    status = "running"
    rt = get_runtime(user_id, project_id) or ApplicationRuntime(
        AppRuntimeSpec(user_id=user_id, project_id=project_id)
    )
    log_id = getattr(rt, "runtime_id", None) or f"delivery:{project_id}"
    if plan_id:
        from execution.cancel_cascade import bind_delivery, is_delivery_cancelled
        bind_delivery(
            plan_id, user_id=user_id, project_id=project_id,
            runtime_id=getattr(rt, "runtime_id", None),
        )
        if cancel_check is None:
            cancel_check = lambda: is_delivery_cancelled(plan_id)

    trace = new_trace()
    set_current_trace(trace)
    saga = create_saga(plan_id=plan_id, mission_id=plan_id, trace_id=trace.trace_id)

    with start_span("delivery.execute", kind="mission", attributes={
        "project_id": project_id, "goal": goal, "saga_id": saga.saga_id,
    }):
        for node in plan["nodes"]:
            if cancel_check and cancel_check():
                status = "cancelled"
                evidence.append({"node": node["id"], "status": "cancelled"})
                # automatic compensations only
                comp = await compensate_saga(saga, user_id=user_id, project_id=project_id)
                evidence.append({"compensation": comp})
                break
            ntype = node["type"]
            publish_log(log_id, "system", f"node start {ntype}")
            started = time.time()
            with start_span(f"dag.node.{ntype}", kind="dag.node", attributes={
                "node_id": node["id"], "saga_id": saga.saga_id, "plan_id": plan_id or "",
            }):
                step = begin_step(
                    saga, node_id=node["id"], action=ntype,
                    trace_id=trace.trace_id,
                    span_id=get_current_trace().span_id if get_current_trace() else None,
                )
                try:
                    if ntype == "inspect":
                        det = detect_application(FileService(user_id, project_id))
                        evidence.append({"node": "inspect", "status": "completed", "detection": det})
                        complete_step(step, evidence_id=f"ev_{step.step_id}")
                    elif ntype in ("build", "install"):
                        st = await rt.install()
                        evidence.append({"node": "install", "status": st.state.value, "detail": st.detail})
                        if st.state.value == "FAILED":
                            fail_step(step, st.detail or "install failed")
                            status = "failed"
                            fail_saga(saga, "install failed")
                            break
                        st = await rt.build()
                        evidence.append({"node": "build", "status": st.state.value, "detail": st.detail})
                        if st.state.value == "FAILED":
                            fail_step(step, st.detail or "build failed")
                            status = "failed"
                            fail_saga(saga, "build failed")
                            break
                        complete_step(step, evidence_id=f"ev_{step.step_id}")
                    elif ntype == "verify":
                        det = detect_application(FileService(user_id, project_id))
                        evidence.append({"node": "verify", "status": "completed", "detection": det})
                        complete_step(step)
                    elif ntype == "preview":
                        st = await rt.start(port=3911)
                        evidence.append({"node": "preview", "status": st.state.value, "port": st.port})
                        if st.state.value == "FAILED":
                            fail_step(step, st.detail or "preview failed")
                            status = "failed"
                            fail_saga(saga, "preview failed")
                            break
                        complete_step(step, meta={"runtime_id": getattr(rt, "runtime_id", None), "port": st.port})
                    elif ntype in ("git_commit", "github_push", "github_pr"):
                        evidence.append({
                            "node": ntype, "status": "blocked",
                            "reason": "EXTERNAL_SIDE_EFFECT requires GITHUB_TOKEN + UCIP approval",
                        })
                        complete_step(step, meta={"blocked": True})
                    elif ntype == "deploy":
                        prov = provider or (
                            "vercel" if "vercel" in goal.lower() else
                            "netlify" if "netlify" in goal.lower() else "vercel"
                        )
                        try:
                            adapter = get_adapter(prov)
                        except KeyError:
                            fail_step(step, "unknown provider")
                            status = "failed"
                            fail_saga(saga, "unknown provider")
                            break
                        fs = FileService(user_id, project_id)
                        creds = {}
                        if prov == "vercel":
                            creds["VERCEL_TOKEN"] = os.environ.get("VERCEL_TOKEN")
                        elif prov == "netlify":
                            creds["NETLIFY_TOKEN"] = os.environ.get("NETLIFY_TOKEN")
                            creds["NETLIFY_SITE_ID"] = os.environ.get("NETLIFY_SITE_ID")
                        with start_span(f"deployment.{prov}", kind="deployment", attributes={"provider": prov}):
                            result = await adapter.deploy(
                                project_path=project_id,
                                meta={"workspace_root": str(fs.root), "user_id": user_id},
                                credentials=creds,
                            )
                        dep_id = new_id("dep_")
                        save_deployment({
                            "deployment_id": dep_id, "user_id": user_id, "project_id": project_id,
                            "provider": prov, "status": result.status.value,
                            "provider_deployment_id": result.deployment_id, "url": result.url,
                            "error": result.error, "evidence": result.evidence,
                            "requested_at": started, "completed_at": time.time(),
                        })
                        evidence.append({
                            "node": "deploy", "status": result.status.value,
                            "result": result.to_dict(), "deployment_id": dep_id,
                        })
                        if result.error == "DEPLOYMENT_AUTH_REQUIRED":
                            complete_step(step, meta={"ask_user": True, "deployment_id": dep_id})
                            status = "ask_user"
                            break
                        if result.status.value == "FAILED":
                            fail_step(step, result.error or "deploy failed")
                            status = "failed"
                            fail_saga(saga, result.error or "deploy failed")
                            break
                        complete_step(step, meta={"deployment_id": dep_id, "url": result.url})
                    elif ntype == "deploy_verify":
                        evidence.append({"node": "deploy_verify", "status": "completed"})
                        complete_step(step)
                    elif ntype == "publish":
                        evidence.append({"node": "publish", "status": "blocked", "reason": "explicit share required"})
                        complete_step(step, meta={"blocked": True})
                    else:
                        evidence.append({"node": ntype, "status": "skipped"})
                        complete_step(step)
                    publish_log(log_id, "system", f"node done {ntype} in {time.time()-started:.2f}s")
                except Exception as e:
                    fail_step(step, str(e))
                    evidence.append({"node": ntype, "status": "failed", "error": str(e)[:300]})
                    status = "failed"
                    fail_saga(saga, str(e)[:300])
                    break
        else:
            if status == "running":
                status = "completed"
                complete_saga(saga)

    return {
        "goal": goal,
        "status": status,
        "evidence": evidence,
        "plan": plan,
        "saga_id": saga.saga_id,
        "trace_id": trace.trace_id,
        "saga": saga.to_dict(),
    }
