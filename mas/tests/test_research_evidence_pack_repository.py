from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from research_evidence import pack_repository as repo
from research_evidence.pack_models import (
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
