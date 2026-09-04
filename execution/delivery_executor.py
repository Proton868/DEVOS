"""Execute delivery DAG nodes via existing Application Runtime + adapters (no second scheduler)."""
from __future__ import annotations

import time
from typing import Any, Optional

from execution.delivery_dag import build_delivery_dag
from execution.app_runtime import ApplicationRuntime, AppRuntimeSpec, get_runtime
from execution.app_detect import detect_application
from execution.files import FileService
from execution.deploy import get_adapter
from execution.durable_store import save_deployment, new_id
from execution.log_stream import publish_log
import os


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
    evidence = []
    status = "running"
    rt = get_runtime(user_id, project_id) or ApplicationRuntime(
        AppRuntimeSpec(user_id=user_id, project_id=project_id)
    )
    log_id = rt.runtime_id if hasattr(rt, "runtime_id") else f"delivery:{project_id}"
    if plan_id:
        from execution.cancel_cascade import bind_delivery, is_delivery_cancelled
        bind_delivery(plan_id, user_id=user_id, project_id=project_id,
                      runtime_id=getattr(rt, "runtime_id", None))
        if cancel_check is None:
            cancel_check = lambda: is_delivery_cancelled(plan_id)

    for node in plan["nodes"]:
        if cancel_check and cancel_check():
            status = "cancelled"
            evidence.append({"node": node["id"], "status": "cancelled"})
            break
        ntype = node["type"]
        publish_log(log_id, "system", f"node start {ntype}")
        started = time.time()
        try:
            if ntype == "inspect":
                det = detect_application(FileService(user_id, project_id))
                evidence.append({"node": "inspect", "status": "completed", "detection": det})
            elif ntype in ("build", "install") or ntype == "build":
                if ntype == "install" or True:
                    st = await rt.install()
                    evidence.append({"node": "install", "status": st.state.value, "detail": st.detail})
                    if st.state.value == "FAILED":
                        status = "failed"
                        break
                st = await rt.build()
                evidence.append({"node": "build", "status": st.state.value, "detail": st.detail})
                if st.state.value == "FAILED":
                    status = "failed"
                    break
            elif ntype == "verify":
                # lightweight: detection + files present
                det = detect_application(FileService(user_id, project_id))
                evidence.append({"node": "verify", "status": "completed", "detection": det})
            elif ntype == "preview":
                st = await rt.start(port=3911)
                evidence.append({"node": "preview", "status": st.state.value, "port": st.port, "detail": st.detail})
                if st.state.value == "FAILED":
                    status = "failed"
                    break
            elif ntype in ("git_commit", "github_push", "github_pr"):
                evidence.append({
                    "node": ntype,
                    "status": "blocked",
                    "reason": "EXTERNAL_SIDE_EFFECT requires GITHUB_TOKEN + UCIP approval",
                })
            elif ntype == "deploy":
                prov = provider or ("vercel" if "vercel" in goal.lower() else "netlify" if "netlify" in goal.lower() else "vercel")
                try:
                    adapter = get_adapter(prov)
                except KeyError:
                    evidence.append({"node": "deploy", "status": "failed", "error": "unknown provider"})
                    status = "failed"
                    break
                fs = FileService(user_id, project_id)
                creds = {}
                if prov == "vercel":
                    creds["VERCEL_TOKEN"] = os.environ.get("VERCEL_TOKEN")
                elif prov == "netlify":
                    creds["NETLIFY_TOKEN"] = os.environ.get("NETLIFY_TOKEN")
                    creds["NETLIFY_SITE_ID"] = os.environ.get("NETLIFY_SITE_ID")
                result = await adapter.deploy(
                    project_path=project_id,
                    meta={"workspace_root": str(fs.root), "user_id": user_id},
                    credentials=creds,
                )
                dep_id = new_id("dep_")
                save_deployment({
                    "deployment_id": dep_id,
                    "user_id": user_id,
                    "project_id": project_id,
                    "provider": prov,
                    "status": result.status.value,
                    "provider_deployment_id": result.deployment_id,
                    "url": result.url,
                    "error": result.error,
                    "evidence": result.evidence,
                    "requested_at": started,
                    "completed_at": time.time(),
                })
                evidence.append({"node": "deploy", "status": result.status.value, "result": result.to_dict(), "deployment_id": dep_id})
                if result.error == "DEPLOYMENT_AUTH_REQUIRED":
                    status = "ask_user"
                    break
                if result.status.value == "FAILED":
                    status = "failed"
                    break
            elif ntype == "deploy_verify":
                evidence.append({"node": "deploy_verify", "status": "completed", "note": "see deploy evidence"})
            elif ntype == "publish":
                evidence.append({"node": "publish", "status": "blocked", "reason": "explicit share/publish required"})
            else:
                evidence.append({"node": ntype, "status": "skipped"})
            publish_log(log_id, "system", f"node done {ntype} in {time.time()-started:.2f}s")
        except Exception as e:
            evidence.append({"node": ntype, "status": "failed", "error": str(e)[:300]})
            status = "failed"
            break
    else:
        if status == "running":
            status = "completed"

    return {"goal": goal, "status": status, "evidence": evidence, "plan": plan}
