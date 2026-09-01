"""Standardized drill result capture."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class DrillResult:
    name: str
    expected: str
    actual: str
    passed: bool
    request_id: str = ""
    execution_job_id: str = ""
    db_state: str = ""
    job_status: str = ""
    evidence_state: str = ""
    side_effect_outcome: str = ""
    recovery_time_ms: float = 0.0
    notes: str = ""
    category: str = ""
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DrillReport:
    profile: str = "pure-logic"
    results: list[DrillResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    def add(self, r: DrillResult) -> None:
        self.results.append(r)

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "passed": self.passed,
            "failed": self.failed,
            "total": len(self.results),
            "gate": "PASS" if self.failed == 0 else "FAIL",
            "results": [r.to_dict() for r in self.results],
            "at": datetime.now(timezone.utc).isoformat(),
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
