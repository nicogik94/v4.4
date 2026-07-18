from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from research_evidence import pack_repository as repo
from research_evidence.pack_models import (
    MAX_PACK_RELATIONSHIPS,
    ResearchEvidenceProjectContextRevisionCreate,
    ResearchEvidenceProjectContextRevisionRecord,
    ResearchEvidenceUsageAuthorizationDecisionCreate,
    ResearchEvidenceUsageAuthorizationDecisionRecord,
)


def uid(): return str(uuid4())


class Result:
    def __init__(self, row=None, rows=None): self.row, self.rows = row, rows or []
    def fetchone(self): return self.row
    def fetchall(self): return self.rows


class Conn:
    def __init__(self, results, isolation="read committed"):
        self.results, self.isolation, self.calls = list(results), isolation, []
        self.autocommit = False
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql == "SHOW transaction_isolation":
            return Result(row=(self.isolation,))
        return self.results.pop(0) if self.results else Result()


def context():
    return ResearchEvidenceProjectContextRevisionCreate(
        project_id=uid(), request_id="r", research_question="q",
        project_limitations=["l"], unresolved_gaps=["g"], actor="a",
    )


def row_for(value):
    return (uid(), value.project_id, value.request_id, value.research_question,
            list(value.project_limitations), list(value.unresolved_gaps), value.actor,
            1, None, datetime.now(timezone.utc))


def authorization():
    return ResearchEvidenceUsageAuthorizationDecisionCreate(
        project_id=uid(), claim_intake_item_id=uid(),
        evidence_intake_item_id=uid(), usage_scope="client_report",
        decision="authorized", reason="approved", actor="operator",
        request_id="authorization-request",
    )


def authorization_record(value):
    return ResearchEvidenceUsageAuthorizationDecisionRecord(
        **value.model_dump(), id=uid(), claim_support_assessment_id=uid(),
        claim_draft_id=uid(), claim_annotation_revision_id=uid(),
        claim_review_decision_id=uid(), evidence_review_decision_id=uid(),
        decision_sequence=1, supersedes_decision_id=None,
        recorded_at=datetime.now(timezone.utc),
    )


def assembly_row(*, claim_id=None, source_id=None, fact_id=None, sequence=1):
    now = datetime.now(timezone.utc)
    row = [None] * 59
    row[0:13] = [
        uid(), uid(), uid(), uid(), claim_id or uid(), uid(), uid(), uid(),
        sequence, now, source_id or uid(), uid(), uid(),
    ]
    row[13:24] = [
        "document", "source-locator", now, "canonical-locator", "publisher",
        "author", now, now, "citation", "high", "quality rationale",
    ]
    row[24:43] = [
        fact_id or uid(), uid(), "text", None, "evidence text", "", None, None,
        None, None, None, None, None, None, "stable-key", None,
        "fact-citation", "claim text", "claim-category",
    ]
    row[43:59] = [
        "reported_fact", "high", "relevant", "supports", "does not prove",
        [], [], None, None, None, None, 1, now, "resolvable", "linked", "support",
    ]
    return tuple(row)


def test_effective_context_is_project_scoped_and_sequence_ordered():
    value = context(); conn = Conn([Result(row=row_for(value))])
    record = repo.get_effective_project_context_revision(conn, project_id=value.project_id)
    sql, params = conn.calls[0]
    assert "project_id = %s" in sql and "context_sequence DESC" in sql
    assert params == (value.project_id,)
    assert record.context_sequence == 1


def test_matching_retry_returns_existing_and_conflict_is_specific():
    value = context()
    existing = ResearchEvidenceProjectContextRevisionRecord(
        **value.model_dump(), id=uid(), context_sequence=1,
        supersedes_context_revision_id=None, recorded_at=datetime.now(timezone.utc),
    )
    assert repo.ensure_project_context_retry_matches(existing, value) is existing
    with pytest.raises(repo.ResearchEvidencePackRequestConflict):
        repo.ensure_project_context_retry_matches(
            existing, value.model_copy(update={"research_question": "different"})
        )


def test_insert_uses_bounded_savepoint_and_omits_server_fields():
    value = context()
    conn = Conn([Result(), Result(), Result(row=row_for(value)), Result()])
    record = repo.insert_project_context_revision(conn, value)
    statements = [sql for sql, _ in conn.calls]
    assert statements[1].startswith("SAVEPOINT ")
    insert = next(sql for sql in statements if "INSERT INTO" in sql)
    assert "context_sequence" not in insert.split("VALUES")[0]
    assert "recorded_at" not in insert.split("VALUES")[0]
    assert statements[-1].startswith("RELEASE SAVEPOINT ")
    assert record.project_id == value.project_id


def test_project_lock_and_counts_are_project_scoped(monkeypatch):
    project_id = uid(); conn = Conn([Result(row=(project_id,))])
    repo.lock_project(conn, project_id=project_id)
    rows = [
        (SimpleNamespace(claim_draft_id="claim-a"), "source-a"),
        (SimpleNamespace(claim_draft_id="claim-a"), "source-b"),
        (SimpleNamespace(claim_draft_id="claim-b"), "source-b"),
    ]
    monkeypatch.setattr(
        repo, "_effective_usage_authorization_rows",
        lambda *args, **kwargs: rows,
    )
    assert repo.effective_project_pack_member_counts(
        conn, project_id=project_id,
    ) == (2, 2)
    assert conn.calls[0][1] == (project_id,)


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable", "read uncommitted"])
def test_direct_authorization_insert_rejects_unsupported_isolation_before_lock(
    monkeypatch, isolation,
):
    value = authorization()
    conn = Conn([], isolation=isolation)
    monkeypatch.setattr(
        repo, "lock_project",
        lambda *args, **kwargs: pytest.fail("lock must follow isolation validation"),
    )
    monkeypatch.setattr(
        repo, "get_usage_authorization_decision_by_request_id",
        lambda *args, **kwargs: pytest.fail("lookup must follow isolation validation"),
    )
    with pytest.raises(repo.ResearchEvidencePackTransactionError, match="READ COMMITTED"):
        repo.insert_usage_authorization_decision(conn, value)
    assert [sql for sql, _ in conn.calls] == ["SHOW transaction_isolation"]


def test_direct_authorization_insert_accepts_read_committed_before_lock(monkeypatch):
    value = authorization()
    existing = authorization_record(value)
    events = []
    conn = Conn([])
    monkeypatch.setattr(
        repo, "lock_project",
        lambda *args, **kwargs: events.append("lock"),
    )
    monkeypatch.setattr(
        repo, "get_usage_authorization_decision_by_request_id",
        lambda *args, **kwargs: events.append("lookup") or existing,
    )
    assert repo.insert_usage_authorization_decision(conn, value) is existing
    assert conn.calls[0][0] == "SHOW transaction_isolation"
    assert events == ["lock", "lookup"]


def test_effective_authorization_query_has_explicit_canonical_final_order():
    normalized = " ".join(repo._EFFECTIVE_AUTHORIZATION_SELECT.split())
    assert normalized.endswith(
        "ORDER BY d.usage_scope, d.claim_draft_id, "
        "evidence_item.source_snapshot_id, d.decision_sequence, d.id"
    )


def test_assembly_query_freezes_current_scope_fail_closed_sql_contract():
    normalized = " ".join(repo._PACK_ASSEMBLY_SELECT.split())
    required = (
        "d.project_id=%s AND d.usage_scope=%s",
        "d.decision='authorized'",
        "annotation.annotation_sequence=( SELECT max",
        "support.assessment_sequence=( SELECT max",
        "claim_review.decision_sequence=( SELECT max",
        "evidence_review.decision_sequence=( SELECT max",
        "support.locator_resolution='resolvable'",
        "support.evidence_linkage='linked'",
        "support.semantic_relationship IN ('support','qualification')",
        "retention.event_type IN ('tombstone','redact')",
        "successor.supersedes_claim_id=claim.id",
        "replacement.supersedes_candidate_fact_revision_id=fact.id",
        "SELECT DISTINCT ON (claim_draft_id,candidate_fact_revision_id)",
        "ORDER BY claim_draft_id,source_snapshot_id,candidate_fact_revision_id",
        "LIMIT %s",
    )
    assert not [fragment for fragment in required if fragment not in normalized]
    forbidden = (" INSERT ", " UPDATE ", " DELETE ", " FOR UPDATE", "LOCK TABLE")
    padded = f" {normalized} "
    assert not [fragment for fragment in forbidden if fragment in padded]


def test_assembly_returns_typed_empty_pack_without_reading_context():
    project_id = uid()
    conn = Conn([Result(row=(project_id,)), Result(rows=[])])
    pack = repo.assemble_effective_project_pack(
        conn, project_id=project_id, usage_scope="client_report",
    )
    assert pack.project_id == project_id
    assert pack.usage_scope.value == "client_report"
    assert pack.counts.relationship_count == 0
    assert len(conn.calls) == 2
    assert conn.calls[1][1] == (
        project_id, "client_report", MAX_PACK_RELATIONSHIPS + 1,
    )


def test_assembly_rejects_missing_project_and_invalid_scope():
    project_id = uid()
    conn = Conn([Result(row=None)])
    with pytest.raises(repo.ResearchEvidencePackParentNotFound, match="project"):
        repo.assemble_effective_project_pack(
            conn, project_id=project_id, usage_scope="client_report",
        )
    assert len(conn.calls) == 1
    invalid = Conn([])
    with pytest.raises(ValueError):
        repo.assemble_effective_project_pack(
            invalid, project_id=project_id, usage_scope="all_scopes",
        )
    assert invalid.calls == []


def test_assembly_builds_current_context_and_deduplicates_identical_rows():
    project_id = uid()
    row = assembly_row()
    context_value = ResearchEvidenceProjectContextRevisionCreate(
        project_id=project_id, request_id="context", research_question="question",
        project_limitations=[], unresolved_gaps=[], actor="operator",
    )
    conn = Conn([
        Result(row=(project_id,)), Result(rows=[row, row]),
        Result(row=row_for(context_value)),
    ])
    pack = repo.assemble_effective_project_pack(
        conn, project_id=project_id, usage_scope="client_report",
    )
    assert pack.context.research_question == "question"
    assert pack.counts.model_dump() == {
        "source_count": 1, "claim_count": 1, "evidence_count": 1,
        "relationship_count": 1,
    }
    assert pack.claims[0].annotation.annotation_revision_id == row[5]
    assert pack.relationships[0].authorization_decision_id == row[0]
    statements = [sql for sql, _ in conn.calls]
    assert not any(
        token in " ".join(statement.upper().split())
        for statement in statements
        for token in ("INSERT INTO", "UPDATE ", "DELETE FROM", "FOR UPDATE", "LOCK TABLE")
    )


def test_assembly_output_order_is_deterministic_independent_of_row_order():
    project_id = uid()
    low_claim, high_claim = sorted((uid(), uid()))
    low_source, high_source = sorted((uid(), uid()))
    low = assembly_row(claim_id=low_claim, source_id=high_source, sequence=2)
    high = assembly_row(claim_id=high_claim, source_id=low_source, sequence=1)
    context_value = ResearchEvidenceProjectContextRevisionCreate(
        project_id=project_id, request_id="context", research_question="question",
        project_limitations=[], unresolved_gaps=[], actor="operator",
    )
    conn = Conn([
        Result(row=(project_id,)), Result(rows=[high, low]),
        Result(row=row_for(context_value)),
    ])
    pack = repo.assemble_effective_project_pack(
        conn, project_id=project_id, usage_scope="operator_dossier",
    )
    assert [item.claim_draft_id for item in pack.claims] == [low_claim, high_claim]
    assert [item.source_snapshot_id for item in pack.sources] == [low_source, high_source]
    assert [item.claim_draft_id for item in pack.relationships] == [low_claim, high_claim]
    assert {item.usage_scope.value for item in pack.relationships} == {"operator_dossier"}


def test_assembly_conflicting_duplicate_canonical_content_fails_closed():
    project_id = uid()
    fact_id = uid()
    first = assembly_row(fact_id=fact_id)
    conflicting = list(assembly_row(fact_id=fact_id))
    conflicting[28] = "conflicting evidence text"
    conn = Conn([
        Result(row=(project_id,)), Result(rows=[first, tuple(conflicting)]),
    ])
    with pytest.raises(repo.ResearchEvidencePackIntegrityError, match="evidence"):
        repo.assemble_effective_project_pack(
            conn, project_id=project_id, usage_scope="internal_analysis",
        )


@pytest.mark.parametrize(
    ("dimension", "count", "message"),
    [("source", 51, "source"), ("claim", 201, "claim")],
)
def test_assembly_existing_capacity_limits_fail_closed(dimension, count, message):
    project_id = uid()
    template = list(assembly_row())
    rows = []
    for _ in range(count):
        row = list(template)
        row[0] = uid()
        row[3] = uid()
        if dimension == "source":
            row[2] = uid()
            row[7] = uid()
            row[10], row[11], row[12] = uid(), uid(), uid()
            row[24], row[25] = uid(), uid()
        else:
            row[1] = uid()
            row[4], row[5], row[6] = uid(), uid(), uid()
        rows.append(tuple(row))
    conn = Conn([Result(row=(project_id,)), Result(rows=rows)])
    with pytest.raises(repo.ResearchEvidencePackCapacityError, match=message):
        repo.assemble_effective_project_pack(
            conn, project_id=project_id, usage_scope="client_report",
        )


def test_assembly_relationship_cross_product_limit_fails_before_materialization():
    project_id = uid()
    conn = Conn([
        Result(row=(project_id,)),
        Result(rows=[()] * (MAX_PACK_RELATIONSHIPS + 1)),
    ])
    with pytest.raises(repo.ResearchEvidencePackCapacityError, match="relationships"):
        repo.assemble_effective_project_pack(
            conn, project_id=project_id, usage_scope="client_report",
        )
    assert len(conn.calls) == 2


def test_authorization_insert_serializes_then_recovers_known_request_race(monkeypatch):
    value = authorization()
    existing = authorization_record(value)
    events = []

    class TriggerRace(Exception):
        sqlstate = "23514"
        diag = SimpleNamespace(
            sqlstate="23514", constraint_name=None,
            message_primary="usage decisions must alternate authorization and revocation",
        )

    class RaceConn:
        autocommit = False
        def execute(self, sql, params=None):
            events.append(sql)
            if sql == "SHOW transaction_isolation":
                return Result(row=("read committed",))
            if "INSERT INTO research_evidence_usage_authorization_decision" in sql:
                raise TriggerRace()
            return Result()

    lookups = iter((None, existing))
    monkeypatch.setattr(
        repo, "lock_project",
        lambda *args, **kwargs: events.append("PROJECT LOCK"),
    )
    monkeypatch.setattr(
        repo, "get_usage_authorization_decision_by_request_id",
        lambda *args, **kwargs: next(lookups),
    )

    assert repo.insert_usage_authorization_decision(RaceConn(), value) is existing
    assert events[:2] == ["SHOW transaction_isolation", "PROJECT LOCK"]
    assert events[-2:] == [
        "ROLLBACK TO SAVEPOINT research_evidence_pack_authorization_insert",
        "RELEASE SAVEPOINT research_evidence_pack_authorization_insert",
    ]


@pytest.mark.parametrize(
    ("sqlstate", "constraint", "message"),
    [
        ("23505", "uq_reuad_scope_request", "duplicate key"),
        (
            "23514", "",
            "usage decisions must alternate authorization and revocation",
        ),
    ],
)
def test_authorization_allowed_races_resolve_conflicts_and_release_savepoint(
    monkeypatch, sqlstate, constraint, message,
):
    value = authorization()
    existing = authorization_record(
        value.model_copy(update={"reason": "winner payload"}),
    )
    statements = []

    class Race(Exception):
        pass

    error = Race()
    error.sqlstate = sqlstate
    error.diag = SimpleNamespace(
        sqlstate=sqlstate, constraint_name=constraint,
        message_primary=message,
    )

    class RaceConn:
        autocommit = False
        def execute(self, sql, params=None):
            statements.append(sql)
            if sql == "SHOW transaction_isolation":
                return Result(row=("read committed",))
            if "INSERT INTO research_evidence_usage_authorization_decision" in sql:
                raise error
            return Result()

    monkeypatch.setattr(repo, "lock_project", lambda *args, **kwargs: None)
    lookups = iter((None, existing))
    monkeypatch.setattr(
        repo, "get_usage_authorization_decision_by_request_id",
        lambda *args, **kwargs: next(lookups),
    )
    with pytest.raises(repo.ResearchEvidencePackRequestConflict):
        repo.insert_usage_authorization_decision(RaceConn(), value)
    assert statements[-2:] == [
        "ROLLBACK TO SAVEPOINT research_evidence_pack_authorization_insert",
        "RELEASE SAVEPOINT research_evidence_pack_authorization_insert",
    ]
    assert not any(sql in ("COMMIT", "ROLLBACK") for sql in statements)


@pytest.mark.parametrize(
    ("sqlstate", "constraint", "message", "mapped"),
    [
        ("23514", "", "unrelated check", False),
        ("23503", "fk_unrelated", "foreign key", False),
        ("23505", "uq_unrelated", "unrelated unique", False),
        ("", "", "programming failure", False),
    ],
    ids=[
        "23514--unrelated check-True",
        "23503-fk_unrelated-foreign key-True",
        "23505-uq_unrelated-unrelated unique-True",
        "--programming failure-False",
    ],
)
def test_authorization_unrelated_failures_never_become_retry_success(
    monkeypatch, sqlstate, constraint, message, mapped,
):
    value = authorization()
    statements = []

    class Failure(Exception):
        pass

    error = Failure(message)
    error.sqlstate = sqlstate
    error.diag = SimpleNamespace(
        sqlstate=sqlstate, constraint_name=constraint,
        message_primary=message,
    )

    class FailingConn:
        autocommit = False
        def execute(self, sql, params=None):
            statements.append(sql)
            if sql == "SHOW transaction_isolation":
                return Result(row=("read committed",))
            if "INSERT INTO research_evidence_usage_authorization_decision" in sql:
                raise error
            return Result()

    monkeypatch.setattr(repo, "lock_project", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        repo, "get_usage_authorization_decision_by_request_id",
        lambda *args, **kwargs: None,
    )
    expected = repo.ResearchEvidencePackIntegrityError if mapped else Failure
    with pytest.raises(expected):
        repo.insert_usage_authorization_decision(FailingConn(), value)
    assert statements[-2:] == [
        "ROLLBACK TO SAVEPOINT research_evidence_pack_authorization_insert",
        "RELEASE SAVEPOINT research_evidence_pack_authorization_insert",
    ]
    assert not any(sql in ("COMMIT", "ROLLBACK") for sql in statements)


def test_authorization_recovery_lookup_error_still_releases_savepoint(monkeypatch):
    value = authorization()
    statements = []
    lookup_error = RuntimeError("controlled recovery lookup failure")

    class RequestRace(Exception):
        sqlstate = "23505"
        diag = SimpleNamespace(
            sqlstate="23505", constraint_name="uq_reuad_scope_request",
            message_primary="duplicate key",
        )

    class RaceConn:
        autocommit = False
        def execute(self, sql, params=None):
            statements.append(sql)
            if sql == "SHOW transaction_isolation":
                return Result(row=("read committed",))
            if "INSERT INTO research_evidence_usage_authorization_decision" in sql:
                raise RequestRace()
            return Result()

    monkeypatch.setattr(repo, "lock_project", lambda *args, **kwargs: None)
    lookups = iter((None, lookup_error))

    def lookup(*args, **kwargs):
        result = next(lookups)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(repo, "get_usage_authorization_decision_by_request_id", lookup)
    with pytest.raises(RuntimeError) as raised:
        repo.insert_usage_authorization_decision(RaceConn(), value)
    assert raised.value is lookup_error
    assert statements[-2:] == [
        "ROLLBACK TO SAVEPOINT research_evidence_pack_authorization_insert",
        "RELEASE SAVEPOINT research_evidence_pack_authorization_insert",
    ]
    assert not any(sql in ("COMMIT", "ROLLBACK") for sql in statements)


@pytest.mark.parametrize(
    "sqlstate,constraint,message,approved",
    [
        ("23505", "uq_reuad_scope_request", "duplicate key", True),
        ("23505", "uq_wrong", "duplicate key", False),
        ("23503", "uq_reuad_scope_request", "foreign key", False),
        ("23514", "uq_reuad_scope_request", "check violation", False),
        (None, "uq_reuad_scope_request", "missing state", False),
        (None, None, "non-integrity text: uq_reuad_scope_request", False),
    ],
    ids=[
        "exact-approved-pair", "wrong-constraint", "wrong-sqlstate-23503",
        "wrong-sqlstate-23514", "missing-sqlstate", "non-integrity-name-text",
    ],
)
def test_unique_request_recovery_requires_exact_sqlstate_and_constraint_pair(
    monkeypatch, sqlstate, constraint, message, approved,
):
    value = authorization()
    existing = authorization_record(value)
    statements = []
    lookup_calls = []

    class Failure(Exception):
        pass

    error = Failure(message)
    if sqlstate is not None:
        error.sqlstate = sqlstate
    error.diag = SimpleNamespace(
        sqlstate=sqlstate, constraint_name=constraint,
        schema_name=None, table_name=None, message_primary=message,
    )

    class FailingConn:
        autocommit = False

        def execute(self, sql, params=None):
            statements.append(sql)
            if sql == "SHOW transaction_isolation":
                return Result(row=("read committed",))
            if "INSERT INTO research_evidence_usage_authorization_decision" in sql:
                raise error
            return Result()

    monkeypatch.setattr(repo, "lock_project", lambda *args, **kwargs: None)

    def lookup(*args, **kwargs):
        lookup_calls.append((args, kwargs))
        if len(lookup_calls) == 1:
            return None
        if approved:
            return existing
        pytest.fail("unapproved unique exception entered recovery lookup")

    monkeypatch.setattr(repo, "get_usage_authorization_decision_by_request_id", lookup)
    if approved:
        assert repo.insert_usage_authorization_decision(FailingConn(), value) is existing
        assert len(lookup_calls) == 2
    else:
        with pytest.raises(Failure) as raised:
            repo.insert_usage_authorization_decision(FailingConn(), value)
        assert raised.value is error
        assert len(lookup_calls) == 1
    assert statements[-2:] == [
        "ROLLBACK TO SAVEPOINT research_evidence_pack_authorization_insert",
        "RELEASE SAVEPOINT research_evidence_pack_authorization_insert",
    ]
    assert not any(sql in ("COMMIT", "ROLLBACK") for sql in statements)


@pytest.mark.parametrize(
    "sqlstate,constraint,schema_name,table_name,message,approved",
    [
        (
            "23514", None, None, None,
            "usage decisions must alternate authorization and revocation", True,
        ),
        ("23514", None, None, None, "different transition identity", False),
        (
            "23505", None, None, None,
            "usage decisions must alternate authorization and revocation", False,
        ),
        ("23514", "ck_unrelated", "public", "other_table",
         "unrelated check violation", False),
        (
            "23514", "ck_nonapproved_origin", "public", "other_table",
            "usage decisions must alternate authorization and revocation", False,
        ),
    ],
    ids=[
        "genuine-approved-identity", "wrong-message-identity", "wrong-sqlstate",
        "unrelated-check", "matching-message-wrong-structured-origin",
    ],
)
def test_transition_recovery_requires_exact_genuine_diagnostic_identity(
    monkeypatch, sqlstate, constraint, schema_name, table_name, message, approved,
):
    value = authorization()
    existing = authorization_record(value)
    statements = []
    lookup_calls = []

    class Failure(Exception):
        pass

    error = Failure(message)
    error.sqlstate = sqlstate
    error.diag = SimpleNamespace(
        sqlstate=sqlstate, constraint_name=constraint,
        schema_name=schema_name, table_name=table_name,
        message_primary=message,
    )

    class FailingConn:
        autocommit = False

        def execute(self, sql, params=None):
            statements.append(sql)
            if sql == "SHOW transaction_isolation":
                return Result(row=("read committed",))
            if "INSERT INTO research_evidence_usage_authorization_decision" in sql:
                raise error
            return Result()

    monkeypatch.setattr(repo, "lock_project", lambda *args, **kwargs: None)

    def lookup(*args, **kwargs):
        lookup_calls.append((args, kwargs))
        if len(lookup_calls) == 1:
            return None
        if approved:
            return existing
        pytest.fail("unapproved transition exception entered recovery lookup")

    monkeypatch.setattr(repo, "get_usage_authorization_decision_by_request_id", lookup)
    if approved:
        assert repo.insert_usage_authorization_decision(FailingConn(), value) is existing
        assert len(lookup_calls) == 2
    else:
        with pytest.raises(Failure) as raised:
            repo.insert_usage_authorization_decision(FailingConn(), value)
        assert raised.value is error
        assert len(lookup_calls) == 1
    assert statements[-2:] == [
        "ROLLBACK TO SAVEPOINT research_evidence_pack_authorization_insert",
        "RELEASE SAVEPOINT research_evidence_pack_authorization_insert",
    ]
    assert not any(sql in ("COMMIT", "ROLLBACK") for sql in statements)
