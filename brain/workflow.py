"""
Workflow Engine — Stage 6 (Agency OS Master Plan).

Visual + YAML + JSON + natural-language workflow authoring, all compiling to
the same UCIP ExecutionPlan format. A workflow is a directed graph of steps
where each step is a capability invocation with inputs, outputs, and
conditional branching.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import yaml

logger = logging.getLogger("devos.workflow")


class StepType(str, Enum):
    CAPABILITY = "capability"   # Execute a UCIP capability
    CONDITION  = "condition"    # Branch based on output
    PARALLEL   = "parallel"     # Run multiple steps concurrently
    WAIT       = "wait"         # Pause for time or event
    APPROVAL   = "approval"     # Human approval gate
    SUBFLOW    = "subflow"      # Nested workflow
    NOTIFY     = "notify"       # Send notification


@dataclass
class WorkflowStep:
    """One step in a workflow."""
    id:          str
    type:        StepType = StepType.CAPABILITY
    name:        str = ""
    description: str = ""
    capability:  Optional[str] = None   # UCIP capability slug
    inputs:      dict = field(default_factory=dict)
    outputs:     dict = field(default_factory=dict)
    condition:   Optional[str] = None    # expression for CONDITION steps
    branches:    dict[str, str] = field(default_factory=dict)  # condition_value → next_step_id
    next_step:   Optional[str] = None    # default next step
    on_error:    Optional[str] = None    # step to jump to on error
    timeout_s:   int = 300
    retry:       int = 0
    metadata:    dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "description": self.description,
            "capability": self.capability,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "condition": self.condition,
            "branches": self.branches,
            "next_step": self.next_step,
            "on_error": self.on_error,
            "timeout_s": self.timeout_s,
            "retry": self.retry,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowStep":
        return cls(
            id=data["id"],
            type=StepType(data.get("type", "capability")),
            name=data.get("name", ""),
            description=data.get("description", ""),
            capability=data.get("capability"),
            inputs=data.get("inputs", {}),
            outputs=data.get("outputs", {}),
            condition=data.get("condition"),
            branches=data.get("branches", {}),
            next_step=data.get("next_step"),
            on_error=data.get("on_error"),
            timeout_s=data.get("timeout_s", 300),
            retry=data.get("retry", 0),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Workflow:
    """A complete workflow definition."""
    workflow_id:   str
    name:          str
    description:   str = ""
    version:       str = "1.0.0"
    steps:         list[WorkflowStep] = field(default_factory=list)
    start_step:    Optional[str] = None
    triggers:      list[str] = field(default_factory=list)  # manual, schedule, webhook, event
    schedule:      Optional[str] = None    # cron expression
    tags:          list[str] = field(default_factory=list)
    created_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata:      dict = field(default_factory=dict)
    status:        str = "draft"  # draft | active | paused | archived

    def to_dict(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "steps": [s.to_dict() for s in self.steps],
            "start_step": self.start_step,
            "triggers": self.triggers,
            "schedule": self.schedule,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
            "status": self.status,
        }

    def to_yaml(self) -> str:
        return yaml.dump(self.to_dict(), default_flow_style=False, sort_keys=False)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_ucip_plan(self) -> dict:
        """Compile workflow to a UCIP ExecutionPlan format."""
        return {
            "plan_id": self.workflow_id,
            "plan_type": "workflow",
            "name": self.name,
            "steps": [
                {
                    "step_id": s.id,
                    "action": s.capability or s.type.value,
                    "inputs": s.inputs,
                    "next_step": s.next_step,
                    "on_error": s.on_error,
                    "timeout_s": s.timeout_s,
                    "retry": s.retry,
                }
                for s in self.steps
            ],
            "start_step": self.start_step,
        }

    def validate(self) -> tuple[bool, list[str]]:
        """Validate the workflow structure."""
        errors = []
        step_ids = {s.id for s in self.steps}
        if not self.steps:
            errors.append("Workflow has no steps")
        if self.start_step and self.start_step not in step_ids:
            errors.append(f"Start step '{self.start_step}' not found in steps")
        for step in self.steps:
            if step.next_step and step.next_step not in step_ids:
                errors.append(f"Step '{step.id}': next_step '{step.next_step}' not found")
            if step.on_error and step.on_error not in step_ids:
                errors.append(f"Step '{step.id}': on_error '{step.on_error}' not found")
            for branch_target in step.branches.values():
                if branch_target not in step_ids:
                    errors.append(f"Step '{step.id}': branch target '{branch_target}' not found")
            if step.type == StepType.CAPABILITY and not step.capability:
                errors.append(f"Step '{step.id}': CAPABILITY type requires a capability slug")
        return len(errors) == 0, errors

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "Workflow":
        data = yaml.safe_load(yaml_str)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Workflow":
        workflow = cls(
            workflow_id=data.get("workflow_id", str(uuid.uuid4())),
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            start_step=data.get("start_step"),
            triggers=data.get("triggers", ["manual"]),
            schedule=data.get("schedule"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            status=data.get("status", "draft"),
        )
        workflow.steps = [WorkflowStep.from_dict(s) for s in data.get("steps", [])]
        return workflow


class WorkflowEngine:
    """In-memory workflow registry. For production, replace with Supabase-backed
    storage. The interface stays the same — store/load/delete/list."""

    def __init__(self):
        self._workflows: dict[str, Workflow] = {}

    def store(self, workflow: Workflow) -> Workflow:
        workflow.updated_at = datetime.now(timezone.utc)
        self._workflows[workflow.workflow_id] = workflow
        return workflow

    def load(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    def delete(self, workflow_id: str) -> bool:
        if workflow_id in self._workflows:
            del self._workflows[workflow_id]
            return True
        return False

    def list_all(self, status: Optional[str] = None,
                 tags: Optional[list[str]] = None) -> list[Workflow]:
        workflows = list(self._workflows.values())
        if status:
            workflows = [w for w in workflows if w.status == status]
        if tags:
            workflows = [w for w in workflows
                        if any(t in w.tags for t in tags)]
        return sorted(workflows, key=lambda w: w.updated_at, reverse=True)

    def count(self) -> int:
        return len(self._workflows)


# Module-level singleton
_engine = WorkflowEngine()


def get_workflow_engine() -> WorkflowEngine:
    return _engine


def create_workflow(name: str, description: str = "",
                    steps: Optional[list[dict]] = None,
                    triggers: Optional[list[str]] = None) -> Workflow:
    """Create a new workflow from a dict-based step list."""
    workflow = Workflow(
        workflow_id=str(uuid.uuid4()),
        name=name,
        description=description,
        triggers=triggers or ["manual"],
    )
    if steps:
        for s in steps:
            workflow.steps.append(WorkflowStep.from_dict(s))
    if workflow.steps:
        workflow.start_step = workflow.steps[0].id
    return _engine.store(workflow)