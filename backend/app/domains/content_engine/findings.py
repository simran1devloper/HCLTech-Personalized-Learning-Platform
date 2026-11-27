"""Structured validation findings shared by every validator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

Severity = Literal["critical", "error", "warning", "info"]
_ORDER = {"critical": 3, "error": 2, "warning": 1, "info": 0}


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str
    block_id: str | None = None
    location: str | None = None   # e.g. "position 12" or "line 3"

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message,
                "block_id": self.block_id, "location": self.location}


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)   # checks that could only run in degraded mode (e.g. no Node)

    def extend(self, items: Iterable[Finding]) -> None:
        self.findings.extend(items)

    def of(self, *severities: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity in severities]

    @property
    def critical(self) -> list[Finding]:
        return self.of("critical")

    @property
    def ok(self) -> bool:
        """No critical findings and no errors (warnings and info never fail a block)."""
        return not self.of("critical", "error")

    @property
    def worst(self) -> Severity | None:
        return max((f.severity for f in self.findings), key=lambda s: _ORDER[s], default=None)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "partial_validation": self.partial, "findings": [f.as_dict() for f in self.findings]}
