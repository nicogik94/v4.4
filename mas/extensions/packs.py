"""Vertical-pack scaffolding for prompt/template/view extensions."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PackDefinition:
    pack_id: str
    label: str
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    templates: dict[str, str] = field(default_factory=dict)
    extra_fields: dict[str, str] = field(default_factory=dict)
    scoring_weights: dict[str, float] = field(default_factory=dict)
    view_hints: dict[str, str] = field(default_factory=dict)


class PackRegistry:
    def __init__(self):
        self._packs: dict[str, PackDefinition] = {}

    def register(self, pack: PackDefinition) -> None:
        self._packs[pack.pack_id] = pack

    def get(self, pack_id: str) -> PackDefinition | None:
        return self._packs.get(pack_id)

    def list_all(self) -> list[PackDefinition]:
        return [self._packs[key] for key in sorted(self._packs)]
