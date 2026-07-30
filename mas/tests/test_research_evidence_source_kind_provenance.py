"""Provenance regression coverage for A-4A ``source_kind`` disclosure (R2.0A-4C).

Audit finding MAJOR-1: a truthful ``source_kind`` (for example
``operator_curated_research_evidence_record``, the category the evidence-only
ingress creates) survived the A-3 ``internal_analysis`` presentation projection
but was dropped by BOTH the A-4A model-facing renderer and the durable
consumption attestation. The model therefore received
``citation_label`` / ``canonical_source_locator`` / ``publisher`` / ``author`` /
``published_at`` / ``retrieved_at`` / ``declared_quality_tier`` /
``declared_quality_rationale`` with no mechanical provenance distinction, and
the persisted source identities carried none either.

These tests pin the fix at both boundaries and pin what must NOT change: the
frozen A-3 disclosure policy, the frozen 65536-byte budget, and fail-closed
overflow with no truncation.
"""
import asyncio
import os
import sys
import types
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_evidence_context as rc  # noqa: E402
from research_evidence import presentation_projection_policy as policy  # noqa: E402
from research_evidence.pack_models import UsageScope  # noqa: E402
from research_evidence.presentation_projection_models import (  # noqa: E402
    ResearchEvidencePresentationSource,
)
from research_evidence.presentation_projection_service import (  # noqa: E402
    project_research_evidence_pack,
)
from tests.test_research_evidence_presentation_models import (  # noqa: E402
    build_pack,
    build_two_member_pack,
    rebuild,
    remodel,
)

# The category the evidence-only ingress (R2.0A-4C) creates, and the category it
# refuses to claim because it never fetches anything. Read from the capture
# service so this file cannot drift from the production vocabulary.
from knowledge.evidence_snapshot.source_service import (  # noqa: E402
    OPERATOR_CURATED_RESEARCH_EVIDENCE_RECORD as CURATED,
    RESERVED_SOURCE_KINDS,
)

RAW_WEB_CAPTURE = "raw_web_capture"

SOURCE_KIND_PREFIX = "     source_kind: "


def _internal_projection(source_kind=CURATED):
    """One-source internal_analysis projection carrying ``source_kind``."""
    projection = project_research_evidence_pack(build_pack("internal_analysis"))
    source = remodel(projection.sources[0], source_kind=source_kind)
    return rebuild(projection, sources=(source,))


def _two_source_projection(first_kind, second_kind):
    projection = project_research_evidence_pack(
        build_two_member_pack("internal_analysis")
    )
    sources = (
        remodel(projection.sources[0], source_kind=first_kind),
        remodel(projection.sources[1], source_kind=second_kind),
    )
    return rebuild(projection, sources=sources)


def _unvalidated_kinds(pack, *kinds):
    """Project ``pack``, then override each source's ``source_kind`` unvalidated.

    ``model_construct`` deliberately bypasses A-3's require check so the renderer
    can be exercised on a category it would normally never be handed. The point
    is that the renderer must normalize on its own, not inherit safety from a
    validator it does not run; everything else stays a real projection.
    """
    projection = project_research_evidence_pack(pack)
    values = {
        name: getattr(projection, name) for name in type(projection).model_fields
    }
    values["sources"] = tuple(
        remodel(source, source_kind=kind)
        for source, kind in zip(projection.sources, kinds, strict=True)
    )
    return type(projection).model_construct(**values)


def _rendered_kind_values(block: str) -> list[str]:
    return [
        line[len(SOURCE_KIND_PREFIX):]
        for line in block.splitlines()
        if line.startswith(SOURCE_KIND_PREFIX)
    ]


def _state(project_type="strategic_audit", log=None):
    return types.SimpleNamespace(
        project_id=str(uuid4()),
        project_type=project_type,
        policy_audit_log=[] if log is None else log,
    )


class _FakeCursor:
    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self):
        self.autocommit = True
        self.read_only = False

    def execute(self, *args, **kwargs):
        return _FakeCursor()

    def commit(self):  # pragma: no cover - the consumer must never call it
        raise AssertionError("the consumer must never commit")

    def rollback(self):
        pass

    def close(self):
        pass


def _consume(monkeypatch, projection, phase="audit"):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setattr(
        rc, "project_research_evidence_presentation",
        lambda conn, *, project_id, usage_scope: projection,
    )
    return asyncio.run(
        rc.load_research_evidence_consumption(
            _state(), phase, connect=lambda: _FakeConn(),
        )
    )


# ═══════════════════ (1,2) model-facing block ═══════════════════


def test_curated_record_kind_reaches_the_model_facing_block_verbatim():
    projection = _internal_projection(CURATED)
    assert projection.sources[0].source_kind == CURATED

    block = rc.render_research_evidence_block(projection)

    assert SOURCE_KIND_PREFIX + CURATED in block.splitlines()
    assert _rendered_kind_values(block) == [CURATED]
    # The distinction accompanies the citation identity it qualifies, inside the
    # same SOURCES entry — not somewhere the model could read it as unrelated.
    sources_section = block[block.index("SOURCES:"):block.index("CLAIMS:")]
    lines = sources_section.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("  S1 "))
    assert lines[header + 1] == SOURCE_KIND_PREFIX + CURATED
    for label in (
        "citation_label", "canonical_source_locator", "publisher", "author",
        "published_at", "retrieved_at", "declared_quality_tier",
        "declared_quality_rationale",
    ):
        assert f"     {label}: " in sources_section


def test_source_kind_is_rendered_exactly_once_per_source():
    single = rc.render_research_evidence_block(_internal_projection(CURATED))
    assert single.count(SOURCE_KIND_PREFIX) == 1

    projection = _two_source_projection(CURATED, CURATED)
    block = rc.render_research_evidence_block(projection)
    assert len(projection.sources) == 2
    assert block.count(SOURCE_KIND_PREFIX) == 2
    assert _rendered_kind_values(block) == [CURATED, CURATED]


def test_every_projected_source_carries_the_distinction_unconditionally():
    """No projected source may reach the model without its provenance category."""
    projection = _two_source_projection(CURATED, RAW_WEB_CAPTURE)
    block = rc.render_research_evidence_block(projection)
    rendered = _rendered_kind_values(block)
    assert len(rendered) == len(projection.sources)
    assert rendered == [source.source_kind for source in projection.sources]


def test_a3_still_refuses_an_absent_category_at_the_projection_boundary():
    """The renderer's normalization below is defense in depth, not a policy change.

    A-3 both allows AND requires ``source_kind`` for ``internal_analysis``, so a
    projection whose source omits the category is refused before any renderer
    sees it. This pins that upstream check as unchanged.
    """
    projection = project_research_evidence_pack(build_pack("internal_analysis"))
    with pytest.raises(ValidationError):
        rebuild(
            projection,
            sources=(remodel(projection.sources[0], source_kind=None),),
        )


def test_absent_source_kind_renders_the_attestation_blank_not_the_literal_none():
    """An absent category must render as blank, exactly as the attestation does.

    ``ResearchEvidencePresentationSource.source_kind`` is ``Optional[str]``, so a
    directly constructed valid presentation source can carry ``None`` even though
    the canonical database-backed chain always supplies a real string and A-3
    refuses the omission one layer up (test above). Where the two boundaries can
    still be reached independently they must agree, and neither may show the
    model the literal text ``None`` as though it were a provenance category.
    """
    projection = _unvalidated_kinds(build_pack("internal_analysis"), None)
    assert projection.sources[0].source_kind is None

    block = rc.render_research_evidence_block(projection)

    # The line stays present and unconditional: absence is disclosed, not hidden.
    assert block.count(SOURCE_KIND_PREFIX) == 1
    lines = block.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("  S1 "))
    assert lines[header + 1].startswith(SOURCE_KIND_PREFIX)

    # ...and its rendered value is blank, never Python's ``None`` repr.
    assert _rendered_kind_values(block) == [""]
    assert "None" not in block

    # The blank is exactly what the durable attestation already normalizes to,
    # so the two boundaries can no longer disagree about the same source.
    identity = rc._attestation_from_projection(projection)["sources"][0]
    assert identity.source_kind == ""
    assert _rendered_kind_values(block) == [identity.source_kind]


def test_absent_and_concrete_source_kinds_stay_separate_in_one_block():
    """Normalizing the absent case must not touch a category that is present."""
    projection = _unvalidated_kinds(
        build_two_member_pack("internal_analysis"), None, CURATED,
    )
    block = rc.render_research_evidence_block(projection)

    assert _rendered_kind_values(block) == ["", CURATED]
    assert block.count(SOURCE_KIND_PREFIX + CURATED) == 1
    assert "None" not in block

    # No private locator, blob id, storage reference or local path rides along.
    assert "storage_ref" not in ResearchEvidencePresentationSource.model_fields
    assert "storage_ref" not in block and "evidence_source_store" not in block
    for source in projection.sources:
        for private in (
            source.source_locator, source.source_blob_id,
            source.source_metadata_revision_id,
        ):
            assert private, "fixture must populate the private field"
            assert private not in block


# ═══════════════════ (3,4,5) durable consumption identity ═══════════════════


def test_source_kind_survives_into_the_consumption_source_identities(monkeypatch):
    projection = _internal_projection(CURATED)
    consumption = _consume(monkeypatch, projection)
    assert consumption.used
    assert [source.source_kind for source in consumption.sources] == [CURATED]
    assert consumption.sources[0].source_snapshot_id == (
        projection.sources[0].source_snapshot_id
    )


def test_source_kind_survives_into_event_details_sources(monkeypatch):
    projection = _internal_projection(CURATED)
    consumption = _consume(monkeypatch, projection)
    details = consumption.event_details()
    assert [item["source_kind"] for item in details["sources"]] == [CURATED]
    # ...and back out again through the Decision Trace impact projection, so the
    # distinction is not dropped one hop after it is recorded.
    state = _state(log=[{
        "event_type": rc.RESEARCH_EVIDENCE_EVENT_TYPE,
        "phase": "audit",
        "details": details,
    }])
    impact = rc.build_phase_research_evidence_impact(state, "audit")
    assert [source.source_kind for source in impact.sources] == [CURATED]


def test_empty_projection_attestation_has_no_source_identities(monkeypatch):
    """The additive field cannot fabricate a source where there is none."""
    from research_evidence.pack_models import ResearchEvidencePackAggregate

    projection = project_research_evidence_pack(
        ResearchEvidencePackAggregate(
            project_id=str(uuid4()), usage_scope="internal_analysis",
        )
    )
    consumption = _consume(monkeypatch, projection)
    assert consumption.empty
    assert consumption.sources == ()
    assert consumption.event_details()["sources"] == []


def test_historical_source_identity_payloads_without_source_kind_still_parse():
    # Constructed the way pre-R2.0A-4C code did: two fields, no source_kind.
    identity = rc.ResearchEvidenceSourceIdentity(
        source_snapshot_id="s-1", citation_label="Example 2026",
    )
    assert identity.source_kind == ""

    # ...and validated from a historical payload/event dict.
    parsed = rc.ResearchEvidenceSourceIdentity.model_validate(
        {"source_snapshot_id": "s-1", "citation_label": "Example 2026"}
    )
    assert parsed.source_kind == ""
    assert rc.ResearchEvidenceSourceIdentity().source_kind == ""


def test_historical_attestation_events_without_source_kind_read_back_safely():
    historical = {
        "event_type": rc.RESEARCH_EVIDENCE_EVENT_TYPE,
        "phase": "audit",
        "details": {
            "phase": "audit",
            "usage_scope": "internal_analysis",
            "status": "used",
            "projection_fingerprint": "f" * 64,
            "policy_identifier": "research_evidence_presentation_disclosure",
            "policy_version": "1.0.0",
            "counts": {
                "source_count": 1, "claim_count": 1,
                "evidence_count": 1, "relationship_count": 1,
            },
            # Exactly the historical shape: no source_kind key at all.
            "sources": [
                {"source_snapshot_id": "s-1", "citation_label": "Example 2026"}
            ],
            "block_bytes": 1234,
            "blocked_reason": "",
        },
    }
    impact = rc.build_phase_research_evidence_impact(
        _state(log=[historical]), "audit",
    )
    assert impact is not None
    assert impact.status == "used"
    assert impact.sources[0].source_snapshot_id == "s-1"
    assert impact.sources[0].citation_label == "Example 2026"
    assert impact.sources[0].source_kind == ""


# ═══════════════════ (6) no storage/path disclosure ═══════════════════


def test_source_kind_discloses_no_storage_reference_or_local_path():
    projection = _internal_projection(CURATED)
    block = rc.render_research_evidence_block(projection)

    # The projection has no storage reference to leak in the first place: A-2/A-3
    # never carry one, so the renderer cannot emit it.
    assert "storage_ref" not in ResearchEvidencePresentationSource.model_fields
    assert "storage_ref" not in block
    assert "evidence_source_store" not in block

    # The rendered category is a closed vocabulary label, not a location.
    [rendered] = _rendered_kind_values(block)
    assert rendered == CURATED
    assert "/" not in rendered and os.sep not in rendered
    assert not Path(rendered).is_absolute()
    assert "sha256" not in rendered

    # ...and the same holds for the durable identity.
    identity = rc._attestation_from_projection(projection)["sources"][0]
    assert identity.source_kind == CURATED
    assert "/" not in identity.source_kind


def test_renderer_still_omits_private_capture_locator_and_blob_ids():
    """The fix must not have widened the consumer-input allowlist."""
    projection = _internal_projection(CURATED)
    block = rc.render_research_evidence_block(projection)
    source = projection.sources[0]
    for private in (
        source.source_locator, source.source_blob_id,
        source.source_metadata_revision_id,
    ):
        assert private, "fixture must populate the private field to prove omission"
        assert private not in block


# ═══════════════════ (7) truthful distinguishability ═══════════════════


def test_raw_web_capture_stays_distinguishable_from_a_curated_record():
    curated = rc.render_research_evidence_block(_internal_projection(CURATED))
    fetched = rc.render_research_evidence_block(
        _internal_projection(RAW_WEB_CAPTURE)
    )
    assert _rendered_kind_values(curated) == [CURATED]
    assert _rendered_kind_values(fetched) == [RAW_WEB_CAPTURE]
    assert CURATED != RAW_WEB_CAPTURE
    assert RAW_WEB_CAPTURE not in curated
    assert CURATED not in fetched

    # Side by side in ONE block the two categories remain separate lines, so a
    # model reading the block can tell an operator-curated record from material
    # this system claims to have fetched.
    mixed = rc.render_research_evidence_block(
        _two_source_projection(*sorted((CURATED, RAW_WEB_CAPTURE)))
    )
    assert sorted(_rendered_kind_values(mixed)) == sorted(
        (CURATED, RAW_WEB_CAPTURE)
    )

    # `raw_web_capture` is exactly one of the kinds the evidence-only ingress
    # refuses to claim, which is why the distinction has to be mechanical.
    assert RAW_WEB_CAPTURE in RESERVED_SOURCE_KINDS
    assert CURATED not in RESERVED_SOURCE_KINDS


def test_source_kind_distinction_changes_the_durable_attestation(monkeypatch):
    curated = _consume(monkeypatch, _internal_projection(CURATED))
    fetched = _consume(monkeypatch, _internal_projection(RAW_WEB_CAPTURE))
    assert curated.event_details()["sources"][0]["source_kind"] == CURATED
    assert fetched.event_details()["sources"][0]["source_kind"] == RAW_WEB_CAPTURE


# ═══════════════════ (8,9) budget and fail-closed overflow ═══════════════════


def test_frozen_prompt_budget_is_unchanged():
    assert rc.RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES == 65536


def test_byte_budget_measures_the_new_provenance_line(monkeypatch):
    projection = _internal_projection(CURATED)
    block = rc.render_research_evidence_block(projection)
    line = SOURCE_KIND_PREFIX + CURATED
    assert line in block.splitlines()

    size = len(block.encode("utf-8"))
    without_line = size - len(("\n" + line).encode("utf-8"))
    assert without_line < size

    # A budget that would have admitted the block BEFORE the provenance line
    # existed now overflows: the line is inside the measured complete block.
    monkeypatch.setattr(
        rc, "RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES", without_line,
    )
    with pytest.raises(rc.ResearchEvidencePromptBudgetError):
        rc.render_research_evidence_block(projection)

    # At exactly the complete size (provenance line included) it passes.
    monkeypatch.setattr(rc, "RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES", size)
    assert rc.render_research_evidence_block(projection) == block


def test_overflow_remains_fail_closed_with_no_truncated_block(monkeypatch):
    projection = _internal_projection(CURATED)
    size = len(rc.render_research_evidence_block(projection).encode("utf-8"))
    monkeypatch.setattr(rc, "RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES", size - 1)

    with pytest.raises(rc.ResearchEvidencePromptBudgetError):
        rc.render_research_evidence_block(projection)

    consumption = _consume(monkeypatch, projection)
    assert consumption.blocked
    assert consumption.blocked_reason == (
        rc.ResearchEvidenceBlockReason.PROMPT_OVERFLOW.value
    )
    # No block at all — not a truncated one — and nothing reaches a prompt.
    assert consumption.block is None
    assert consumption.block_bytes == 0
    assert consumption.prompt_section() == ""


def test_renderer_introduces_no_truncation_of_the_rendered_value():
    """A long (hypothetical) category is emitted whole or the block overflows."""
    long_kind = "operator_curated_research_evidence_record_" + "x" * 200
    block = rc.render_research_evidence_block(_internal_projection(long_kind))
    assert SOURCE_KIND_PREFIX + long_kind in block.splitlines()
    for marker in ("...", "…", "[truncated]"):
        assert marker not in block


# ═══════════════════ (10) A-3 policy is unchanged ═══════════════════


def test_a3_policy_still_authorizes_source_kind_only_where_it_did_before():
    internal_allowed = policy.allowed_presentation_fields(
        UsageScope.INTERNAL_ANALYSIS, "source",
    )
    internal_required = policy.required_presentation_fields(
        UsageScope.INTERNAL_ANALYSIS, "source",
    )
    assert "source_kind" in internal_allowed
    assert "source_kind" in internal_required

    # The consumer's fix must not have widened any other scope's disclosure. The
    # authorized set is pinned exactly, so a future policy change that lets
    # `source_kind` into client_report fails here instead of silently shipping.
    authorized = {
        scope.value
        for scope in UsageScope
        if "source_kind" in policy.allowed_presentation_fields(scope, "source")
    }
    assert authorized == {"internal_analysis", "operator_dossier"}
    assert "client_report" not in authorized
    assert "source_kind" not in policy.required_presentation_fields(
        UsageScope.CLIENT_REPORT, "source",
    )


def test_unauthorized_scopes_omit_source_kind_from_the_projection_entirely():
    unauthorized = project_research_evidence_pack(build_pack("client_report"))
    assert unauthorized.sources[0].source_kind is None
    # The pack it was projected from really did carry a category, so this is
    # omission by policy, not an empty fixture.
    assert build_pack("client_report").sources[0].source_kind == "url"


def test_renderer_cannot_be_pointed_at_an_unauthorized_scope():
    """The provenance fix did not create a path to a wider disclosure."""
    from research_evidence.presentation_projection_service import (
        ResearchEvidencePresentationProjectionIntegrityError,
    )

    unauthorized = project_research_evidence_pack(build_pack("client_report"))
    with pytest.raises(ResearchEvidencePresentationProjectionIntegrityError):
        rc.render_research_evidence_block(unauthorized)
