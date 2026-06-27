"""Agent Blueprint Studio — S1 (Draft-only Foundation) package.

Additive, feature-gated MAS vertical for operator-authored agent blueprints. This
package is draft-only: it makes no released / validated / high-quality /
runtime-tested claim, deploys or tests no external agent, performs no external
processing or content egress, and never touches the Decision Engine
(ProjectState / store.py / state_snapshots).

Wave 1 ships the foundation only: the feature flag (in ``config``), the additive
``sql/v50_*`` schema, the data models, and a sticky-ephemeral, insert-only
repository. Routes, compiler, linter, evaluation, exports, and UI are later waves.
"""
from __future__ import annotations

import config

from .repository import StudioDisabled

S1_SCHEMA_MIGRATION = "v50_agent_blueprint_studio_foundation.sql"


def is_enabled() -> bool:
    """True when the Agent Blueprint Studio feature flag is on (off by default)."""
    return config.agent_blueprint_studio_enabled()


__all__ = ["is_enabled", "S1_SCHEMA_MIGRATION", "StudioDisabled"]
