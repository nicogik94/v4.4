"""PostgreSQL contract tests for R1.6 consumer-input bindings."""
import concurrent.futures
from datetime import datetime, timedelta, timezone
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.automation_roi_fixtures as roi_fx  # noqa: E402
import tests.evidence_snapshot_pg as pg  # noqa: E402
from knowledge.evidence_snapshot import repository as ev_repo  # noqa: E402
from research_evidence import binding_service  # noqa: E402
from research_evidence import claim_support_service  # noqa: E402
from research_evidence import freshness_service  # noqa: E402
from research_evidence import review_service  # noqa: E402
from research_evidence.binding_models import (  # noqa: E402
    ResearchEvidenceConsumerInputBindingCreate,
)
from research_evidence.claim_support_models import (  # noqa: E402
    ResearchEvidenceClaimSupportAssessmentCreate,
)
from research_evidence.freshness_models import (  # noqa: E402
    ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
)
from research_evidence.review_models import (  # noqa: E402
    ResearchEvidenceIntakeItemReviewDecisionCreate,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
TABLES = (
    "research_evidence_consumer_input_binding",
    "research_evidence_consumer_input_binding_sequence_allocator",
)
CONSTRAINTS = (
    "research_evidence_consumer_input_binding_pkey",
    "uq_recib_id_project_scope",
    "uq_recib_scope_sequence",
    "uq_recib_scope_request",
    "uq_recib_supersedes_once",
    "fk_recib_evidence_item_project",
    "fk_recib_calculation_input_role",
    "fk_recib_claim_support_pair",
    "fk_recib_review_decision_item",
    "fk_recib_freshness_assessment_item",
    "fk_recib_supersedes_same_scope",
    "ck_recib_consumer_contract",
    "ck_recib_consumer_shape",
    "ck_recib_claim_pair_shape",
    "ck_recib_review_shape",
    "ck_recib_freshness_shape",
    "pk_recib_sequence_allocator",
)


def _sql_text(statement, conn):
    return (
        statement
        if isinstance(statement, str)
        else statement.as_string(conn)
    )


class BarrierBeforeBindingInsert:
    def __init__(self, conn, barrier):
        self._conn = conn
        self._barrier = barrier
        self._waiting = True

    def execute(self, statement, params=None):
        if (
            self._waiting
            and "INSERT INTO research_evidence_consumer_input_binding"
            in _sql_text(statement, self._conn)
        ):
            self._waiting = False
            self._barrier.wait()
        return self._conn.execute(statement, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class AdvisoryStatementSnapshotBarrier:
    def __init__(self, conn, advisory_key):
        self._conn = conn
        self._advisory_key = advisory_key
        self._waiting = True

    def execute(self, statement, params=None):
        text = _sql_text(statement, self._conn)
        if (
            self._waiting
            and "evaluated_context AS MATERIALIZED" in text
        ):
            self._waiting = False
            text = text.replace(
                "WITH request_input AS MATERIALIZED (",
                "WITH snapshot_barrier AS MATERIALIZED "
                f"(SELECT pg_advisory_xact_lock({self._advisory_key})), "
                "request_input AS MATERIALIZED (",
                1,
            )
            text = text.replace(
                "%s::text AS evaluated_by\n        ),\n"
                "        evidence_context AS MATERIALIZED",
                "%s::text AS evaluated_by\n"
                "            FROM snapshot_barrier\n"
                "        ),\n"
                "        evidence_context AS MATERIALIZED",
                1,
            )
            assert "FROM snapshot_barrier" in text
            statement = text
        return self._conn.execute(statement, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _execute_autocommit(conn, statements):
    prior = pg._begin_autocommit(conn)
    try:
        for statement in statements:
            if isinstance(statement, tuple):
                conn.execute(statement[0], statement[1])
            else:
                conn.execute(statement)
    finally:
        pg._restore_autocommit(conn, prior)


def _corrupt_with_replication_role(conn, statement, params):
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute("SET session_replication_role = replica")
        try:
            conn.execute(statement, params)
        finally:
            conn.execute("SET session_replication_role = origin")
    finally:
        pg._restore_autocommit(conn, prior)


@pytest.fixture
def conn():
    pg.require_dsn()
    connection = pg.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def schema_v57(conn):
    with pg.fresh_schema(conn) as schema:
        pg.apply_v48(conn)
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        pg.apply_v53_research_intake(conn)
        pg.apply_v54_research_review(conn)
        pg.apply_v55_research_freshness(conn)
        pg.apply_v56_research_claim_support(conn)
        pg.apply_v57_research_binding(conn)
        yield schema


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


def _seed_evidence(conn, *, tag="evidence", project_id=None, role="periods_per_year"):
    project_id = project_id or pg.insert_project(conn, name=f"binding-{tag}")
    fact_id, snapshot_id, frozen_input_id = roi_fx.seed_and_freeze(
        conn, project_id, role, tag=f"{tag}-{uuid.uuid4().hex}"
    )
    source_metadata_id = conn.execute(
        """
        INSERT INTO research_source_metadata_revision
            (project_id, source_snapshot_id, canonical_source_locator,
             citation_label, created_by)
        VALUES (%s, %s, %s, %s, 'operator')
        RETURNING id::text
        """,
        (
            project_id,
            snapshot_id,
            f"stored://{tag}",
            f"source-{tag}",
        ),
    ).fetchone()[0]
    fact_metadata_id = conn.execute(
        """
        INSERT INTO research_fact_metadata_revision
            (project_id, candidate_fact_revision_id, citation_locator,
             excerpt_hash, created_by)
        VALUES (%s, %s, %s, %s, 'operator')
        RETURNING id::text
        """,
        (project_id, fact_id, f"section-{tag}", f"excerpt-{tag}"),
    ).fetchone()[0]
    intake_id = conn.execute(
        """
        INSERT INTO research_evidence_intake
            (project_id, source_snapshot_id, source_metadata_revision_id,
             selection_reason, created_by)
        VALUES (%s, %s, %s, 'Selected existing evidence', 'operator')
        RETURNING id::text
        """,
        (project_id, snapshot_id, source_metadata_id),
    ).fetchone()[0]
    item_id = conn.execute(
        """
        INSERT INTO research_evidence_intake_item
            (project_id, research_evidence_intake_id, source_snapshot_id,
             item_kind, candidate_fact_revision_id,
             fact_metadata_revision_id, created_by)
        VALUES (%s, %s, %s, 'candidate_fact', %s, %s, 'operator')
        RETURNING id::text
        """,
        (
            project_id,
            intake_id,
            snapshot_id,
            fact_id,
            fact_metadata_id,
        ),
    ).fetchone()[0]
    blob_id = conn.execute(
        "SELECT source_blob_id::text FROM source_snapshot WHERE id = %s",
        (snapshot_id,),
    ).fetchone()[0]
    return {
        "project": project_id,
        "blob": blob_id,
        "snapshot": snapshot_id,
        "source_metadata": source_metadata_id,
        "fact": fact_id,
        "fact_metadata": fact_metadata_id,
        "intake": intake_id,
        "item": item_id,
        "frozen_input": frozen_input_id,
        "role": role,
    }


def _seed_claim(conn, evidence, *, tag="claim"):
    blob_id = ev_repo.insert_or_get_blob(
        conn,
        project_id=evidence["project"],
        content_hash=f"claim-{tag}-{uuid.uuid4().hex}",
        byte_size=19,
    )
    snapshot_id = ev_repo.insert_snapshot(
        conn,
        source_blob_id=blob_id,
        project_id=evidence["project"],
        storage_ref=f"/r1.6/{tag}/{uuid.uuid4().hex}",
    )
    source_metadata_id = conn.execute(
        """
        INSERT INTO research_source_metadata_revision
            (project_id, source_snapshot_id, canonical_source_locator,
             created_by)
        VALUES (%s, %s, %s, 'operator')
        RETURNING id::text
        """,
        (evidence["project"], snapshot_id, f"stored://{tag}"),
    ).fetchone()[0]
    intake_id = conn.execute(
        """
        INSERT INTO research_evidence_intake
            (project_id, source_snapshot_id, source_metadata_revision_id,
             selection_reason, created_by)
        VALUES (%s, %s, %s, 'Selected claim context', 'operator')
        RETURNING id::text
        """,
        (evidence["project"], snapshot_id, source_metadata_id),
    ).fetchone()[0]
    claim_id = conn.execute(
        """
        INSERT INTO research_claim_draft
            (project_id, claim_text, created_by)
        VALUES (%s, %s, 'operator')
        RETURNING id::text
        """,
        (evidence["project"], f"Draft claim {tag}"),
    ).fetchone()[0]
    item_id = conn.execute(
        """
        INSERT INTO research_evidence_intake_item
            (project_id, research_evidence_intake_id, source_snapshot_id,
             item_kind, claim_draft_id, created_by)
        VALUES (%s, %s, %s, 'claim_draft', %s, 'operator')
        RETURNING id::text
        """,
        (evidence["project"], intake_id, snapshot_id, claim_id),
    ).fetchone()[0]
    return {"item": item_id, "claim": claim_id}


def _review(conn, evidence, decision="approved", request_id="review"):
    return review_service.record_item_review_decision(
        conn,
        ResearchEvidenceIntakeItemReviewDecisionCreate(
            project_id=evidence["project"],
            research_evidence_intake_item_id=evidence["item"],
            decision_type=decision,
            decision_reason=f"Operator recorded {decision}",
            decided_by="operator",
            request_id=request_id,
        ),
    )


def _freshness(
    conn,
    evidence,
    *,
    request_id="freshness",
    fresh_through=NOW + timedelta(days=30),
    drift_status="no_material_drift",
):
    return freshness_service.record_item_freshness_assessment(
        conn,
        ResearchEvidenceIntakeItemFreshnessAssessmentCreate(
            project_id=evidence["project"],
            research_evidence_intake_item_id=evidence["item"],
            request_id=request_id,
            policy_identifier="binding-freshness",
            policy_version="1",
            policy_parameters_json={"window_days": 30},
            evaluator_version="freshness.v1",
            basis_timestamp=NOW,
            fresh_through=fresh_through,
            drift_status=drift_status,
            drift_reason=f"Operator declared {drift_status}.",
            assessed_by="operator",
        ),
    )


def _pair(conn, evidence, claim, *, relationship="support", request_id="pair"):
    return claim_support_service.record_claim_support_assessment(
        conn,
        ResearchEvidenceClaimSupportAssessmentCreate(
            project_id=evidence["project"],
            claim_intake_item_id=claim["item"],
            evidence_intake_item_id=evidence["item"],
            request_id=request_id,
            locator_resolution="resolvable",
            locator_rationale="Stored locator reviewed.",
            evidence_linkage="linked",
            evidence_linkage_rationale="Evidence link reviewed.",
            semantic_relationship=relationship,
            semantic_relationship_rationale="Relationship was operator declared.",
            assessed_by="operator",
        ),
    )


def _command(evidence, *, request_id="binding-1", **changes):
    values = {
        "project_id": evidence["project"],
        "consumer_contract": "report_evidence_register",
        "consumer_contract_version": "report-register.v1",
        "binding_set_id": "register-1",
        "input_key": "entry-1",
        "request_id": request_id,
        "evidence_intake_item_id": evidence["item"],
        "policy_identifier": "binding-policy",
        "policy_version": "1",
        "policy_parameters_json": {"stale": "qualified"},
        "evaluator_version": "binding-evaluator.v1",
        "freshness_as_of": NOW + timedelta(days=1),
        "consumer_disposition": "meets_contract",
        "disposition_reasons": ("inputs_observed",),
        "evaluated_by": "operator",
    }
    values.update(changes)
    return ResearchEvidenceConsumerInputBindingCreate(**values)


def test_v57_objects_constraints_triggers_and_reapply(conn, schema_v57):
    for table in TABLES:
        assert pg.table_exists(conn, schema_v57, table)
    present = conn.execute(
        """
        SELECT conname
        FROM pg_constraint
        WHERE connamespace = current_schema()::regnamespace
          AND conname = ANY(%s)
        """,
        (list(CONSTRAINTS),),
    ).fetchall()
    assert {row[0] for row in present} == set(CONSTRAINTS)
    triggers = conn.execute(
        """
        SELECT tgname, tgenabled
        FROM pg_trigger
        WHERE tgrelid =
              'research_evidence_consumer_input_binding'::regclass
          AND NOT tgisinternal
        """
    ).fetchall()
    assert set(triggers) == {
        ("trg_recib_prepare_insert", "A"),
        ("trg_recib_no_mutation", "O"),
    }
    pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    ("event_type", "target", "expected"),
    [
        (None, None, True),
        ("legal_hold", "blob", True),
        ("tombstone", "fact", False),
        ("redact", "snapshot", False),
    ],
)
def test_composable_fact_availability_matches_public_helper(
    conn, schema_v57, event_type, target, expected
):
    evidence = _seed_evidence(
        conn, tag=f"availability-{event_type or 'available'}"
    )
    if event_type is not None:
        target_fields = {
            "blob": {"source_blob_id": evidence["blob"]},
            "snapshot": {"source_snapshot_id": evidence["snapshot"]},
            "fact": {"candidate_fact_revision_id": evidence["fact"]},
        }
        ev_repo.insert_retention_event(
            conn,
            project_id=evidence["project"],
            event_type=event_type,
            reason=f"Parity case {event_type}",
            created_by="operator",
            **target_fields[target],
        )
    component = ev_repo.fact_availability_sql(
        sql.Placeholder(),
        fact_id_params=(evidence["fact"],),
    )
    composed = bool(
        conn.execute(
            sql.SQL("SELECT {}").format(component.expression),
            component.params,
        ).fetchone()[0]
    )
    assert composed is expected
    assert ev_repo.fact_available(conn, evidence["fact"]) is expected


def test_composable_fact_availability_matches_missing_fact_semantics(
    conn, schema_v57
):
    missing_fact = str(uuid.uuid4())
    component = ev_repo.fact_availability_sql(
        sql.Placeholder(),
        fact_id_params=(missing_fact,),
    )
    composed = bool(
        conn.execute(
            sql.SQL("SELECT {}").format(component.expression),
            component.params,
        ).fetchone()[0]
    )
    assert composed is True
    assert ev_repo.fact_available(conn, missing_fact) is True


def test_item_only_binding_derives_separate_review_freshness_and_graph(
    conn, schema_v57
):
    evidence = _seed_evidence(conn, tag="derived")
    review = _review(conn, evidence)
    freshness = _freshness(conn, evidence)
    record = binding_service.record_consumer_input_binding(
        conn, _command(evidence)
    )
    assert record.source_snapshot_id == evidence["snapshot"]
    assert record.source_blob_id == evidence["blob"]
    assert record.candidate_fact_revision_id == evidence["fact"]
    assert record.fact_metadata_revision_id == evidence["fact_metadata"]
    assert record.availability_status is True
    assert record.lineage_is_current is True
    assert record.review_decision_id == review.id
    assert record.review_status == "approved"
    assert record.freshness_assessment_id == freshness.id
    assert record.freshness_status == "fresh"
    assert record.drift_status == "no_material_drift"
    assert record.locator_resolution is None
    assert record.evidence_linkage is None
    assert record.semantic_relationship is None


def test_calculation_binding_requires_exact_project_role_and_fact(
    conn, schema_v57
):
    evidence = _seed_evidence(conn, tag="calculation")
    command = _command(
        evidence,
        consumer_contract="deterministic_calculation",
        consumer_contract_version="automation-roi.v1",
        binding_set_id="calculation-1",
        input_key=evidence["role"],
        approved_calculation_input_id=evidence["frozen_input"],
    )
    record = binding_service.record_consumer_input_binding(conn, command)
    assert record.calculation_kind == "automation_roi"
    assert record.input_key == evidence["role"]

    with pytest.raises(Exception, match="project, role, and fact"):
        binding_service.record_consumer_input_binding(
            conn,
            _command(
                evidence,
                request_id="wrong-role",
                consumer_contract="deterministic_calculation",
                consumer_contract_version="automation-roi.v1",
                binding_set_id="calculation-2",
                input_key="annual_recurring_cost",
                approved_calculation_input_id=evidence["frozen_input"],
            ),
        )


def test_scenario_identity_is_stored_without_stance_inference(conn, schema_v57):
    evidence = _seed_evidence(conn, tag="scenario")
    claim = _seed_claim(conn, evidence)
    pair = _pair(conn, evidence, claim, relationship="contradiction")
    record = binding_service.record_consumer_input_binding(
        conn,
        _command(
            evidence,
            consumer_contract="scenario_input",
            consumer_contract_version="scenario-observation.v1",
            binding_set_id="scenario-1",
            input_key="observation-1",
            observation_identity_version="scenario-observation.v1",
            observation_identity_fingerprint="a" * 64,
            claim_intake_item_id=claim["item"],
            claim_support_assessment_id=pair.id,
        ),
    )
    assert record.observation_identity_fingerprint == "a" * 64
    assert record.semantic_relationship == "contradiction"
    assert "stance" not in record.model_dump()


@pytest.mark.parametrize(
    "relationship",
    ("contradiction", "qualification", "insufficient_evidence"),
)
def test_claim_pair_report_retains_non_support_relationships(
    conn, schema_v57, relationship
):
    evidence = _seed_evidence(conn, tag=f"report-{relationship}")
    claim = _seed_claim(conn, evidence, tag=f"claim-{relationship}")
    pair = _pair(
        conn,
        evidence,
        claim,
        relationship=relationship,
        request_id=f"pair-{relationship}",
    )
    record = binding_service.record_consumer_input_binding(
        conn,
        _command(
            evidence,
            binding_set_id=f"register-{relationship}",
            input_key=f"entry-{relationship}",
            request_id=f"binding-{relationship}",
            claim_intake_item_id=claim["item"],
            claim_support_assessment_id=pair.id,
            consumer_disposition="qualified",
            disposition_reasons=(f"relationship_{relationship}",),
        ),
    )
    assert record.locator_resolution == "resolvable"
    assert record.evidence_linkage == "linked"
    assert record.semantic_relationship == relationship
    assert record.consumer_disposition == "qualified"


def test_pair_reference_must_match_exact_project_claim_and_evidence(
    conn, schema_v57
):
    evidence = _seed_evidence(conn, tag="pair-match")
    other = _seed_evidence(
        conn, tag="pair-other", project_id=evidence["project"]
    )
    claim = _seed_claim(conn, evidence)
    pair = _pair(conn, evidence, claim)
    with pytest.raises(Exception, match="does not match project and intake pair"):
        binding_service.record_consumer_input_binding(
            conn,
            _command(
                other,
                claim_intake_item_id=claim["item"],
                claim_support_assessment_id=pair.id,
            ),
        )


def test_later_status_appends_qualification_without_mutating_prior(
    conn, schema_v57
):
    evidence = _seed_evidence(conn, tag="later")
    _review(conn, evidence)
    _freshness(conn, evidence)
    first = binding_service.record_consumer_input_binding(
        conn, _command(evidence, request_id="before")
    )
    ev_repo.insert_retention_event(
        conn,
        project_id=evidence["project"],
        event_type="tombstone",
        candidate_fact_revision_id=evidence["fact"],
        reason="Withdrawn after original evaluation",
        created_by="operator",
    )
    _review(
        conn,
        evidence,
        decision="withdrawn",
        request_id="review-withdrawn",
    )
    second = binding_service.record_consumer_input_binding(
        conn,
        _command(
            evidence,
            request_id="after",
            freshness_as_of=NOW + timedelta(days=60),
            consumer_disposition="qualified",
            disposition_reasons=("now_unavailable", "review_withdrawn", "stale"),
        ),
    )
    assert first.binding_sequence == 1
    assert second.binding_sequence == 2
    assert second.supersedes_binding_id == first.id
    assert first.availability_status is True
    assert first.review_status == "approved"
    assert first.freshness_status == "fresh"
    assert second.availability_status is False
    assert second.review_status == "withdrawn"
    assert second.freshness_status == "stale"


def test_history_is_append_only(conn, schema_v57):
    evidence = _seed_evidence(conn, tag="immutable")
    record = binding_service.record_consumer_input_binding(
        conn, _command(evidence)
    )
    conn.commit()
    with pytest.raises(Exception, match="append-only"):
        conn.execute(
            """
            UPDATE research_evidence_consumer_input_binding
            SET consumer_disposition = 'qualified'
            WHERE id = %s
            """,
            (record.id,),
        )
    conn.rollback()
    with pytest.raises(Exception, match="append-only"):
        conn.execute(
            "DELETE FROM research_evidence_consumer_input_binding WHERE id = %s",
            (record.id,),
        )
    conn.rollback()


def test_request_retry_is_idempotent_and_payload_mismatch_conflicts(
    conn, schema_v57
):
    evidence = _seed_evidence(conn, tag="retry")
    command = _command(evidence)
    first = binding_service.record_consumer_input_binding(conn, command)
    second = binding_service.record_consumer_input_binding(conn, command)
    assert second == first
    with pytest.raises(Exception, match="different immutable"):
        binding_service.record_consumer_input_binding(
            conn,
            _command(
                evidence,
                consumer_disposition="does_not_meet_contract",
                disposition_reasons=("policy_failed",),
            ),
        )
    assert conn.execute(
        """
        SELECT count(*)
        FROM research_evidence_consumer_input_binding
        WHERE project_id = %s
          AND consumer_contract = %s
          AND binding_set_id = %s
          AND input_key = %s
        """,
        (
            evidence["project"],
            command.consumer_contract,
            command.binding_set_id,
            command.input_key,
        ),
    ).fetchone()[0] == 1


def test_one_statement_snapshot_is_consistent_without_blocking_status_writers(
    conn, schema_v57
):
    evidence = _seed_evidence(conn, tag="stable-snapshot")
    claim = _seed_claim(conn, evidence, tag="stable-snapshot")
    original_review = _review(conn, evidence)
    original_freshness = _freshness(conn, evidence)
    original_pair = _pair(conn, evidence, claim)
    conn.commit()

    advisory_key = uuid.uuid4().int % 2_000_000_000
    conn.execute("SELECT pg_advisory_lock(%s)", (advisory_key,))
    evaluator_started = threading.Event()
    evaluator_pid = {}

    def evaluate():
        worker = pg.connect(schema=schema_v57)
        try:
            evaluator_pid["value"] = worker.info.backend_pid
            evaluator_started.set()
            wrapped = AdvisoryStatementSnapshotBarrier(worker, advisory_key)
            record = binding_service.record_consumer_input_binding(
                wrapped,
                _command(
                    evidence,
                    request_id="stable-snapshot",
                    binding_set_id="stable-snapshot",
                    claim_intake_item_id=claim["item"],
                    claim_support_assessment_id=original_pair.id,
                ),
            )
            worker.commit()
            return record
        finally:
            worker.close()

    def append_independent_status_changes():
        worker = pg.connect(schema=schema_v57)
        try:
            ev_repo.insert_retention_event(
                worker,
                project_id=evidence["project"],
                event_type="tombstone",
                candidate_fact_revision_id=evidence["fact"],
                reason="Concurrent retention change",
                created_by="operator",
            )
            worker.commit()
            worker.execute(
                """
                INSERT INTO research_fact_metadata_revision
                    (project_id, candidate_fact_revision_id,
                     supersedes_metadata_revision_id, created_by)
                VALUES (%s, %s, %s, 'operator')
                """,
                (
                    evidence["project"],
                    evidence["fact"],
                    evidence["fact_metadata"],
                ),
            )
            worker.commit()
            _review(
                worker,
                evidence,
                decision="withdrawn",
                request_id="concurrent-review",
            )
            worker.commit()
            _freshness(
                worker,
                evidence,
                request_id="concurrent-freshness",
                fresh_through=NOW,
                drift_status="material_drift",
            )
            worker.commit()
            changed_pair = _pair(
                worker,
                evidence,
                claim,
                relationship="contradiction",
                request_id="concurrent-pair",
            )
            worker.commit()
            return changed_pair
        finally:
            worker.close()

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        evaluated = executor.submit(evaluate)
        assert evaluator_started.wait(timeout=10)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            waiting = conn.execute(
                """
                SELECT wait_event_type, wait_event
                FROM pg_stat_activity
                WHERE pid = %s
                """,
                (evaluator_pid["value"],),
            ).fetchone()
            if waiting == ("Lock", "advisory"):
                break
            time.sleep(0.05)
        else:
            pytest.fail("binding evaluator did not reach statement barrier")

        changed = executor.submit(append_independent_status_changes)
        changed_pair = changed.result(timeout=20)
        assert not evaluated.done()
        conn.execute("SELECT pg_advisory_unlock(%s)", (advisory_key,))
        record = evaluated.result(timeout=20)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (advisory_key,))
        executor.shutdown(wait=True, cancel_futures=True)

    assert record.availability_status is True
    assert record.retention_basis == ()
    assert record.lineage_is_current is True
    assert record.lineage_basis == ()
    assert record.review_decision_id == original_review.id
    assert record.review_status == "approved"
    assert record.freshness_assessment_id == original_freshness.id
    assert record.freshness_status == "fresh"
    assert record.drift_status == "no_material_drift"
    assert record.claim_support_assessment_id == original_pair.id
    assert record.semantic_relationship == "support"

    later = binding_service.record_consumer_input_binding(
        conn,
        _command(
            evidence,
            request_id="after-concurrent-statuses",
            binding_set_id="after-concurrent-statuses",
            claim_intake_item_id=claim["item"],
            claim_support_assessment_id=changed_pair.id,
        ),
    )
    assert later.availability_status is False
    assert later.retention_basis
    assert later.lineage_is_current is False
    assert later.lineage_basis
    assert later.review_status == "withdrawn"
    assert later.freshness_status == "stale"
    assert later.drift_status == "material_drift"
    assert later.semantic_relationship == "contradiction"


def test_v57_reapply_detects_allocator_drift(conn, schema_v57):
    evidence = _seed_evidence(conn, tag="allocator-drift")
    binding_service.record_consumer_input_binding(conn, _command(evidence))
    conn.commit()
    conn.execute(
        """
        UPDATE research_evidence_consumer_input_binding_sequence_allocator
        SET last_sequence = 2
        WHERE project_id = %s
          AND consumer_contract = 'report_evidence_register'
          AND binding_set_id = 'register-1'
          AND input_key = 'entry-1'
        """,
        (evidence["project"],),
    )
    conn.commit()
    with pytest.raises(Exception, match="allocator diverges"):
        pg.apply_v57_research_binding(conn)


def test_v57_reapply_detects_altered_prepare_function(conn, schema_v57):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        """
        CREATE OR REPLACE FUNCTION research_evidence_prepare_binding_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$ BEGIN RETURN NEW; END $$
        """
    )
    conn.execute(
        "REVOKE ALL ON FUNCTION "
        "research_evidence_prepare_binding_insert() FROM PUBLIC"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent binding prepare function"):
        pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    ("mutation_sql", "error"),
    [
        (
            """
            ALTER TABLE research_evidence_consumer_input_binding
                DROP CONSTRAINT
                    research_evidence_consumer_input_binding_pkey,
                ADD CONSTRAINT
                    research_evidence_consumer_input_binding_pkey
                    PRIMARY KEY (id, project_id)
            """,
            "divergent binding keys",
        ),
        (
            """
            ALTER TABLE
                research_evidence_consumer_input_binding_sequence_allocator
                DROP CONSTRAINT
                    pk_recib_sequence_allocator,
                ADD CONSTRAINT
                    pk_recib_sequence_allocator
                    PRIMARY KEY (
                        project_id, consumer_contract, binding_set_id,
                        input_key, evidence_intake_item_id
                    )
            """,
            "divergent binding keys",
        ),
        (
            """
            ALTER TABLE research_evidence_consumer_input_binding
                DROP CONSTRAINT uq_recib_scope_request,
                ADD CONSTRAINT uq_recib_scope_request
                    UNIQUE (
                        project_id, consumer_contract, binding_set_id,
                        input_key, request_id, evidence_intake_item_id
                    )
            """,
            "divergent binding keys",
        ),
        (
            """
            ALTER TABLE research_evidence_consumer_input_binding
                DROP CONSTRAINT uq_recib_scope_request,
                ADD CONSTRAINT uq_recib_scope_request
                    UNIQUE (
                        project_id, consumer_contract, binding_set_id,
                        input_key, request_id
                    )
                    DEFERRABLE INITIALLY DEFERRED
            """,
            "divergent binding keys",
        ),
    ],
)
def test_v57_reapply_detects_altered_primary_and_unique_keys(
    conn, schema_v57, mutation_sql, error
):
    _execute_autocommit(conn, [mutation_sql])
    with pytest.raises(Exception, match=error):
        pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    "mutation_sql",
    [
        """
        ALTER TABLE research_evidence_consumer_input_binding
            DROP CONSTRAINT fk_recib_blob_project,
            ADD CONSTRAINT fk_recib_blob_project
                FOREIGN KEY (source_blob_id)
                REFERENCES source_blob(id)
                ON UPDATE NO ACTION ON DELETE RESTRICT
        """,
        """
        ALTER TABLE research_evidence_consumer_input_binding
            DROP CONSTRAINT fk_recib_calculation_input_role,
            ADD CONSTRAINT fk_recib_calculation_input_role
                FOREIGN KEY (approved_calculation_input_id, project_id)
                REFERENCES approved_calculation_input(id, project_id)
                ON UPDATE NO ACTION ON DELETE RESTRICT
        """,
        """
        ALTER TABLE research_evidence_consumer_input_binding
            DROP CONSTRAINT fk_recib_evidence_item_project,
            ADD CONSTRAINT fk_recib_evidence_item_project
                FOREIGN KEY (evidence_intake_item_id, project_id)
                REFERENCES research_evidence_intake_item(id, project_id)
                ON UPDATE CASCADE ON DELETE CASCADE
        """,
        """
        ALTER TABLE research_evidence_consumer_input_binding
            DROP CONSTRAINT fk_recib_blob_project,
            ADD CONSTRAINT fk_recib_blob_project
                FOREIGN KEY (source_blob_id, project_id)
                REFERENCES source_blob(id, project_id)
                ON UPDATE NO ACTION ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED
        """,
        """
        ALTER TABLE research_evidence_consumer_input_binding
            DROP CONSTRAINT fk_recib_evidence_item_project,
            ADD CONSTRAINT fk_recib_evidence_item_project
                FOREIGN KEY (evidence_intake_item_id, project_id)
                REFERENCES research_evidence_intake_item(id, project_id)
                MATCH FULL
                ON UPDATE NO ACTION ON DELETE RESTRICT
        """,
    ],
)
def test_v57_reapply_detects_foreign_key_scope_reference_and_action_drift(
    conn, schema_v57, mutation_sql
):
    _execute_autocommit(conn, [mutation_sql])
    with pytest.raises(Exception, match="divergent binding foreign keys"):
        pg.apply_v57_research_binding(conn)


def test_v57_reapply_detects_unvalidated_foreign_key(conn, schema_v57):
    _execute_autocommit(
        conn,
        [
            """
            ALTER TABLE research_evidence_consumer_input_binding
                DROP CONSTRAINT fk_recib_blob_project,
                ADD CONSTRAINT fk_recib_blob_project
                    FOREIGN KEY (source_blob_id, project_id)
                    REFERENCES source_blob(id, project_id)
                    MATCH SIMPLE
                    ON UPDATE NO ACTION ON DELETE RESTRICT
                    NOT VALID
            """,
        ],
    )
    state = conn.execute(
        """
        SELECT
            convalidated,
            condeferrable,
            condeferred,
            confupdtype,
            confdeltype,
            confmatchtype,
            conrelid =
                'research_evidence_consumer_input_binding'::regclass,
            confrelid = 'source_blob'::regclass,
            ARRAY(
                SELECT attribute.attname::text
                FROM unnest(conkey)
                     WITH ORDINALITY key_column(attnum, position)
                JOIN pg_attribute attribute
                  ON attribute.attrelid = conrelid
                 AND attribute.attnum = key_column.attnum
                ORDER BY key_column.position
            ),
            ARRAY(
                SELECT attribute.attname::text
                FROM unnest(confkey)
                     WITH ORDINALITY key_column(attnum, position)
                JOIN pg_attribute attribute
                  ON attribute.attrelid = confrelid
                 AND attribute.attnum = key_column.attnum
                ORDER BY key_column.position
            )
        FROM pg_constraint
        WHERE connamespace = current_schema()::regnamespace
          AND conname = 'fk_recib_blob_project'
        """
    ).fetchone()
    assert state == (
        False,
        False,
        False,
        "a",
        "r",
        "s",
        True,
        True,
        ["source_blob_id", "project_id"],
        ["id", "project_id"],
    )
    with pytest.raises(Exception, match="missing constraints fk_recib_blob_project"):
        pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    "statements",
    [
        [
            """
            ALTER TABLE research_evidence_consumer_input_binding
                ENABLE TRIGGER trg_recib_prepare_insert
            """,
        ],
        [
            """
            DROP TRIGGER trg_recib_prepare_insert
                ON research_evidence_consumer_input_binding
            """,
            """
            CREATE TRIGGER trg_recib_prepare_insert
                BEFORE INSERT
                ON research_evidence_consumer_input_binding
                FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation()
            """,
            """
            ALTER TABLE research_evidence_consumer_input_binding
                ENABLE ALWAYS TRIGGER trg_recib_prepare_insert
            """,
        ],
        [
            """
            DROP TRIGGER trg_recib_no_mutation
                ON research_evidence_consumer_input_binding
            """,
            """
            CREATE TRIGGER trg_recib_no_mutation
                BEFORE UPDATE OF consumer_disposition OR DELETE
                ON research_evidence_consumer_input_binding
                FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation()
            """,
        ],
    ],
)
def test_v57_reapply_detects_trigger_state_linkage_and_definition_drift(
    conn, schema_v57, statements
):
    _execute_autocommit(conn, statements)
    with pytest.raises(Exception, match="divergent binding triggers"):
        pg.apply_v57_research_binding(conn)


def test_v57_reapply_detects_trigger_arguments(conn, schema_v57):
    _execute_autocommit(
        conn,
        [
            """
            DROP TRIGGER trg_recib_no_mutation
                ON research_evidence_consumer_input_binding
            """,
            """
            CREATE TRIGGER trg_recib_no_mutation
                BEFORE UPDATE OR DELETE
                ON research_evidence_consumer_input_binding
                FOR EACH ROW
                EXECUTE FUNCTION slicea_reject_mutation('argument')
            """,
        ],
    )
    state = conn.execute(
        """
        SELECT
            tgnargs,
            tgqual IS NULL,
            tgenabled,
            tgtype,
            tgfoid = 'slicea_reject_mutation()'::regprocedure
        FROM pg_trigger
        WHERE tgrelid =
              'research_evidence_consumer_input_binding'::regclass
          AND tgname = 'trg_recib_no_mutation'
          AND NOT tgisinternal
        """
    ).fetchone()
    assert state == (1, True, "O", 27, True)
    with pytest.raises(Exception, match="divergent binding triggers"):
        pg.apply_v57_research_binding(conn)


def test_v57_reapply_detects_trigger_condition(conn, schema_v57):
    _execute_autocommit(
        conn,
        [
            """
            DROP TRIGGER trg_recib_no_mutation
                ON research_evidence_consumer_input_binding
            """,
            """
            CREATE TRIGGER trg_recib_no_mutation
                BEFORE UPDATE OR DELETE
                ON research_evidence_consumer_input_binding
                FOR EACH ROW
                WHEN (OLD.id IS NOT NULL)
                EXECUTE FUNCTION slicea_reject_mutation()
            """,
        ],
    )
    state = conn.execute(
        """
        SELECT
            tgnargs,
            tgqual IS NOT NULL,
            tgenabled,
            tgtype,
            tgfoid = 'slicea_reject_mutation()'::regprocedure
        FROM pg_trigger
        WHERE tgrelid =
              'research_evidence_consumer_input_binding'::regclass
          AND tgname = 'trg_recib_no_mutation'
          AND NOT tgisinternal
        """
    ).fetchone()
    assert state == (0, True, "O", 27, True)
    with pytest.raises(Exception, match="divergent binding triggers"):
        pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    ("mutation_sql", "error"),
    [
        (
            """
            ALTER TABLE research_evidence_consumer_input_binding
                ADD COLUMN unexpected_column TEXT
            """,
            "divergent binding column count",
        ),
        (
            """
            ALTER TABLE research_evidence_consumer_input_binding
                RENAME COLUMN evaluator_version
                TO evaluator_version_drift
            """,
            "divergent binding columns",
        ),
        (
            """
            ALTER TABLE research_evidence_consumer_input_binding
                ALTER COLUMN consumer_contract_version TYPE varchar(255)
            """,
            "divergent binding columns",
        ),
        (
            """
            ALTER TABLE research_evidence_consumer_input_binding
                ALTER COLUMN evaluated_by DROP NOT NULL
            """,
            "divergent binding columns",
        ),
        (
            """
            ALTER TABLE
                research_evidence_consumer_input_binding_sequence_allocator
                ADD COLUMN unexpected_column TEXT
            """,
            "divergent allocator columns",
        ),
        (
            """
            ALTER TABLE
                research_evidence_consumer_input_binding_sequence_allocator
                RENAME COLUMN evidence_intake_item_id
                TO evidence_intake_item_id_drift
            """,
            "divergent allocator columns",
        ),
        (
            """
            ALTER TABLE
                research_evidence_consumer_input_binding_sequence_allocator
                ALTER COLUMN consumer_contract TYPE varchar(255)
            """,
            "divergent allocator columns",
        ),
        (
            """
            ALTER TABLE
                research_evidence_consumer_input_binding_sequence_allocator
                ALTER COLUMN evidence_intake_item_id DROP NOT NULL
            """,
            "divergent allocator columns",
        ),
    ],
)
def test_v57_reapply_detects_column_count_name_type_and_nullability_drift(
    conn, schema_v57, mutation_sql, error
):
    _execute_autocommit(conn, [mutation_sql])
    with pytest.raises(Exception, match=error):
        pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    "mutation_sql",
    [
        """
        ALTER TABLE research_evidence_consumer_input_binding
            ALTER COLUMN id SET DEFAULT
                '00000000-0000-0000-0000-000000000000'::uuid
        """,
        """
        ALTER TABLE research_evidence_consumer_input_binding
            ALTER COLUMN id DROP DEFAULT
        """,
    ],
)
def test_v57_reapply_detects_binding_id_default_drift(
    conn, schema_v57, mutation_sql
):
    _execute_autocommit(conn, [mutation_sql])
    with pytest.raises(Exception, match="divergent binding id default"):
        pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    ("table", "constraint"),
    [
        (
            "research_evidence_consumer_input_binding",
            "ck_recib_consumer_contract",
        ),
        (
            "research_evidence_consumer_input_binding",
            "ck_recib_consumer_shape",
        ),
        (
            "research_evidence_consumer_input_binding",
            "ck_recib_claim_pair_shape",
        ),
        (
            "research_evidence_consumer_input_binding",
            "ck_recib_review_shape",
        ),
        (
            "research_evidence_consumer_input_binding",
            "ck_recib_freshness_shape",
        ),
        (
            "research_evidence_consumer_input_binding",
            "ck_recib_consumer_disposition",
        ),
        (
            "research_evidence_consumer_input_binding",
            "ck_recib_json_shapes",
        ),
        (
            "research_evidence_consumer_input_binding",
            "ck_recib_policy_provenance",
        ),
        (
            "research_evidence_consumer_input_binding",
            "ck_recib_nonblank",
        ),
        (
            "research_evidence_consumer_input_binding",
            "ck_recib_observation_fingerprint",
        ),
        (
            "research_evidence_consumer_input_binding",
            "ck_recib_sequence_positive",
        ),
        (
            "research_evidence_consumer_input_binding_sequence_allocator",
            "ck_recib_allocator_last_sequence",
        ),
    ],
)
def test_v57_reapply_detects_each_altered_check(
    conn, schema_v57, table, constraint
):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        f"ALTER TABLE {table} DROP CONSTRAINT {constraint}"
    )
    conn.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK (true)"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(
        Exception, match="divergent binding check constraints"
    ):
        pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    ("column", "default_expression"),
    [
        ("calculation_kind", "'automation_roi'::text"),
        ("source_snapshot_id", "gen_random_uuid()"),
        ("source_blob_id", "gen_random_uuid()"),
        ("source_metadata_revision_id", "gen_random_uuid()"),
        ("candidate_fact_revision_id", "gen_random_uuid()"),
        ("fact_metadata_revision_id", "gen_random_uuid()"),
        ("availability_status", "false"),
        ("retention_basis_json", "'[]'::jsonb"),
        ("lineage_is_current", "false"),
        ("lineage_basis_json", "'[]'::jsonb"),
        ("review_decision_id", "gen_random_uuid()"),
        ("review_decision_sequence", "1"),
        ("review_status", "'not_assessed'::text"),
        ("freshness_assessment_id", "gen_random_uuid()"),
        ("freshness_assessment_sequence", "1"),
        ("fresh_through", "clock_timestamp()"),
        ("freshness_status", "'unknown'::text"),
        ("drift_status", "'not_assessed'::text"),
        ("locator_resolution", "'not_assessed'::text"),
        ("evidence_linkage", "'not_assessed'::text"),
        ("semantic_relationship", "'not_assessed'::text"),
        ("binding_sequence", "1"),
        ("supersedes_binding_id", "gen_random_uuid()"),
        ("evaluated_at", "clock_timestamp()"),
    ],
)
def test_v57_reapply_detects_each_server_owned_default_drift(
    conn, schema_v57, column, default_expression
):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "ALTER TABLE research_evidence_consumer_input_binding "
        f"ALTER COLUMN {column} SET DEFAULT {default_expression}"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="server-owned fields have defaults"):
        pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    ("column", "default_expression"),
    [
        ("project_id", "gen_random_uuid()"),
        ("consumer_contract", "'report_evidence_register'::text"),
        ("binding_set_id", "'register'::text"),
        ("input_key", "'input'::text"),
        ("evidence_intake_item_id", "gen_random_uuid()"),
        ("last_sequence", "0"),
    ],
)
def test_v57_reapply_detects_each_allocator_default_drift(
    conn, schema_v57, column, default_expression
):
    _execute_autocommit(
        conn,
        [
            "ALTER TABLE "
            "research_evidence_consumer_input_binding_sequence_allocator "
            f"ALTER COLUMN {column} SET DEFAULT {default_expression}"
        ],
    )
    with pytest.raises(Exception, match="allocator fields have defaults"):
        pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    "create_sql",
    [
        "CREATE INDEX idx_recib_scope_sequence "
        "ON research_evidence_consumer_input_binding(project_id)",
        "CREATE INDEX idx_recib_scope_sequence "
        "ON research_evidence_consumer_input_binding("
        "project_id, consumer_contract, binding_set_id, input_key, "
        "binding_sequence) INCLUDE (request_id)",
    ],
)
def test_v57_reapply_detects_altered_index(conn, schema_v57, create_sql):
    _execute_autocommit(
        conn,
        [
            "DROP INDEX idx_recib_scope_sequence",
            create_sql,
        ],
    )
    with pytest.raises(Exception, match="divergent binding index"):
        pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    ("grant_sql", "error"),
    [
        (
            "GRANT SELECT ON "
            "research_evidence_consumer_input_binding_sequence_allocator "
            "TO PUBLIC",
            "allocator has PUBLIC privileges",
        ),
        (
            "GRANT EXECUTE ON FUNCTION "
            "research_evidence_prepare_binding_insert() TO PUBLIC",
            "divergent binding prepare function",
        ),
    ],
)
def test_v57_reapply_detects_privilege_drift(
    conn, schema_v57, grant_sql, error
):
    prior = pg._begin_autocommit(conn)
    conn.execute(grant_sql)
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match=error):
        pg.apply_v57_research_binding(conn)


def test_v57_reapply_detects_malformed_predecessor_chain(conn, schema_v57):
    evidence = _seed_evidence(conn, tag="predecessor-drift")
    binding_service.record_consumer_input_binding(
        conn, _command(evidence, request_id="predecessor-1")
    )
    second = binding_service.record_consumer_input_binding(
        conn, _command(evidence, request_id="predecessor-2")
    )
    conn.commit()

    _corrupt_with_replication_role(
        conn,
        """
        UPDATE research_evidence_consumer_input_binding
        SET supersedes_binding_id = NULL
        WHERE id = %s
        """,
        (second.id,),
    )
    with pytest.raises(Exception, match="malformed binding chain"):
        pg.apply_v57_research_binding(conn)


def test_v57_reapply_detects_allocator_evidence_identity_drift(
    conn, schema_v57
):
    evidence = _seed_evidence(conn, tag="allocator-evidence-drift")
    other = _seed_evidence(
        conn,
        tag="allocator-evidence-other",
        project_id=evidence["project"],
    )
    binding_service.record_consumer_input_binding(conn, _command(evidence))
    conn.commit()

    _execute_autocommit(
        conn,
        [
            (
                """
                UPDATE
                    research_evidence_consumer_input_binding_sequence_allocator
                SET evidence_intake_item_id = %s
                WHERE project_id = %s
                  AND consumer_contract = 'report_evidence_register'
                  AND binding_set_id = 'register-1'
                  AND input_key = 'entry-1'
                """,
                (other["item"], evidence["project"]),
            )
        ],
    )
    with pytest.raises(Exception, match="allocator evidence identity diverges"):
        pg.apply_v57_research_binding(conn)


def test_v57_reapply_detects_linked_evidence_identity_drift(conn, schema_v57):
    evidence = _seed_evidence(conn, tag="linked-evidence-drift")
    other = _seed_evidence(
        conn, tag="linked-evidence-other", project_id=evidence["project"]
    )
    record = binding_service.record_consumer_input_binding(
        conn, _command(evidence)
    )
    conn.commit()

    _corrupt_with_replication_role(
        conn,
        """
        UPDATE research_evidence_consumer_input_binding
        SET evidence_intake_item_id = %s
        WHERE id = %s
        """,
        (other["item"], record.id),
    )
    with pytest.raises(Exception, match="evaluated identities diverge"):
        pg.apply_v57_research_binding(conn)


@pytest.mark.parametrize(
    "field",
    ("claim_intake_item_id", "claim_support_assessment_id"),
)
def test_v57_reapply_detects_linked_claim_and_pair_identity_drift(
    conn, schema_v57, field
):
    evidence = _seed_evidence(conn, tag=f"linked-{field}")
    original_claim = _seed_claim(conn, evidence, tag=f"original-{field}")
    original_pair = _pair(
        conn, evidence, original_claim, request_id=f"original-{field}"
    )
    other_claim = _seed_claim(conn, evidence, tag=f"other-{field}")
    other_pair = _pair(
        conn, evidence, other_claim, request_id=f"other-{field}"
    )
    record = binding_service.record_consumer_input_binding(
        conn,
        _command(
            evidence,
            claim_intake_item_id=original_claim["item"],
            claim_support_assessment_id=original_pair.id,
        ),
    )
    conn.commit()

    replacement = (
        other_claim["item"]
        if field == "claim_intake_item_id"
        else other_pair.id
    )
    _corrupt_with_replication_role(
        conn,
        f"""
        UPDATE research_evidence_consumer_input_binding
        SET {field} = %s
        WHERE id = %s
        """,
        (replacement, record.id),
    )
    with pytest.raises(Exception, match="evaluated identities diverge"):
        pg.apply_v57_research_binding(conn)


def test_two_connections_produce_contiguous_binding_chain(conn, schema_v57):
    evidence = _seed_evidence(conn, tag="concurrent-distinct")
    conn.commit()
    barrier = threading.Barrier(2)

    def append(request_id):
        worker = pg.connect(schema=schema_v57)
        try:
            wrapped = BarrierBeforeBindingInsert(worker, barrier)
            record = binding_service.record_consumer_input_binding(
                wrapped, _command(evidence, request_id=request_id)
            )
            worker.commit()
            return record
        finally:
            worker.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        records = list(executor.map(append, ("concurrent-1", "concurrent-2")))
    assert sorted(record.binding_sequence for record in records) == [1, 2]
    rows = conn.execute(
        """
        SELECT id::text, binding_sequence, supersedes_binding_id::text
        FROM research_evidence_consumer_input_binding
        WHERE project_id = %s
          AND consumer_contract = 'report_evidence_register'
          AND binding_set_id = 'register-1'
          AND input_key = 'entry-1'
        ORDER BY binding_sequence
        """,
        (evidence["project"],),
    ).fetchall()
    assert rows[0][1:] == (1, None)
    assert rows[1][1:] == (2, rows[0][0])


def test_two_connections_same_request_return_one_immutable_row(
    conn, schema_v57
):
    evidence = _seed_evidence(conn, tag="concurrent-retry")
    conn.commit()
    barrier = threading.Barrier(2)

    def append():
        worker = pg.connect(schema=schema_v57)
        try:
            wrapped = BarrierBeforeBindingInsert(worker, barrier)
            record = binding_service.record_consumer_input_binding(
                wrapped, _command(evidence, request_id="same-request")
            )
            worker.commit()
            return record
        finally:
            worker.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        records = list(executor.map(lambda _: append(), range(2)))
    assert records[0] == records[1]
    assert records[0].binding_sequence == 1
    assert conn.execute(
        """
        SELECT count(*), min(binding_sequence), max(binding_sequence)
        FROM research_evidence_consumer_input_binding
        WHERE project_id = %s
          AND consumer_contract = 'report_evidence_register'
          AND binding_set_id = 'register-1'
          AND input_key = 'entry-1'
        """,
        (evidence["project"],),
    ).fetchone() == (1, 1, 1)
