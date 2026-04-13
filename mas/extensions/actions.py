"""Safe action-layer scaffolding for future reversible execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ActionRequest:
    action_type: str
    scope: str
    payload: dict[str, str] = field(default_factory=dict)
    requested_by: str = "operator"
    dry_run: bool = True


@dataclass
class DryRunResult:
    allowed: bool
    requires_approval: bool
    reversible: bool
    summary: str = ""
    audit_fields: dict[str, str] = field(default_factory=dict)


@dataclass
class ActionExecutionResult:
    status: str
    action_id: str
    summary: str = ""
    audit_fields: dict[str, str] = field(default_factory=dict)


class ActionHandler(Protocol):
    action_type: str

    def dry_run(self, request: ActionRequest) -> DryRunResult:
        ...

    def execute(self, request: ActionRequest) -> ActionExecutionResult:
        ...


class ActionRegistry:
    def __init__(self):
        self._handlers: dict[str, ActionHandler] = {}

    def register(self, handler: ActionHandler) -> None:
        self._handlers[handler.action_type] = handler

    def get(self, action_type: str) -> ActionHandler | None:
        return self._handlers.get(action_type)
