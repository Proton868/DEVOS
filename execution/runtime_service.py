"""
Project Runtime Service — lifecycle facade for Preview / IDE / Flow / Nuha.

Wraps existing ApplicationRuntime + detection + deploy probes without creating
a second process manager. Surfaces a structured snapshot that can represent
full-stack applications while the Preview UI stays lightweight.
"""
from __future__ import annotations

import logging
import time
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from execution.app_detect import detect_application
from execution.app_runtime import (
    ApplicationRuntime,
    AppRuntimeSpec,
    AppRuntimeState,
    AppRuntimeStatus,
    get_runtime,
)
from execution.files import FileService

logger = logging.getLogger("devos.runtime_service")


class ComponentKind(str, Enum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    WORKER = "worker"
    DATABASE = "database"
    STORAGE = "storage"
    QUEUE = "queue"
    SCHEDULER = "scheduler"
    BUILD = "build"
    OTHER = "other"


@dataclass
class RuntimeComponent:
    kind: ComponentKind
    name: str
    status: str = "unknown"
    detail: str = ""
    port: Optional[int] = None
    managed: bool = False  # True when DevOS process-manages this component

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value if isinstance(self.kind, ComponentKind) else self.kind,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "port": self.port,
            "managed": self.managed,
        }


@dataclass
class ProjectRuntimeSnapshot:
    project_id: str
    user_id: str
    state: str
    detail: str = ""
    port: Optional[int] = None
    pid: Optional[int] = None
    health: str = "unknown"  # healthy | unhealthy | unknown | n/a
    detection: dict = field(default_factory=dict)
    components: list = field(default_factory=list)
    env_keys: list = field(default_factory=list)  # names only, never values
    logs_tail: str = ""
    evidence: dict = field(default_factory=dict)
    capabilities: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "user_id": self.user_id,
            "state": self.state,
            "detail": self.detail,
            "port": self.port,
            "pid": self.pid,
            "health": self.health,
            "detection": self.detection,
            "components": [
                c.to_dict() if hasattr(c, "to_dict") else c for c in (self.components or [])
            ],
            "env_keys": list(self.env_keys or []),
            "logs_tail": self.logs_tail,
            "evidence": dict(self.evidence or {}),
            "capabilities": list(self.capabilities or []),
        }


LIFECYCLE_ACTIONS = frozenset({
    "status", "install", "build", "start", "stop", "restart", "rebuild", "health", "test",
})


def _components_from_detection(detection: dict, status: Optional[AppRuntimeStatus]) -> list[RuntimeComponent]:
    kind = (detection or {}).get("kind") or "UNKNOWN_APP"
    comps: list[RuntimeComponent] = []
    st = status.state.value if status and status.state else "STOPPED"
    port = status.port if status else None
    detail = status.detail if status else ""

    if kind in ("NEXTJS_APP", "VITE_APP", "REACT_APP", "NODE_APP", "STATIC_SITE"):
        comps.append(
            RuntimeComponent(
                kind=ComponentKind.FRONTEND,
                name=kind,
                status=st,
                detail=detail,
                port=port,
                managed=True,
            )
        )
    if kind in ("PYTHON_APP", "FASTAPI_APP", "FLASK_APP"):
        comps.append(
            RuntimeComponent(
                kind=ComponentKind.BACKEND,
                name=kind,
                status=st,
                detail=detail,
                port=port,
                managed=True,
            )
        )
    if not comps:
        comps.append(
            RuntimeComponent(
                kind=ComponentKind.OTHER,
                name=kind,
                status=st,
                detail=detail or "undetected or unsupported",
                port=port,
                managed=st not in ("UNSUPPORTED", "UNKNOWN", "STOPPED"),
            )
        )

    # Declared but not process-managed placeholders (honest about limits)
    scripts = (detection or {}).get("scripts") or {}
    if "worker" in scripts or "workers" in scripts:
        comps.append(
            RuntimeComponent(
                kind=ComponentKind.WORKER,
                name="worker",
                status="declared",
                detail="project declares worker script; not auto-managed",
                managed=False,
            )
        )
    return comps


def _scan_env_key_names(fs: FileService) -> list[str]:
    """Return env *names* from .env.example if present — never values."""
    keys: list[str] = []
    for name in (".env.example", ".env.sample", "env.example"):
        try:
            data = fs.read(name)
            raw = data.get("content") if isinstance(data, dict) else str(data or "")
        except Exception:
            continue
        for line in (raw or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k = line.split("=", 1)[0].strip()
            if k and k not in keys and not k.startswith("."):
                keys.append(k)
        if keys:
            break
    return keys[:64]


def probe_health(port: Optional[int], timeout: float = 2.0) -> str:
    if not port:
        return "n/a"
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/", timeout=timeout)
        return "healthy"
    except Exception:
        return "unhealthy"


def get_or_create_runtime(user_id: str, project_id: str) -> ApplicationRuntime:
    rt = get_runtime(user_id, project_id)
    if rt is not None:
        return rt
    return ApplicationRuntime(AppRuntimeSpec(user_id=user_id, project_id=project_id))


def snapshot(user_id: str, project_id: str, *, probe: bool = True) -> ProjectRuntimeSnapshot:
    fs = FileService(user_id, project_id)
    detection = detect_application(fs)
    rt = get_runtime(user_id, project_id)
    status = rt.status if rt else AppRuntimeStatus(state=AppRuntimeState.STOPPED, detail="no active runtime")
    health = "unknown"
    if probe and status.port and status.state == AppRuntimeState.READY:
        health = probe_health(status.port)
    elif status.state in (AppRuntimeState.STOPPED, AppRuntimeState.UNSUPPORTED):
        health = "n/a"
    elif status.state == AppRuntimeState.FAILED:
        health = "unhealthy"

    env_keys = _scan_env_key_names(fs)
    comps = _components_from_detection(detection, status if rt else None)

    caps = [
        "runtime.status",
        "runtime.start",
        "runtime.stop",
        "runtime.restart",
        "runtime.rebuild",
        "runtime.health",
        "runtime.logs",
        "preview.iframe",
    ]
    if detection.get("kind") not in (None, "UNKNOWN_APP", "STATIC_SITE"):
        caps.extend(["runtime.install", "runtime.build", "runtime.test"])

    return ProjectRuntimeSnapshot(
        project_id=project_id,
        user_id=user_id,
        state=status.state.value if status.state else "UNKNOWN",
        detail=status.detail or "",
        port=status.port,
        pid=status.pid,
        health=health,
        detection=detection,
        components=comps,
        env_keys=env_keys,
        logs_tail=(status.logs_tail or "")[-4000:],
        evidence=dict(status.evidence or {}),
        capabilities=caps,
    )


async def run_lifecycle_action(
    user_id: str,
    project_id: str,
    action: str,
    *,
    port: int = 3911,
) -> ProjectRuntimeSnapshot:
    """Execute a governed lifecycle action and return a fresh snapshot."""
    act = (action or "").strip().lower()
    if act not in LIFECYCLE_ACTIONS:
        raise ValueError(f"INVALID_ACTION:{act}")

    if act == "status":
        return snapshot(user_id, project_id, probe=True)
    if act == "health":
        snap = snapshot(user_id, project_id, probe=True)
        return snap

    rt = get_or_create_runtime(user_id, project_id)

    if act == "install":
        await rt.install()
    elif act == "build":
        await rt.build()
    elif act == "start":
        await rt.start(port=port)
    elif act == "stop":
        await rt.stop()
    elif act == "restart":
        await rt.restart()
    elif act == "rebuild":
        st = await rt.install()
        if st.state != AppRuntimeState.FAILED:
            st = await rt.build()
        if st.state not in (AppRuntimeState.FAILED, AppRuntimeState.UNSUPPORTED):
            # Prefer built artifacts; start is separate explicit step for safety
            pass
    elif act == "test":
        # Best-effort: run package test script when detected
        info = rt._detect_and_plan()
        scripts = (info or {}).get("scripts") or {}
        if "test" not in scripts:
            rt.status = AppRuntimeStatus(
                state=AppRuntimeState.FAILED,
                detail="no test script detected",
                evidence={"detection": info},
            )
        else:
            pm = (info or {}).get("package_manager") or "npm"
            from execution.app_runtime import _pm_commands
            code, out, err = await rt._run_cmd(
                _pm_commands(pm, "test"),
                timeout=300,
                allow_network=False,
            )
            rt.status = AppRuntimeStatus(
                state=AppRuntimeState.READY if code == 0 else AppRuntimeState.FAILED,
                detail="tests passed" if code == 0 else "tests failed",
                logs_tail=(out or err or "")[-4000:],
                evidence={"exit_code": code},
            )

    return snapshot(user_id, project_id, probe=True)
