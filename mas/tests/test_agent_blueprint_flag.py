"""Agent Blueprint Studio S1 — feature-flag gating (no database)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import agent_blueprint_studio as studio  # noqa: E402

ENV = "MAS_AGENT_BLUEPRINT_STUDIO_ENABLED"


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert config.agent_blueprint_studio_enabled() is False
    assert studio.is_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "On"])
def test_enabled_for_truthy_values(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert config.agent_blueprint_studio_enabled() is True
    assert studio.is_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "nope"])
def test_disabled_for_falsy_values(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert config.agent_blueprint_studio_enabled() is False


def test_studio_flag_is_independent_of_other_verticals(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setenv("MAS_AUTOMATION_ROI_ENABLED", "true")
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")
    # Enabling other verticals must not enable Studio.
    assert config.agent_blueprint_studio_enabled() is False
