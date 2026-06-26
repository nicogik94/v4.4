"""Feature-flag tests for R1.1 research-evidence sidecar writes."""
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from research_evidence import is_enabled  # noqa: E402
from research_evidence.models import SourceMetadataRevisionCreate  # noqa: E402
from research_evidence.service import (  # noqa: E402
    ResearchEvidenceDisabled,
    create_source_metadata_revision,
)


ENV = "MAS_RESEARCH_EVIDENCE_ENABLED"


class TripwireConn:
    def execute(self, *args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("disabled service write must not touch the connection")


def test_research_evidence_flag_defaults_false(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert config.research_evidence_enabled() is False
    assert is_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "On"])
def test_research_evidence_flag_truthy_values(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert config.research_evidence_enabled() is True
    assert is_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "nope"])
def test_research_evidence_flag_falsy_values(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert config.research_evidence_enabled() is False


def test_service_writes_fail_closed_when_disabled(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    with pytest.raises(ResearchEvidenceDisabled):
        create_source_metadata_revision(
            TripwireConn(),
            SourceMetadataRevisionCreate(
                project_id="00000000-0000-0000-0000-000000000001",
                source_snapshot_id="00000000-0000-0000-0000-000000000002",
            ),
        )
