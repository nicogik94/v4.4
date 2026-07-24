"""R2.0A-4B — Research Evidence Operator Bridge.

A single bounded operator CLI that lets an authenticated local operator
construct, inspect, authorize, revoke, and preview canonical Research Evidence
using the existing v47 (Slice A) and v51–v61 (Research Evidence) architecture.

Ownership boundaries this tool never crosses:

* A-2 (``research_evidence.pack_service``) owns membership/eligibility. This tool
  never recreates or approximates the authorization eligibility rules; it calls
  the canonical service and lets it decide.
* A-3 (``research_evidence.presentation_projection_service``) owns disclosure.
* A-4A (``research_evidence_context``) owns consumption + the frozen 65536-byte
  model-facing budget and its exact renderer, which this tool reuses read-only.

Safety posture:

* every write requires ``MAS_RESEARCH_EVIDENCE_ENABLED=true`` (and
  ``MAS_EVIDENCE_SNAPSHOT_ENABLED=true`` when it touches v47 capture/facts);
* every write runs on ONE caller-owned, non-autocommit, READ COMMITTED
  transaction and defaults to rollback — it persists only with ``--commit``;
* ``usage_scope`` is mechanically fixed to ``internal_analysis`` — there is no
  operator-selectable usage scope anywhere in this tool;
* authorization/revocation additionally require a typed confirmation echoing the
  exact project, claim, and evidence identities;
* previews open a read-only connection, never commit, and never truncate;
* the tool never prints DSNs, secrets, storage paths, private content, or raw
  payloads.

The tool creates no parallel evidence system, adds no migration (v62 stays
unused), and performs no raw SQL writes as a production contract — every write
goes through an existing validated service (or, for the bare
``candidate_fact_revision``, the bounded
``knowledge.evidence_snapshot.fact_service`` wrapper documented in the wave).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

# Direct-execution bootstrap. Under `python mas/tools/research_evidence_bridge.py`
# Python puts `mas/tools` — not `mas` — at sys.path[0], so the application
# imports below would fail before argparse ever runs. Add the repository's `mas`
# root the same bounded way the other repository tools do (``cdp_review``,
# ``validate_t1a_gate2``): a path derived from this file, never an
# environment-specific absolute path, and no weakening of the imports it enables.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402

# ─────────────────────────── exit codes ────────────────────────────

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

# The tool fixes internal_analysis mechanically. There is no code path that
# accepts a caller-selected usage scope.
FIXED_USAGE_SCOPE_VALUE = "internal_analysis"

# ─────────────────────────── catalog manifest ───────────────────────────
#
# The bounded, closed catalog the bridge's write paths depend on. It is NOT a
# reproduction of the schema mega-suites — it is the smallest set of catalog
# facts sufficient to prevent a false-positive "ready" verdict for the bridge's
# writes: exact relation names + relkind, exact trigger name/relation/function/
# type/enabled-state, exact function name/signature, the load-bearing request-id
# UNIQUE constraints the write paths' idempotency depends on, the v59/v60
# topology security posture, and the connection namespace. Every main-schema check is
# resolved against ``current_schema`` so an unqualified decoy schema cannot
# satisfy the manifest. Values were captured from the canonical v47/v51→v61
# topology; no new migration is introduced (v62 stays unused).

# Dedicated schema that v59/v60 live in.
CATALOG_DEDICATED_SCHEMA = "research_evidence_automation_roi"

# Main-schema relations the bridge writes or depends on → expected relkind.
CATALOG_MAIN_RELATIONS: dict[str, str] = {name: "r" for name in (
    # parent + v47 source-capture the fact path binds to
    "projects", "source_blob", "source_snapshot", "candidate_fact_revision",
    "evidence_retention_event", "ingest_operation",
    # v51 sidecar + event ledger
    "research_source_metadata_revision", "research_fact_metadata_revision",
    "research_claim_draft", "research_evidence_event",
    # v52 event sequence
    "research_evidence_event_sequence_allocator",
    # v53 intake
    "research_evidence_intake", "research_evidence_intake_item",
    # v54 review
    "research_evidence_intake_item_review_decision",
    "research_evidence_item_review_sequence_allocator",
    # v55 freshness
    "research_evidence_intake_item_freshness_assessment",
    "research_evidence_item_freshness_sequence_allocator",
    # v56 claim support
    "research_evidence_claim_support_assessment",
    "research_evidence_claim_support_sequence_allocator",
    # v57 consumer-input binding (pack dependency)
    "research_evidence_consumer_input_binding",
    "research_evidence_consumer_input_binding_sequence_allocator",
    # v58 scenario-input evaluation (pack dependency)
    "research_evidence_scenario_input_manifest",
    "research_evidence_scenario_input_manifest_item",
    "research_evidence_scenario_input_evaluation",
    "research_evidence_scenario_input_evaluation_input",
    "research_evidence_scenario_input_evaluation_sequence_allocator",
    # v61 pack ledgers the bridge authorizes/annotates/contextualizes
    "research_evidence_claim_annotation_revision",
    "research_evidence_claim_annotation_sequence_allocator",
    "research_evidence_project_context_revision",
    "research_evidence_project_context_sequence_allocator",
    "research_evidence_usage_authorization_decision",
    "research_evidence_usage_authorization_sequence_allocator",
)}

# Dedicated v59/v60 relations → expected relkind.
CATALOG_DEDICATED_RELATIONS: dict[str, str] = {name: "r" for name in (
    "research_evidence_automation_roi_input_snapshot",
    "automation_roi_input_snapshot_sequence_allocator",
    "research_evidence_automation_roi_input_snapshot_binding",
    "automation_roi_calculation_result",
)}

# ─────────────────────── canonical trigger-function semantics ───────────────────────
# Identity alone is forgeable: `CREATE OR REPLACE FUNCTION slicea_reject_mutation()
# RETURNS trigger ... BEGIN RETURN NEW; END` preserves schema, name, identity
# arguments and result type while neutralising the append-only guard. So every
# trigger function the bridge's write boundary depends on is frozen by its
# BEHAVIOUR as well as its identity: language, kind, set-returning state,
# defaults, variadic state, security-definer state, volatility, strictness,
# parallel mode, proconfig (fixed search_path where canonical) and a body
# fingerprint (`md5(prosrc)`), plus an exact overload count.
#
# These values are NOT uniform — `slicea_reject_mutation` (v47) is a plain
# SECURITY INVOKER function with no proconfig, while the v52–v61 prepare/validate
# functions are SECURITY DEFINER with a fixed `search_path=pg_catalog`. They were
# captured from a clean disposable full-topology introspection (v47 + v51→v61)
# and cross-checked against the ratified migration definitions; a PG test
# re-derives them from a clean topology so the manifest cannot silently drift.
#
# `proowner` is deliberately NOT frozen: under a clean apply the v47 function is
# owned by `workflow_migration_owner` while the v52–v61 functions are owned by
# the bootstrapping superuser, so ownership reflects the migration application
# path rather than a ratified invariant. Ownership of the *dedicated schema* IS
# checked, in `_topology_security_findings`.
#
# This is a bounded write-boundary closure, NOT a general migration validator:
# only the trigger functions above are covered.
_SECURITY_DEFINER_PG_CATALOG: tuple = (True, ("search_path=pg_catalog",))
_SECURITY_INVOKER_NO_CONFIG: tuple = (False, None)

CATALOG_FUNCTIONS: dict[str, dict] = {
    name: {
        # identity
        "args": "", "result": "trigger", "rettype": "trigger", "pronargs": 0,
        # kind / shape
        "language": "plpgsql", "prokind": "f", "proretset": False,
        "pronargdefaults": 0, "provariadic": 0,
        # execution semantics
        "prosecdef": secdef, "proconfig": proconfig,
        "provolatile": "v", "proisstrict": False, "proparallel": "u",
        # behaviour
        "prosrc_md5": prosrc_md5,
        # exactly one function may carry this protected name
        "overloads": 1,
    }
    for name, (secdef, proconfig), prosrc_md5 in (
        ("slicea_reject_mutation",
         _SECURITY_INVOKER_NO_CONFIG, "db1789cb02a71f58b608dde5d8e51326"),
        ("research_evidence_prepare_event_insert",
         _SECURITY_DEFINER_PG_CATALOG, "366022c157d083b735094a00428461b8"),
        ("research_evidence_intake_validate_item_snapshot",
         _SECURITY_DEFINER_PG_CATALOG, "4c7aa1824cbdba4432ea304312360d9e"),
        ("research_evidence_prepare_item_review_insert",
         _SECURITY_DEFINER_PG_CATALOG, "a9732a129a8b761445c5ce87398ea37d"),
        ("research_evidence_prepare_freshness_assessment_insert",
         _SECURITY_DEFINER_PG_CATALOG, "e284a9ed3b08896d78e3a85e6d3e3bf8"),
        ("research_evidence_prepare_claim_support_insert",
         _SECURITY_DEFINER_PG_CATALOG, "eb6192b7d739d3fb568f3620e97639ce"),
        ("research_evidence_prepare_project_context_insert",
         _SECURITY_DEFINER_PG_CATALOG, "8badc5b2d4f5b588af8c01785c58d252"),
        ("research_evidence_prepare_claim_annotation_insert",
         _SECURITY_DEFINER_PG_CATALOG, "da31e5238de34c616edc4519ebe31660"),
        ("research_evidence_prepare_usage_authorization_insert",
         _SECURITY_DEFINER_PG_CATALOG, "5fe986d5b76e21b281dd0f94385325af"),
    )
}

# Write-boundary triggers on every ledger the bridge writes:
# (relation, trigger name, function, tgtype, tgenabled). The append-only mutation
# guard (`*_no_mutation` → slicea_reject_mutation, BEFORE UPDATE|DELETE row =
# tgtype 27) plus each ledger's preparatory / validation trigger (BEFORE INSERT
# row = tgtype 7). tgenabled is the exact captured origin ('O') / always ('A').
CATALOG_TRIGGERS: tuple[tuple[str, str, str, int, str], ...] = (
    ("candidate_fact_revision", "trg_cfr_no_mutation", "slicea_reject_mutation", 27, "O"),
    ("research_source_metadata_revision", "trg_rsmr_no_mutation", "slicea_reject_mutation", 27, "O"),
    ("research_fact_metadata_revision", "trg_rfmr_no_mutation", "slicea_reject_mutation", 27, "O"),
    ("research_claim_draft", "trg_rcd_no_mutation", "slicea_reject_mutation", 27, "O"),
    ("research_evidence_event", "trg_ree_no_mutation", "slicea_reject_mutation", 27, "O"),
    ("research_evidence_event", "trg_ree_prepare_insert", "research_evidence_prepare_event_insert", 7, "A"),
    ("research_evidence_intake", "trg_rei_no_mutation", "slicea_reject_mutation", 27, "O"),
    ("research_evidence_intake_item", "trg_reii_no_mutation", "slicea_reject_mutation", 27, "O"),
    ("research_evidence_intake_item", "trg_reii_validate_snapshot", "research_evidence_intake_validate_item_snapshot", 7, "O"),
    ("research_evidence_intake_item_review_decision", "trg_reird_no_mutation", "slicea_reject_mutation", 27, "O"),
    ("research_evidence_intake_item_review_decision", "trg_reird_prepare_insert", "research_evidence_prepare_item_review_insert", 7, "A"),
    ("research_evidence_intake_item_freshness_assessment", "trg_reifa_no_mutation", "slicea_reject_mutation", 27, "O"),
    ("research_evidence_intake_item_freshness_assessment", "trg_reifa_prepare_insert", "research_evidence_prepare_freshness_assessment_insert", 7, "A"),
    ("research_evidence_claim_support_assessment", "trg_recsa_no_mutation", "slicea_reject_mutation", 27, "O"),
    ("research_evidence_claim_support_assessment", "trg_recsa_prepare_insert", "research_evidence_prepare_claim_support_insert", 7, "A"),
    ("research_evidence_project_context_revision", "trg_repcr_no_mutation", "slicea_reject_mutation", 27, "A"),
    ("research_evidence_project_context_revision", "trg_repcr_prepare_insert", "research_evidence_prepare_project_context_insert", 7, "A"),
    ("research_evidence_claim_annotation_revision", "trg_recar_no_mutation", "slicea_reject_mutation", 27, "A"),
    ("research_evidence_claim_annotation_revision", "trg_recar_prepare_insert", "research_evidence_prepare_claim_annotation_insert", 7, "A"),
    ("research_evidence_usage_authorization_decision", "trg_reuad_no_mutation", "slicea_reject_mutation", 27, "A"),
    ("research_evidence_usage_authorization_decision", "trg_reuad_prepare_insert", "research_evidence_prepare_usage_authorization_insert", 7, "A"),
)

# ────────────── load-bearing request-id uniqueness (idempotency) ──────────────
#
# Every request-id-bearing bridge write documents an idempotent retry: re-running
# the same command with the same `--request-id` must return the existing record
# rather than append a second one. That promise is NOT enforced by the pre-read
# the repositories perform first — at READ COMMITTED two concurrent callers can
# both observe "no such request", and the ledgers' prepare triggers serialise on
# the *sequence allocator*, which happily hands out two distinct sequences. The
# only thing that makes the retry contract hold under concurrency is the UNIQUE
# constraint: the loser's INSERT raises 23505 naming the constraint, and the
# repository recovers the winner's row from that specific violation (see e.g.
# ``review_repository.insert_decision`` keying recovery on
# ``uq_reird_item_request``). Drop or weaken the constraint and the recovery arm
# is simply never reached — two rows commit for one request_id, silently.
#
# So the write preflight freezes those constraints as catalog facts. This is
# deliberately NOT every CHECK/FK/index in v47–v61: only the request-id
# uniqueness the bridge's own write surface depends on. Names and ordered columns
# were derived from the ratified migrations (v54, v55, v56, v61).
#
# (relation, constraint name, exact ordered conkey columns)
CATALOG_CONSTRAINTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # v54 review decision
    ("research_evidence_intake_item_review_decision", "uq_reird_item_request",
     ("project_id", "research_evidence_intake_item_id", "request_id")),
    # v55 freshness assessment
    ("research_evidence_intake_item_freshness_assessment", "uq_reifa_item_request",
     ("project_id", "research_evidence_intake_item_id", "request_id")),
    # v56 claim-support assessment
    ("research_evidence_claim_support_assessment", "uq_recsa_pair_request",
     ("project_id", "claim_intake_item_id", "evidence_intake_item_id",
      "request_id")),
    # v61 project context revision
    ("research_evidence_project_context_revision", "uq_repcr_project_request",
     ("project_id", "request_id")),
    # v61 claim annotation revision
    ("research_evidence_claim_annotation_revision", "uq_recar_claim_request",
     ("project_id", "claim_draft_id", "request_id")),
    # v61 usage authorization decision
    ("research_evidence_usage_authorization_decision", "uq_reuad_scope_request",
     ("project_id", "claim_intake_item_id", "evidence_intake_item_id",
      "usage_scope", "request_id")),
)

# Topology roles the disposable/production cluster is expected to carry, with the
# EXACT attributes from the ratified R2 external-role manifest. A role is "ready"
# only when it exists AND every relevant attribute matches — a mere name match is
# insufficient. Absence or divergence blocks writes but still permits a
# diagnostic report.
REQUIRED_ROLE_ATTRIBUTES: dict[str, dict[str, bool]] = {
    "workflow_research_evidence_owner": {
        "login": False, "inherit": False, "superuser": False,
        "createdb": False, "createrole": False, "replication": False,
        "bypassrls": False,
    },
    "workflow_migration_owner": {
        "login": True, "inherit": False, "superuser": False,
        "createdb": False, "createrole": False, "replication": False,
        "bypassrls": False,
    },
    "workflow_automation_roi_runtime": {
        "login": True, "inherit": False, "superuser": False,
        "createdb": False, "createrole": False, "replication": False,
        "bypassrls": False,
    },
}
REQUIRED_ROLES: tuple[str, ...] = tuple(REQUIRED_ROLE_ATTRIBUTES)


class BridgeError(RuntimeError):
    """An operator-visible, non-secret bridge failure."""


class BridgeConfirmationError(BridgeError):
    """The typed authorization/revocation confirmation did not match."""


class BridgePreflightError(BridgeError):
    """Preflight prerequisites are missing and block a write."""


# ─────────────────────────── connection seam ───────────────────────────


def _open_authoritative_connection():
    """Open the authoritative MAS PostgreSQL connection (injectable seam)."""
    import psycopg

    return psycopg.connect(config.DATABASE_URL)


# Tests replace this with a callable returning a connection to a disposable
# database; production uses the authoritative DATABASE_URL. It is the ONLY place
# the tool obtains a connection.
open_bridge_connection: Callable[[], Any] = _open_authoritative_connection


def _configure_write_connection(conn):
    """Pin a caller-owned, non-autocommit, READ COMMITTED write transaction.

    A failure to pin READ COMMITTED is NOT swallowed: it propagates so the write
    path rolls back and returns non-zero rather than proceeding at an unknown
    isolation level. The pinned level is independently re-verified with
    ``SHOW transaction_isolation`` in :func:`_verify_read_committed`.
    """
    import psycopg

    conn.autocommit = False
    conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
    return conn


def _configure_readonly_connection(conn):
    """Pin a demonstrably read-only, no-commit preview connection."""
    conn.autocommit = False
    conn.read_only = True
    return conn


def _safe_rollback_close(conn) -> None:
    try:
        conn.rollback()
    except Exception:  # pragma: no cover - best effort
        pass
    try:
        conn.close()
    except Exception:  # pragma: no cover - best effort
        pass


# ─────────────────────────── JSON output ───────────────────────────


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"unserializable value of type {type(value).__name__}")


def _emit(payload: dict, stream) -> None:
    stream.write(
        json.dumps(payload, default=_json_default, sort_keys=True, indent=2)
    )
    stream.write("\n")


def _base_payload(command: str, **extra: Any) -> dict:
    payload = {
        "command": command,
        "status": "ok",
        "dry_run": None,
        "committed": False,
    }
    payload.update(extra)
    return payload


# ─────────────────────────── feature gates ───────────────────────────


def _require_research_evidence_enabled() -> None:
    if not config.research_evidence_enabled():
        raise BridgeError(
            "Research Evidence is disabled "
            "(set MAS_RESEARCH_EVIDENCE_ENABLED=true to enable writes)"
        )


def _require_evidence_snapshot_enabled() -> None:
    if not config.evidence_snapshot_enabled():
        raise BridgeError(
            "Evidence snapshot capture is disabled "
            "(set MAS_EVIDENCE_SNAPSHOT_ENABLED=true to create v47 facts)"
        )


# ─────────────────────────── introspection ───────────────────────────


def _current_schema(conn) -> str:
    """The effective creation/resolution schema (first existing on search_path)."""
    row = conn.execute("SELECT current_schema").fetchone()
    return (row[0] or "") if row and row[0] else ""


def _relation_relkind(conn, schema: str, name: str) -> Optional[str]:
    """Return the relkind of ``schema.name`` (schema-qualified), or ``None``."""
    row = conn.execute(
        """
        SELECT c.relkind
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s
        """,
        (schema, name),
    ).fetchone()
    return row[0] if row else None


def _trigger_state(conn, schema: str, relation: str, tgname: str) -> Optional[dict]:
    """Full semantic state of one trigger on ``schema.relation``, or ``None``.

    Includes the bound function's *schema* + identity (so a same-name function in
    a different schema cannot satisfy the manifest) and the complete trigger
    semantics (arguments, column list, WHEN qual, deferrability, constraint
    identity) so a semantically-drifted recreation cannot pass on name alone.
    """
    row = conn.execute(
        """
        SELECT fn_ns.nspname, fn.proname,
               pg_get_function_identity_arguments(fn.oid),
               pg_get_function_result(fn.oid),
               t.tgtype, t.tgenabled, t.tgisinternal,
               t.tgnargs, COALESCE(octet_length(t.tgargs), 0),
               t.tgattr::text, (t.tgqual IS NULL),
               t.tgdeferrable, t.tginitdeferred, t.tgconstraint,
               t.tgfoid
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc fn ON fn.oid = t.tgfoid
        JOIN pg_namespace fn_ns ON fn_ns.oid = fn.pronamespace
        WHERE n.nspname = %s AND c.relname = %s AND t.tgname = %s
        """,
        (schema, relation, tgname),
    ).fetchone()
    if row is None:
        return None
    return {
        "func_schema": row[0], "func_name": row[1], "func_args": row[2],
        "func_result": row[3], "tgtype": int(row[4]), "tgenabled": row[5],
        "tgisinternal": bool(row[6]), "tgnargs": int(row[7]),
        "tgargs_len": int(row[8]), "tgattr": row[9] or "",
        "tgqual_null": bool(row[10]), "tgdeferrable": bool(row[11]),
        "tginitdeferred": bool(row[12]), "tgconstraint": int(row[13]),
        "tgfoid": int(row[14]),
    }


def _ledger_trigger_names(conn, schema: str, relation: str) -> set:
    """All non-internal trigger names on ``schema.relation`` (for inventory)."""
    return {
        r[0]
        for r in conn.execute(
            """
            SELECT t.tgname FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s AND NOT t.tgisinternal
            """,
            (schema, relation),
        ).fetchall()
    }


def _trigger_problem(
    state, *, expected_func, expected_tgtype, expected_tgenabled, cur_schema,
    certified_oid=None
) -> Optional[str]:
    """First drift reason for a trigger state against the manifest, or ``None``.

    Enforces the canonical v61 trigger exactness: the function must be the
    canonical one *in current_schema* with the exact identity args + result,
    plus tgtype, enabled state, not-internal, and frozen trigger semantics
    (tgnargs=0, empty tgargs, empty tgattr, NULL tgqual, not deferrable, not
    initially deferred, no constraint identity).

    ``certified_oid`` is the OID returned by :func:`_certify_function` for
    ``expected_func``. The trigger must bind to *that exact certified function*
    (``tgfoid``), so a same-schema/name/signature replacement whose body or
    execution semantics drifted cannot satisfy the manifest even though its
    identity is unchanged. ``None`` means the function failed certification, in
    which case the trigger fails closed too.
    """
    if state is None:
        return "missing"
    if certified_oid is None:
        return "func_not_certified"
    for failed, reason in (
        # Descriptive identity reasons first, so a cross-schema or wrong-name
        # binding still reports the specific fact that diverged...
        (state["func_name"] != expected_func, "func_name"),
        (state["func_schema"] != cur_schema, "func_schema"),
        (state["func_args"] != "", "func_args"),
        (state["func_result"] != "trigger", "func_result"),
        # ...then the authoritative binding: the trigger must point at the exact
        # certified function OID, not merely at something that looks like it.
        (state["tgfoid"] != certified_oid, "func_identity"),
        (state["tgtype"] != expected_tgtype, "tgtype"),
        (state["tgenabled"] != expected_tgenabled, "tgenabled"),
        (state["tgisinternal"], "internal"),
        (state["tgnargs"] != 0, "tgnargs"),
        (state["tgargs_len"] != 0, "tgargs"),
        (state["tgattr"] != "", "tgattr"),
        (not state["tgqual_null"], "tgqual"),
        (state["tgdeferrable"], "tgdeferrable"),
        (state["tginitdeferred"], "tginitdeferred"),
        (state["tgconstraint"] != 0, "tgconstraint"),
    ):
        if failed:
            return reason
    return None


def _function_semantics(conn, schema: str, name: str) -> list[dict]:
    """Complete bounded semantics of EVERY ``schema.name`` overload.

    Returns one dict per overload (so overload drift is visible), each carrying
    the function's identity, kind, execution semantics and body fingerprint.
    """
    rows = conn.execute(
        """
        SELECT p.oid,
               pg_get_function_identity_arguments(p.oid),
               pg_get_function_result(p.oid),
               p.prorettype::regtype::text, p.pronargs,
               l.lanname, p.prokind, p.proretset, p.pronargdefaults,
               p.provariadic, p.prosecdef, p.provolatile, p.proisstrict,
               p.proparallel, p.proconfig, md5(p.prosrc)
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_language l ON l.oid = p.prolang
        WHERE n.nspname = %s AND p.proname = %s
        ORDER BY p.oid
        """,
        (schema, name),
    ).fetchall()
    return [
        {
            "oid": int(r[0]), "args": r[1], "result": r[2], "rettype": r[3],
            "pronargs": int(r[4]), "language": r[5], "prokind": r[6],
            "proretset": bool(r[7]), "pronargdefaults": int(r[8]),
            "provariadic": int(r[9]), "prosecdef": bool(r[10]),
            "provolatile": r[11], "proisstrict": bool(r[12]),
            "proparallel": r[13],
            "proconfig": tuple(r[14]) if r[14] is not None else None,
            "prosrc_md5": r[15],
        }
        for r in rows
    ]


# Semantic properties compared one-for-one against the frozen manifest. Ordered
# so the reported reason names the FIRST divergence.
_FUNCTION_SEMANTIC_KEYS: tuple[str, ...] = (
    "args", "result", "rettype", "pronargs", "language", "prokind",
    "proretset", "pronargdefaults", "provariadic", "prosecdef", "proconfig",
    "provolatile", "proisstrict", "proparallel", "prosrc_md5",
)


def _certify_function(conn, schema: str, name: str) -> tuple[Optional[str], Optional[int]]:
    """Certify one protected trigger function against the frozen manifest.

    Returns ``(problem, oid)``: ``problem`` is ``None`` and ``oid`` is the
    canonical function's OID only when the function exists exactly once in
    ``schema`` AND every frozen semantic property matches. A same-schema,
    same-name, same-signature replacement whose BODY changed fails on
    ``prosrc_md5``; an extra overload fails on ``overloads``.
    """
    expected = CATALOG_FUNCTIONS[name]
    found = _function_semantics(conn, schema, name)
    if not found:
        return "missing", None
    if len(found) != expected["overloads"]:
        return f"overloads={len(found)}", None
    state = found[0]
    for key in _FUNCTION_SEMANTIC_KEYS:
        if state[key] != expected[key]:
            return key, None
    return None, state["oid"]


def _constraint_state(conn, schema: str, relation: str, conname: str) -> Optional[dict]:
    """Complete bounded state of one named constraint on ``schema.relation``.

    Resolved by (namespace, relation, constraint name) so a same-named constraint
    on a *different* relation or in a decoy schema cannot satisfy the manifest —
    it simply is not found. The backing index is joined in (``LEFT``, so a
    constraint carrying no index still yields a row with null index facts) because
    a UNIQUE constraint's enforcement lives in that index: an invalid, not-ready,
    non-live, non-immediate, expression or partial index does not enforce the
    uniqueness the retry contract assumes.
    """
    row = conn.execute(
        """
        SELECT con.contype, con.convalidated, con.condeferrable, con.condeferred,
               con.conrelid, con.conindid,
               (
                   SELECT array_agg(a.attname::text ORDER BY k.ord)
                   FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                   JOIN pg_attribute a
                     ON a.attrelid = con.conrelid AND a.attnum = k.attnum
               ),
               i.indrelid, i.indisunique, i.indisvalid, i.indisready,
               i.indislive, i.indimmediate,
               (i.indexprs IS NOT NULL), (i.indpred IS NOT NULL)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_index i ON i.indexrelid = con.conindid
        WHERE n.nspname = %s AND c.relname = %s AND con.conname = %s
        """,
        (schema, relation, conname),
    ).fetchone()
    if row is None:
        return None
    return {
        "contype": row[0],
        "convalidated": bool(row[1]),
        "condeferrable": bool(row[2]),
        "condeferred": bool(row[3]),
        "conrelid": int(row[4]),
        "conindid": int(row[5]),
        "columns": tuple(row[6]) if row[6] is not None else (),
        "index_relation": None if row[7] is None else int(row[7]),
        "index_unique": None if row[8] is None else bool(row[8]),
        "index_valid": None if row[9] is None else bool(row[9]),
        "index_ready": None if row[10] is None else bool(row[10]),
        "index_live": None if row[11] is None else bool(row[11]),
        "index_immediate": None if row[12] is None else bool(row[12]),
        "index_has_expressions": None if row[13] is None else bool(row[13]),
        "index_has_predicate": None if row[14] is None else bool(row[14]),
    }


def _constraint_problem(state, *, expected_columns: tuple) -> Optional[str]:
    """First drift reason for a request-id UNIQUE constraint, or ``None``.

    Ordered so the reported reason names the FIRST divergence. ``deferrable`` is
    a real defect and not pedantry: a DEFERRABLE constraint reports its violation
    at COMMIT rather than at the INSERT, so the repository's per-statement
    ``except`` around the savepoint never sees the 23505 it recovers from — the
    duplicate surfaces as a failed transaction instead of an idempotent retry.
    Column ORDER is frozen as ratified catalog exactness (it is also the backing
    index's column order); it is not a claim that a permuted UNIQUE would enforce
    a different set.
    """
    if state is None:
        return "missing"
    for failed, reason in (
        (state["contype"] != "u", "contype"),
        (state["columns"] != tuple(expected_columns), "columns"),
        (not state["convalidated"], "not_validated"),
        (state["condeferrable"], "deferrable"),
        (state["condeferred"], "initially_deferred"),
    ):
        if failed:
            return reason
    # Beyond this point the backing index must exist for its facts to be read.
    if state["conindid"] == 0 or state["index_relation"] is None:
        return "index_missing"
    for failed, reason in (
        (state["index_relation"] != state["conrelid"], "index_relation"),
        (not state["index_unique"], "index_not_unique"),
        (not state["index_valid"], "index_not_valid"),
        (not state["index_ready"], "index_not_ready"),
        (not state["index_live"], "index_not_live"),
        (not state["index_immediate"], "index_not_immediate"),
        (state["index_has_expressions"], "index_expression"),
        (state["index_has_predicate"], "index_partial"),
    ):
        if failed:
            return reason
    return None


def _role_attributes(conn, name: str) -> Optional[dict]:
    """Return the relevant attributes of a role, or ``None`` if it is absent."""
    row = conn.execute(
        """
        SELECT rolcanlogin, rolinherit, rolsuper, rolcreatedb,
               rolcreaterole, rolreplication, rolbypassrls
        FROM pg_roles WHERE rolname = %s LIMIT 1
        """,
        (name,),
    ).fetchone()
    if row is None:
        return None
    return {
        "login": bool(row[0]), "inherit": bool(row[1]), "superuser": bool(row[2]),
        "createdb": bool(row[3]), "createrole": bool(row[4]),
        "replication": bool(row[5]), "bypassrls": bool(row[6]),
    }


def _role_ready(conn, name: str) -> bool:
    """A role is ready only when present AND its attributes match exactly."""
    return _role_attributes(conn, name) == REQUIRED_ROLE_ATTRIBUTES[name]


def _current_database(conn) -> str:
    return conn.execute("SELECT current_database()").fetchone()[0]


def _runtime_identity(conn) -> dict:
    """Read the bounded, non-secret runtime identity of the live connection.

    Includes ``current_schema`` so the fingerprint is bound to the schema writes
    actually resolve into — an operator who repoints search_path at a decoy gets
    a different fingerprint.

    Also includes a stable *cluster* discriminator that survives a Unix-domain
    socket connection. Over a socket ``inet_server_addr()`` and
    ``inet_server_port()`` are both NULL (there is no TCP peer), so on the
    socket-backed topology this project runs on, ``database``/``user``/``schema``
    alone cannot tell two distinct local clusters apart — two clusters sharing
    those three values would collide. ``pg_control_system().system_identifier``
    (the control-file cluster identity fixed at initdb) plus the configured
    ``port`` GUC restore that discrimination. None of these fields is a secret: no
    DSN, credential, connection option, or data directory is ever read.

    Fails closed: if the live role cannot supply the cluster's
    ``system_identifier`` (e.g. it cannot execute ``pg_control_system()``), this
    raises :class:`BridgePreflightError` rather than silently degrading to the
    collision-prone address/port-only identity.
    """
    try:
        row = conn.execute(
            """
            SELECT current_database(),
                   COALESCE(inet_server_addr()::text, ''),
                   COALESCE(inet_server_port()::text, ''),
                   COALESCE(current_setting('port', true), ''),
                   (SELECT system_identifier::text
                      FROM pg_catalog.pg_control_system()),
                   current_user,
                   COALESCE(current_schema, '')
            """
        ).fetchone()
    except Exception as exc:
        raise BridgePreflightError(
            "runtime cluster identity is unavailable "
            f"({type(exc).__name__}); refusing to fingerprint an ambiguous "
            "runtime"
        ) from exc
    system_identifier = (str(row[4]) if row and row[4] is not None else "").strip()
    if not system_identifier:
        raise BridgePreflightError(
            "runtime cluster identity is unavailable (system_identifier missing); "
            "refusing to fingerprint an ambiguous runtime"
        )
    return {
        "current_database": row[0] or "",
        "inet_server_addr": row[1] or "",
        "inet_server_port": row[2] or "",
        "configured_port": row[3] or "",
        "system_identifier": system_identifier,
        "current_user": row[5] or "",
        "current_schema": row[6] or "",
    }


def _runtime_fingerprint(conn) -> str:
    """A bounded fingerprint over the non-secret runtime identity (never a DSN).

    Hashes ALL identity dimensions deterministically, including the socket-safe
    cluster discriminators (``configured_port`` and ``system_identifier``) so two
    socket-backed clusters sharing database/user/schema still fingerprint apart.
    """
    identity = _runtime_identity(conn)
    canonical = "|".join(
        (
            identity["current_database"],
            identity["inet_server_addr"],
            identity["inet_server_port"],
            identity["configured_port"],
            identity["system_identifier"],
            identity["current_user"],
            identity["current_schema"],
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verify_read_committed(conn) -> None:
    """Verify (never assume) the live transaction runs at READ COMMITTED."""
    row = conn.execute("SHOW transaction_isolation").fetchone()
    level = (row[0] if row else "").strip().lower()
    if level != "read committed":
        raise BridgePreflightError(
            f"write transaction isolation must be READ COMMITTED (got {level!r})"
        )


def _project_exists(conn, project_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM projects WHERE id = %s LIMIT 1", (project_id,)
    ).fetchone()
    return row is not None


def _snapshot_summary(conn, *, project_id: str, source_snapshot_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT snapshot.id::text, snapshot.source_kind,
               metadata.citation_label, metadata.canonical_source_locator,
               metadata.declared_quality_tier, metadata.publisher
        FROM source_snapshot snapshot
        LEFT JOIN research_source_metadata_revision metadata
          ON metadata.source_snapshot_id = snapshot.id
         AND metadata.project_id = snapshot.project_id
        WHERE snapshot.id = %s AND snapshot.project_id = %s
        ORDER BY metadata.created_at DESC NULLS LAST
        LIMIT 1
        """,
        (source_snapshot_id, project_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "source_snapshot_id": row[0],
        "source_kind": row[1] or "",
        "citation_label": row[2] or "",
        "canonical_source_locator": row[3] or "",
        "declared_quality_tier": row[4] or "",
        "publisher": row[5] or "",
    }


# ─────────────────────────── typed argument helpers ───────────────────────────


def _require_arg(value: Optional[str], name: str) -> str:
    if value is None or str(value).strip() == "":
        raise BridgeError(f"{name} is required")
    return str(value)


def _parse_aware_datetime(value: str, name: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise BridgeError(f"{name} must be ISO-8601 (got {value!r})") from exc
    if parsed.tzinfo is None:
        raise BridgeError(f"{name} must include a timezone offset")
    return parsed


def _parse_string_array(value: Optional[str]) -> tuple[str, ...]:
    """Parse a JSON array or a newline/`||`-delimited string into a tuple."""
    if value is None or value.strip() == "":
        return ()
    text = value.strip()
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BridgeError(f"invalid JSON array: {exc}") from exc
        if not isinstance(data, list):
            raise BridgeError("expected a JSON array")
        return tuple(str(item) for item in data)
    return tuple(part.strip() for part in text.split("||") if part.strip())


# ─────────────────────────── fail-closed write preflight ───────────────────────────


def _require_configured_database_url() -> None:
    """A write requires an explicitly configured DATABASE_URL env var.

    ``config.DATABASE_URL`` carries a localhost fallback default; a write must
    never target that fallback silently. Only an explicit ``DATABASE_URL``
    environment variable authorizes a write. This is checked *before* any
    connection is opened.
    """
    if not os.getenv("DATABASE_URL", "").strip():
        raise BridgePreflightError(
            "writes require an explicitly configured DATABASE_URL environment "
            "variable (the localhost config fallback is not accepted for writes)"
        )


def _topology_security_findings(conn) -> list[str]:
    """Verify the v59/v60 dedicated-schema security posture; return findings."""
    findings: list[str] = []
    # A role must exist before has_schema_privilege can be evaluated.
    for role in REQUIRED_ROLES:
        if _role_attributes(conn, role) is None:
            findings.append(f"role_absent:{role}")
    if findings:
        return findings

    owner = conn.execute(
        "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = %s",
        (CATALOG_DEDICATED_SCHEMA,),
    ).fetchone()
    if owner is None:
        return ["dedicated_schema_absent"]
    if owner[0] != "workflow_research_evidence_owner":
        findings.append("dedicated_schema_owner")

    # Canonical membership: the owner role is granted to the migration owner with
    # the EXACT ratified PG16 options — admin=false, inherit=false, set=true.
    # Any material option drift is rejected (not just the presence of the row).
    membership = conn.execute(
        """
        SELECT m.admin_option, m.inherit_option, m.set_option
        FROM pg_auth_members m
        JOIN pg_roles granted ON granted.oid = m.roleid
        JOIN pg_roles member ON member.oid = m.member
        WHERE granted.rolname = 'workflow_research_evidence_owner'
          AND member.rolname = 'workflow_migration_owner'
        """
    ).fetchone()
    if membership is None:
        findings.append("owner_membership_missing")
    elif (bool(membership[0]), bool(membership[1]), bool(membership[2])) != (
        False, False, True
    ):
        findings.append("owner_membership_options")

    # The runtime role must NOT reach the owner or migration-owner roles by any
    # direct OR indirect membership path. A recursive pg_auth_members traversal
    # (not a fixed-depth join) catches runtime → intermediate → privileged-role.
    escalation = conn.execute(
        """
        WITH RECURSIVE reachable(roleid) AS (
            SELECT oid FROM pg_roles WHERE rolname = 'workflow_automation_roi_runtime'
          UNION
            SELECT m.roleid
            FROM pg_auth_members m
            JOIN reachable r ON m.member = r.roleid
        )
        SELECT rolname FROM pg_roles
        WHERE oid IN (SELECT roleid FROM reachable)
          AND rolname IN (
              'workflow_research_evidence_owner', 'workflow_migration_owner'
          )
        ORDER BY rolname
        """
    ).fetchall()
    for row in escalation:
        findings.append(f"runtime_role_escalation:{row[0]}")

    # ACL posture: runtime has USAGE but NOT CREATE on the dedicated schema.
    usage = conn.execute(
        "SELECT has_schema_privilege('workflow_automation_roi_runtime', %s, 'USAGE')",
        (CATALOG_DEDICATED_SCHEMA,),
    ).fetchone()[0]
    if not usage:
        findings.append("runtime_missing_usage")
    create = conn.execute(
        "SELECT has_schema_privilege('workflow_automation_roi_runtime', %s, 'CREATE')",
        (CATALOG_DEDICATED_SCHEMA,),
    ).fetchone()[0]
    if create:
        findings.append("runtime_has_create")
    return findings


def _namespace_findings(conn, cur_schema: str) -> list[str]:
    """Verify search_path resolves canonical ledgers into ``cur_schema``.

    A decoy schema earlier in search_path that shadows a ledger would make the
    ledger resolve elsewhere than ``current_schema`` (caught here); a decoy that
    IS ``current_schema`` fails the trigger checks (they are all schema-qualified
    to ``cur_schema``).
    """
    findings: list[str] = []
    if not cur_schema or cur_schema.startswith("pg_"):
        return ["current_schema_invalid"]
    for anchor in (
        "candidate_fact_revision",
        "research_evidence_usage_authorization_decision",
    ):
        row = conn.execute(
            """
            SELECT n.nspname
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.oid = to_regclass(%s)
            """,
            (anchor,),
        ).fetchone()
        resolved = row[0] if row and row[0] else None
        if resolved != cur_schema:
            findings.append(f"anchor_resolves_offschema:{anchor}")
    return findings


def _collect_catalog(conn) -> dict:
    """Introspect the live catalog against the bounded manifest.

    Returns per-category readiness flags plus bounded (count-only) diagnostics.
    Every main-schema check is resolved against ``current_schema`` so an
    unqualified decoy schema cannot produce a false-positive ready verdict.
    """
    cur = _current_schema(conn)

    missing_relations = [
        name for name, relkind in CATALOG_MAIN_RELATIONS.items()
        if _relation_relkind(conn, cur, name) != relkind
    ]
    missing_relations += [
        f"{CATALOG_DEDICATED_SCHEMA}.{name}"
        for name, relkind in CATALOG_DEDICATED_RELATIONS.items()
        if _relation_relkind(conn, CATALOG_DEDICATED_SCHEMA, name) != relkind
    ]

    # Certify each protected trigger function by identity AND behaviour, keeping
    # the canonical OID so the trigger checks below can prove tgfoid binds to
    # exactly the certified function.
    bad_functions: list[str] = []
    certified_oids: dict[str, int] = {}
    for name in CATALOG_FUNCTIONS:
        problem, oid = _certify_function(conn, cur, name)
        if problem:
            bad_functions.append(f"{name}:{problem}")
        else:
            certified_oids[name] = oid

    bad_triggers: list[str] = []
    expected_by_relation: dict = {}
    for relation, tgname, *_rest in CATALOG_TRIGGERS:
        expected_by_relation.setdefault(relation, set()).add(tgname)
    # Complete inventory per bridge-written ledger: reject EXTRA non-internal
    # triggers (missing ones are reported by the per-trigger loop below).
    for relation, expected_names in expected_by_relation.items():
        for extra in sorted(_ledger_trigger_names(conn, cur, relation) - expected_names):
            bad_triggers.append(f"{relation}.{extra}:extra")
    # Exact per-trigger binding + semantic state.
    for relation, tgname, func, tgtype, tgenabled in CATALOG_TRIGGERS:
        problem = _trigger_problem(
            _trigger_state(conn, cur, relation, tgname),
            expected_func=func, expected_tgtype=tgtype,
            expected_tgenabled=tgenabled, cur_schema=cur,
            certified_oid=certified_oids.get(func),
        )
        if problem:
            bad_triggers.append(f"{relation}.{tgname}:{problem}")

    # Load-bearing request-id uniqueness: without it the repositories' 23505
    # recovery arm is unreachable and concurrent retries append duplicates.
    bad_constraints: list[str] = []
    for relation, conname, columns in CATALOG_CONSTRAINTS:
        problem = _constraint_problem(
            _constraint_state(conn, cur, relation, conname),
            expected_columns=columns,
        )
        if problem:
            bad_constraints.append(f"{relation}.{conname}:{problem}")

    missing_roles = [role for role in REQUIRED_ROLES if not _role_ready(conn, role)]
    security_findings = _topology_security_findings(conn)
    namespace_findings = _namespace_findings(conn, cur)

    return {
        "current_schema": cur,
        "missing_relations": missing_relations,
        "relations_ready": not missing_relations,
        "bad_functions": bad_functions,
        "functions_ready": not bad_functions,
        "bad_triggers": bad_triggers,
        "triggers_ready": not bad_triggers,
        "bad_constraints": bad_constraints,
        "constraints_ready": not bad_constraints,
        "missing_roles": missing_roles,
        "roles_ready": not missing_roles,
        "security_findings": security_findings,
        "topology_security_ready": not security_findings,
        "namespace_findings": namespace_findings,
        "namespace_ready": not namespace_findings,
    }


def _catalog_ready(catalog: dict) -> bool:
    return all(
        catalog[flag]
        for flag in (
            "relations_ready", "functions_ready", "triggers_ready",
            "constraints_ready", "roles_ready", "topology_security_ready",
            "namespace_ready",
        )
    )


def _enforce_write_preflight(conn, args) -> None:
    """Fail-closed prerequisites, on the write connection, before any service write.

    Any unmet prerequisite raises :class:`BridgePreflightError`, which the write
    runner turns into a rollback + non-zero exit. Nothing here writes; the DSN is
    never referenced.
    """
    # (1) Verify — never assume — the transaction is READ COMMITTED. This is the
    #     first statement on the connection, so it also opens the transaction at
    #     the pinned level.
    _verify_read_committed(conn)

    # (2) Catalog-exact topology: relations+relkind, functions+signature,
    #     write-boundary triggers, load-bearing request-id uniqueness,
    #     exact-attribute roles, v59/v60 security, and the connection namespace.
    #     Report the FIRST failing category.
    catalog = _collect_catalog(conn)
    for flag, message in (
        ("namespace_ready", "connection namespace/search_path is unsafe"),
        ("relations_ready", "required relations are missing or wrong relkind"),
        ("functions_ready",
         "required trigger functions are missing or semantically drifted"),
        ("triggers_ready", "required write-boundary triggers are missing/drifted/disabled"),
        ("constraints_ready",
         "load-bearing request-id UNIQUE constraints are missing or drifted "
         "(idempotent retry is unenforceable)"),
        ("roles_ready", "required topology roles are missing or divergent"),
        ("topology_security_ready", "v59/v60 topology security posture is unsafe"),
    ):
        if not catalog[flag]:
            raise BridgePreflightError(f"{message}; write blocked")

    # (3) Parent project and (when relevant) the source snapshot must exist.
    project_id = getattr(args, "project_id", None)
    if project_id and not _project_exists(conn, project_id):
        raise BridgePreflightError("project parent not found; write blocked")
    source_snapshot_id = getattr(args, "source_snapshot_id", None)
    if source_snapshot_id and (
        _snapshot_summary(
            conn, project_id=project_id, source_snapshot_id=source_snapshot_id
        )
        is None
    ):
        raise BridgePreflightError(
            "required source snapshot not found for project; write blocked"
        )

    # (4) The operator must pin the exact runtime identity they intend to write to.
    expected = getattr(args, "expect_runtime_fingerprint", None)
    if not expected or not str(expected).strip():
        raise BridgePreflightError(
            "writes require --expect-runtime-fingerprint "
            "(obtain it from `preflight`); write blocked"
        )
    if str(expected).strip() != _runtime_fingerprint(conn):
        raise BridgePreflightError(
            "runtime identity fingerprint mismatch; refusing to write"
        )


# ─────────────────────────── write transaction runner ───────────────────────────


def _run_write(
    command: str,
    args,
    stream,
    build,
    *,
    requires_snapshot_flag: bool = False,
) -> int:
    """Open one caller-owned write transaction, run ``build``, commit iff asked.

    ``build(conn) -> dict`` performs the validated service write and returns the
    command-specific JSON payload fields (record identities, sequences, etc.).
    The transaction is rolled back unless ``--commit`` was supplied AND ``build``
    did not raise. A fail-closed preflight runs on the same connection before any
    service write.
    """
    _require_research_evidence_enabled()
    if requires_snapshot_flag:
        _require_evidence_snapshot_enabled()
    _require_configured_database_url()

    commit = bool(getattr(args, "commit", False))
    conn = _configure_write_connection(open_bridge_connection())
    try:
        _enforce_write_preflight(conn, args)
        payload = _base_payload(command, dry_run=not commit)
        result = build(conn)
        payload.update(result)
        if commit:
            conn.commit()
            payload["committed"] = True
            payload["status"] = "committed"
        else:
            conn.rollback()
            payload["committed"] = False
            payload["status"] = "dry_run"
        _emit(payload, stream)
        return EXIT_OK
    except Exception:
        _safe_rollback_close(conn)
        raise
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover
            pass


def _run_readonly(command: str, stream, build) -> int:
    conn = _configure_readonly_connection(open_bridge_connection())
    try:
        payload = _base_payload(command, dry_run=True)
        payload.update(build(conn))
        _emit(payload, stream)
        return EXIT_OK
    finally:
        _safe_rollback_close(conn)


# ═══════════════════════════ command: preflight ═══════════════════════════


def cmd_preflight(args, stream) -> int:
    research_enabled = config.research_evidence_enabled()
    snapshot_enabled = config.evidence_snapshot_enabled()
    # Writes require an explicitly configured DATABASE_URL env var; the config
    # attribute carries a localhost fallback that does NOT authorize a write.
    database_url_configured = bool(os.getenv("DATABASE_URL", "").strip())

    payload = _base_payload(
        "preflight",
        dry_run=True,
        research_evidence_enabled=research_enabled,
        evidence_snapshot_enabled=snapshot_enabled,
        database_url_configured=database_url_configured,
    )

    warnings: list[str] = []
    connection_available = False
    active_runtime_database = ""
    runtime_fingerprint = ""
    runtime_identity: dict = {}
    catalog: dict = {}
    project_present: Optional[bool] = None
    snapshot_present: Optional[bool] = None
    same_runtime_database: Optional[bool] = None

    try:
        conn = _configure_readonly_connection(open_bridge_connection())
    except Exception as exc:
        payload["connection_available"] = False
        payload["status"] = "degraded"
        payload["writes_allowed"] = False
        payload["warnings"] = [
            "database connection unavailable: " + type(exc).__name__
        ]
        payload["blocked_reason"] = "connection_unavailable"
        _emit(payload, stream)
        return EXIT_OK

    try:
        connection_available = True
        active_runtime_database = _current_database(conn)
        runtime_identity = _runtime_identity(conn)
        runtime_fingerprint = _runtime_fingerprint(conn)
        catalog = _collect_catalog(conn)

        expect_database = getattr(args, "expect_database", None)
        if expect_database:
            same_runtime_database = active_runtime_database == expect_database
            if not same_runtime_database:
                warnings.append(
                    "active runtime database does not match --expect-database"
                )

        project_id = getattr(args, "project_id", None)
        if project_id:
            project_present = _project_exists(conn, project_id)
            if not project_present:
                warnings.append("project parent not found for --project-id")
            source_snapshot_id = getattr(args, "source_snapshot_id", None)
            if source_snapshot_id:
                snapshot_present = (
                    _snapshot_summary(
                        conn,
                        project_id=project_id,
                        source_snapshot_id=source_snapshot_id,
                    )
                    is not None
                )
                if not snapshot_present:
                    warnings.append("source snapshot not found for project")
    finally:
        _safe_rollback_close(conn)

    relations_ready = catalog.get("relations_ready", False)
    functions_ready = catalog.get("functions_ready", False)
    triggers_ready = catalog.get("triggers_ready", False)
    constraints_ready = catalog.get("constraints_ready", False)
    roles_ready = catalog.get("roles_ready", False)
    topology_security_ready = catalog.get("topology_security_ready", False)
    namespace_ready = catalog.get("namespace_ready", False)
    catalog_ready = _catalog_ready(catalog) if catalog else False

    # Whole-topology write eligibility (independent of the supplied target).
    research_writes_allowed = (
        research_enabled
        and database_url_configured
        and connection_available
        and catalog_ready
    )
    # fact-create additionally requires the snapshot capture flag.
    fact_writes_allowed = research_writes_allowed and snapshot_enabled

    # Supplied-target readiness: any provided target check that fails flips this.
    target_checks = []
    if getattr(args, "project_id", None):
        target_checks.append(bool(project_present))
    if getattr(args, "source_snapshot_id", None):
        target_checks.append(bool(snapshot_present))
    if getattr(args, "expect_database", None):
        target_checks.append(bool(same_runtime_database))
    requested_target_ready = all(target_checks)

    # The overall verdict is the conjunction relevant to the supplied arguments:
    # a single writes_allowed must not stay true when a supplied target fails.
    writes_allowed = research_writes_allowed and requested_target_ready

    if not research_enabled:
        warnings.append(
            "MAS_RESEARCH_EVIDENCE_ENABLED is not true; writes are disabled"
        )
    if not snapshot_enabled:
        warnings.append(
            "MAS_EVIDENCE_SNAPSHOT_ENABLED is not true; fact writes are disabled"
        )
    if not database_url_configured:
        warnings.append(
            "DATABASE_URL is not explicitly configured; writes are blocked"
        )
    if connection_available and not catalog_ready:
        warnings.append("catalog manifest not satisfied; writes are blocked")
    if connection_available and not constraints_ready:
        warnings.append(
            "load-bearing request-id UNIQUE constraints are missing or drifted; "
            "idempotent retry cannot be enforced and writes are blocked"
        )
    if not requested_target_ready:
        warnings.append("a supplied target check failed; writes are blocked")

    payload.update(
        connection_available=connection_available,
        active_runtime_database=active_runtime_database,
        runtime_identity=runtime_identity,
        runtime_fingerprint=runtime_fingerprint,
        current_schema=catalog.get("current_schema", ""),
        same_runtime_database=same_runtime_database,
        relations_ready=relations_ready,
        functions_ready=functions_ready,
        triggers_ready=triggers_ready,
        constraints_ready=constraints_ready,
        roles_ready=roles_ready,
        topology_security_ready=topology_security_ready,
        namespace_ready=namespace_ready,
        missing_relations=catalog.get("missing_relations", []),
        bad_functions=catalog.get("bad_functions", []),
        bad_triggers=catalog.get("bad_triggers", []),
        bad_constraints=catalog.get("bad_constraints", []),
        missing_roles=catalog.get("missing_roles", []),
        security_findings=catalog.get("security_findings", []),
        namespace_findings=catalog.get("namespace_findings", []),
        project_present=project_present,
        source_snapshot_present=snapshot_present,
        research_writes_allowed=research_writes_allowed,
        fact_writes_allowed=fact_writes_allowed,
        requested_target_ready=requested_target_ready,
        writes_allowed=writes_allowed,
        warnings=warnings,
        status="ok" if writes_allowed else "degraded",
    )
    _emit(payload, stream)
    return EXIT_OK


# ═══════════════════════════ command: project-show ═══════════════════════════


def cmd_project_show(args, stream) -> int:
    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")

    def build(conn) -> dict:
        row = conn.execute(
            "SELECT id::text, name FROM projects WHERE id = %s", (project_id,)
        ).fetchone()
        return {
            "project_id": project_id,
            "project_present": row is not None,
            "project_name": (row[1] if row is not None else ""),
        }

    return _run_readonly("project-show", stream, build)


# ═══════════════════════════ command: source-list ═══════════════════════════


def cmd_source_list(args, stream) -> int:
    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    limit = int(getattr(args, "limit", 50) or 50)

    def build(conn) -> dict:
        rows = conn.execute(
            """
            SELECT snapshot.id::text, snapshot.source_kind,
                   metadata.citation_label, metadata.canonical_source_locator,
                   metadata.declared_quality_tier
            FROM source_snapshot snapshot
            LEFT JOIN LATERAL (
                SELECT citation_label, canonical_source_locator,
                       declared_quality_tier
                FROM research_source_metadata_revision m
                WHERE m.source_snapshot_id = snapshot.id
                  AND m.project_id = snapshot.project_id
                ORDER BY m.created_at DESC
                LIMIT 1
            ) metadata ON true
            WHERE snapshot.project_id = %s
            ORDER BY snapshot.captured_at DESC, snapshot.id
            LIMIT %s
            """,
            (project_id, limit),
        ).fetchall()
        sources = [
            {
                "source_snapshot_id": row[0],
                "source_kind": row[1] or "",
                "citation_label": row[2] or "",
                "canonical_source_locator": row[3] or "",
                "declared_quality_tier": row[4] or "",
            }
            for row in rows
        ]
        return {
            "project_id": project_id,
            "counts": {"source_count": len(sources)},
            "sources": sources,
        }

    return _run_readonly("source-list", stream, build)


# ═══════════════════════════ command: source-metadata-create ═══════════════════════════


def cmd_source_metadata_create(args, stream) -> int:
    from research_evidence.models import SourceMetadataRevisionCreate
    from research_evidence.service import create_source_metadata_revision

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    source_snapshot_id = _require_arg(
        getattr(args, "source_snapshot_id", None), "--source-snapshot-id"
    )
    actor = _require_arg(getattr(args, "actor", None), "--actor")

    def build(conn) -> dict:
        revision = SourceMetadataRevisionCreate(
            project_id=project_id,
            source_snapshot_id=source_snapshot_id,
            canonical_source_locator=getattr(args, "canonical_source_locator", "") or "",
            publisher=getattr(args, "publisher", "") or "",
            author=getattr(args, "author", "") or "",
            citation_label=getattr(args, "citation_label", "") or "",
            declared_quality_tier=getattr(args, "declared_quality_tier", "") or "",
            declared_quality_rationale=(
                getattr(args, "declared_quality_rationale", "") or ""
            ),
            created_by=actor,
        )
        record = create_source_metadata_revision(conn, revision)
        return {
            "project_id": project_id,
            "source_metadata_revision_id": record.id,
            "source_snapshot_id": record.source_snapshot_id,
        }

    return _run_write("source-metadata-create", args, stream, build)


# ═══════════════════════════ command: fact-create ═══════════════════════════
# fact-metadata-create is safely combined into fact-create: the v47 fact and its
# R1.1 metadata revision are created atomically on one transaction so a fact is
# never left without the metadata that downstream intake items require.


def cmd_fact_create(args, stream) -> int:
    from knowledge.evidence_snapshot.fact_service import (
        create_candidate_fact_revision,
    )
    from knowledge.evidence_snapshot.validation import (
        FactValidationError,
        validate_fact,
    )
    from research_evidence.models import FactMetadataRevisionCreate
    from research_evidence.service import create_fact_metadata_revision

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    source_snapshot_id = _require_arg(
        getattr(args, "source_snapshot_id", None), "--source-snapshot-id"
    )
    actor = _require_arg(getattr(args, "actor", None), "--actor")
    fact_type = _require_arg(getattr(args, "fact_type", None), "--fact-type")

    numeric_value: Any = None
    raw_value = getattr(args, "value", None)
    if raw_value is not None and str(raw_value).strip() != "":
        try:
            numeric_value = Decimal(str(raw_value))
        except InvalidOperation as exc:
            raise BridgeError(f"--value must be a decimal (got {raw_value!r})") from exc

    as_of = getattr(args, "as_of_date", None)
    as_of_date = None
    if as_of:
        as_of_date = _parse_aware_datetime(as_of + "T00:00:00+00:00", "--as-of-date").date() \
            if len(as_of) == 10 else _parse_aware_datetime(as_of, "--as-of-date").date()

    def build(conn) -> dict:
        # Reject a non-finite numeric value with the canonical FactValidationError
        # BEFORE this CLI's own validate_fact call: for a bound-comparing profile
        # (duration, bounded percentage) validate_fact would compare a NaN against
        # a bound and raise a non-canonical decimal.InvalidOperation instead. The
        # bounded fact_service applies the same authoritative Decimal.is_finite()
        # guard; this mirror keeps the CLI's own validation from tripping first.
        if numeric_value is not None and not numeric_value.is_finite():
            raise FactValidationError("numeric candidate facts must be finite")
        validated = validate_fact(
            fact_type,
            value=numeric_value,
            text=getattr(args, "text", None),
            unit=getattr(args, "unit", "") or "",
            currency_code=getattr(args, "currency_code", None),
            as_of_date=as_of_date,
            numerator_context=getattr(args, "numerator_context", None),
            denominator_context=getattr(args, "denominator_context", None),
            percentage_basis=getattr(args, "percentage_basis", None),
            percentage_subtype=getattr(args, "percentage_subtype", None),
            time_unit=getattr(args, "time_unit", None),
            counted_entity=getattr(args, "counted_entity", None),
        )
        fact_id = create_candidate_fact_revision(
            conn,
            project_id=project_id,
            source_snapshot_id=source_snapshot_id,
            fact=validated,
            created_by=actor,
        )
        metadata = create_fact_metadata_revision(
            conn,
            FactMetadataRevisionCreate(
                project_id=project_id,
                candidate_fact_revision_id=fact_id,
                citation_locator=getattr(args, "citation_locator", "") or "",
                source_char_range=getattr(args, "source_char_range", None),
                excerpt_hash=getattr(args, "excerpt_hash", "") or "",
                stable_fact_key=getattr(args, "stable_fact_key", "") or "",
                created_by=actor,
            ),
        )
        return {
            "project_id": project_id,
            "candidate_fact_revision_id": fact_id,
            "fact_metadata_revision_id": metadata.id,
            "source_snapshot_id": source_snapshot_id,
            "fact_type": fact_type,
        }

    return _run_write(
        "fact-create", args, stream, build, requires_snapshot_flag=True
    )


# ═══════════════════════════ command: claim-create ═══════════════════════════


def cmd_claim_create(args, stream) -> int:
    from research_evidence.models import ClaimDraftCreate
    from research_evidence.service import create_claim_draft

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    actor = _require_arg(getattr(args, "actor", None), "--actor")
    claim_text = _require_arg(getattr(args, "claim_text", None), "--claim-text")

    def build(conn) -> dict:
        record = create_claim_draft(
            conn,
            ClaimDraftCreate(
                project_id=project_id,
                claim_text=claim_text,
                claim_category=getattr(args, "claim_category", "") or "",
                created_by=actor,
            ),
        )
        return {"project_id": project_id, "claim_draft_id": record.id}

    return _run_write("claim-create", args, stream, build)


# ═══════════════════════════ command: intake-create ═══════════════════════════


def cmd_intake_create(args, stream) -> int:
    from research_evidence.intake_models import ResearchEvidenceIntakeCreate
    from research_evidence.intake_service import create_intake

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    actor = _require_arg(getattr(args, "actor", None), "--actor")
    source_snapshot_id = _require_arg(
        getattr(args, "source_snapshot_id", None), "--source-snapshot-id"
    )
    source_metadata_revision_id = _require_arg(
        getattr(args, "source_metadata_revision_id", None),
        "--source-metadata-revision-id",
    )
    selection_reason = _require_arg(
        getattr(args, "selection_reason", None), "--selection-reason"
    )

    def build(conn) -> dict:
        record = create_intake(
            conn,
            ResearchEvidenceIntakeCreate(
                project_id=project_id,
                source_snapshot_id=source_snapshot_id,
                source_metadata_revision_id=source_metadata_revision_id,
                selection_reason=selection_reason,
                created_by=actor,
            ),
        )
        return {
            "project_id": project_id,
            "research_evidence_intake_id": record.id,
            "source_snapshot_id": record.source_snapshot_id,
        }

    return _run_write("intake-create", args, stream, build)


# ═══════════════════════════ command: intake-item-create ═══════════════════════════


def cmd_intake_item_create(args, stream) -> int:
    from research_evidence.intake_models import ResearchEvidenceIntakeItemCreate
    from research_evidence.intake_service import create_intake_item

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    actor = _require_arg(getattr(args, "actor", None), "--actor")
    intake_id = _require_arg(
        getattr(args, "research_evidence_intake_id", None),
        "--research-evidence-intake-id",
    )
    item_kind = _require_arg(getattr(args, "item_kind", None), "--item-kind")

    def build(conn) -> dict:
        record = create_intake_item(
            conn,
            ResearchEvidenceIntakeItemCreate(
                project_id=project_id,
                research_evidence_intake_id=intake_id,
                item_kind=item_kind,
                candidate_fact_revision_id=getattr(
                    args, "candidate_fact_revision_id", None
                ),
                fact_metadata_revision_id=getattr(
                    args, "fact_metadata_revision_id", None
                ),
                claim_draft_id=getattr(args, "claim_draft_id", None),
                created_by=actor,
            ),
        )
        return {
            "project_id": project_id,
            "research_evidence_intake_item_id": record.id,
            "item_kind": record.item_kind,
            "source_snapshot_id": record.source_snapshot_id,
        }

    return _run_write("intake-item-create", args, stream, build)


# ═══════════════════════════ command: review-record ═══════════════════════════


def cmd_review_record(args, stream) -> int:
    from research_evidence.review_models import (
        ResearchEvidenceIntakeItemReviewDecisionCreate,
    )
    from research_evidence.review_service import record_item_review_decision

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    item_id = _require_arg(
        getattr(args, "research_evidence_intake_item_id", None),
        "--research-evidence-intake-item-id",
    )
    decision_type = _require_arg(getattr(args, "decision_type", None), "--decision-type")
    decision_reason = _require_arg(
        getattr(args, "decision_reason", None), "--decision-reason"
    )
    actor = _require_arg(getattr(args, "actor", None), "--actor")
    request_id = _require_arg(getattr(args, "request_id", None), "--request-id")

    def build(conn) -> dict:
        record = record_item_review_decision(
            conn,
            ResearchEvidenceIntakeItemReviewDecisionCreate(
                project_id=project_id,
                research_evidence_intake_item_id=item_id,
                decision_type=decision_type,
                decision_reason=decision_reason,
                decided_by=actor,
                request_id=request_id,
            ),
        )
        return {
            "project_id": project_id,
            "request_id": request_id,
            "review_decision_id": record.id,
            "decision_type": record.decision_type,
            "decision_sequence": record.decision_sequence,
        }

    return _run_write("review-record", args, stream, build)


# ═══════════════════════════ command: freshness-record ═══════════════════════════


def cmd_freshness_record(args, stream) -> int:
    from research_evidence.freshness_models import (
        ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
    )
    from research_evidence.freshness_service import record_item_freshness_assessment

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    item_id = _require_arg(
        getattr(args, "research_evidence_intake_item_id", None),
        "--research-evidence-intake-item-id",
    )
    request_id = _require_arg(getattr(args, "request_id", None), "--request-id")
    basis_timestamp = _parse_aware_datetime(
        _require_arg(getattr(args, "basis_timestamp", None), "--basis-timestamp"),
        "--basis-timestamp",
    )
    fresh_through = _parse_aware_datetime(
        _require_arg(getattr(args, "fresh_through", None), "--fresh-through"),
        "--fresh-through",
    )

    def build(conn) -> dict:
        record = record_item_freshness_assessment(
            conn,
            ResearchEvidenceIntakeItemFreshnessAssessmentCreate(
                project_id=project_id,
                research_evidence_intake_item_id=item_id,
                request_id=request_id,
                policy_identifier=_require_arg(
                    getattr(args, "policy_identifier", None), "--policy-identifier"
                ),
                policy_version=_require_arg(
                    getattr(args, "policy_version", None), "--policy-version"
                ),
                policy_parameters_json=json.loads(
                    getattr(args, "policy_parameters_json", None) or "{}"
                ),
                policy_fingerprint=getattr(args, "policy_fingerprint", "") or "",
                evaluator_version=_require_arg(
                    getattr(args, "evaluator_version", None), "--evaluator-version"
                ),
                basis_timestamp=basis_timestamp,
                fresh_through=fresh_through,
                drift_status=_require_arg(
                    getattr(args, "drift_status", None), "--drift-status"
                ),
                drift_reason=_require_arg(
                    getattr(args, "drift_reason", None), "--drift-reason"
                ),
                assessed_by=_require_arg(getattr(args, "actor", None), "--actor"),
            ),
        )
        return {
            "project_id": project_id,
            "request_id": request_id,
            "freshness_assessment_id": record.id,
            "assessment_sequence": record.assessment_sequence,
        }

    return _run_write("freshness-record", args, stream, build)


# ═══════════════════════════ command: claim-support-record ═══════════════════════════


def cmd_claim_support_record(args, stream) -> int:
    from research_evidence.claim_support_models import (
        ResearchEvidenceClaimSupportAssessmentCreate,
    )
    from research_evidence.claim_support_service import record_claim_support_assessment

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    claim_item = _require_arg(
        getattr(args, "claim_intake_item_id", None), "--claim-intake-item-id"
    )
    evidence_item = _require_arg(
        getattr(args, "evidence_intake_item_id", None), "--evidence-intake-item-id"
    )
    request_id = _require_arg(getattr(args, "request_id", None), "--request-id")

    def build(conn) -> dict:
        record = record_claim_support_assessment(
            conn,
            ResearchEvidenceClaimSupportAssessmentCreate(
                project_id=project_id,
                claim_intake_item_id=claim_item,
                evidence_intake_item_id=evidence_item,
                request_id=request_id,
                locator_resolution=_require_arg(
                    getattr(args, "locator_resolution", None), "--locator-resolution"
                ),
                locator_rationale=_require_arg(
                    getattr(args, "locator_rationale", None), "--locator-rationale"
                ),
                evidence_linkage=_require_arg(
                    getattr(args, "evidence_linkage", None), "--evidence-linkage"
                ),
                evidence_linkage_rationale=_require_arg(
                    getattr(args, "evidence_linkage_rationale", None),
                    "--evidence-linkage-rationale",
                ),
                semantic_relationship=_require_arg(
                    getattr(args, "semantic_relationship", None),
                    "--semantic-relationship",
                ),
                semantic_relationship_rationale=_require_arg(
                    getattr(args, "semantic_relationship_rationale", None),
                    "--semantic-relationship-rationale",
                ),
                assessed_by=_require_arg(getattr(args, "actor", None), "--actor"),
            ),
        )
        return {
            "project_id": project_id,
            "request_id": request_id,
            "claim_support_assessment_id": record.id,
            "assessment_sequence": record.assessment_sequence,
        }

    return _run_write("claim-support-record", args, stream, build)


# ═══════════════════════════ command: annotation-record ═══════════════════════════


def cmd_annotation_record(args, stream) -> int:
    from research_evidence.pack_models import (
        ResearchEvidenceClaimAnnotationRevisionCreate,
    )
    from research_evidence.pack_service import record_claim_annotation_revision

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    claim_draft_id = _require_arg(
        getattr(args, "claim_draft_id", None), "--claim-draft-id"
    )
    request_id = _require_arg(getattr(args, "request_id", None), "--request-id")

    def build(conn) -> dict:
        record = record_claim_annotation_revision(
            conn,
            ResearchEvidenceClaimAnnotationRevisionCreate(
                project_id=project_id,
                claim_draft_id=claim_draft_id,
                request_id=request_id,
                epistemic_status=_require_arg(
                    getattr(args, "epistemic_status", None), "--epistemic-status"
                ),
                confidence_label=_require_arg(
                    getattr(args, "confidence_label", None), "--confidence-label"
                ),
                decision_relevance=_require_arg(
                    getattr(args, "decision_relevance", None), "--decision-relevance"
                ),
                supports_statement=_require_arg(
                    getattr(args, "supports_statement", None), "--supports-statement"
                ),
                does_not_prove=_require_arg(
                    getattr(args, "does_not_prove", None), "--does-not-prove"
                ),
                limitations=_parse_string_array(getattr(args, "limitations", None)),
                related_claim_draft_ids=_parse_string_array(
                    getattr(args, "related_claim_draft_ids", None)
                ),
                actor=_require_arg(getattr(args, "actor", None), "--actor"),
            ),
        )
        return {
            "project_id": project_id,
            "request_id": request_id,
            "claim_draft_id": claim_draft_id,
            "annotation_revision_id": record.id,
            "annotation_sequence": record.annotation_sequence,
        }

    return _run_write("annotation-record", args, stream, build)


# ═══════════════════════════ command: context-record ═══════════════════════════


def cmd_context_record(args, stream) -> int:
    from research_evidence.pack_models import (
        ResearchEvidenceProjectContextRevisionCreate,
    )
    from research_evidence.pack_service import record_project_context_revision

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    request_id = _require_arg(getattr(args, "request_id", None), "--request-id")

    def build(conn) -> dict:
        record = record_project_context_revision(
            conn,
            ResearchEvidenceProjectContextRevisionCreate(
                project_id=project_id,
                request_id=request_id,
                research_question=_require_arg(
                    getattr(args, "research_question", None), "--research-question"
                ),
                project_limitations=_parse_string_array(
                    getattr(args, "project_limitations", None)
                ),
                unresolved_gaps=_parse_string_array(
                    getattr(args, "unresolved_gaps", None)
                ),
                actor=_require_arg(getattr(args, "actor", None), "--actor"),
            ),
        )
        return {
            "project_id": project_id,
            "request_id": request_id,
            "context_revision_id": record.id,
            "context_sequence": record.context_sequence,
        }

    return _run_write("context-record", args, stream, build)


# ═══════════════════════════ authorization preview + confirmation ═══════════════════════════


def _citation_label(conn, *, project_id: str, source_metadata_revision_id: str) -> str:
    """Return the citation label for one source metadata revision, or ``""``."""
    if not source_metadata_revision_id:
        return ""
    row = conn.execute(
        """
        SELECT citation_label FROM research_source_metadata_revision
        WHERE id = %s AND project_id = %s
        """,
        (source_metadata_revision_id, project_id),
    ).fetchone()
    return row[0] if row else ""


def _authorization_preview(conn, *, project_id, claim_item, evidence_item) -> dict:
    """Assemble the bounded, non-secret authorization preview for one pair."""
    from research_evidence import claim_support_repository as cs_repo
    from research_evidence import review_repository
    from research_evidence.claim_support_service import (
        get_effective_claim_support_assessment,
    )
    from research_evidence.pack_service import (
        get_effective_claim_annotation_revision,
    )

    claim_ctx, evidence_ctx = cs_repo.require_pair_context(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_item,
        evidence_intake_item_id=evidence_item,
    )
    annotation = get_effective_claim_annotation_revision(
        conn, project_id=project_id, claim_draft_id=claim_ctx.claim_draft_id
    )
    support = get_effective_claim_support_assessment(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_item,
        evidence_intake_item_id=evidence_item,
    )
    claim_review = review_repository.get_effective_decision(
        conn, project_id=project_id, research_evidence_intake_item_id=claim_item
    )
    evidence_review = review_repository.get_effective_decision(
        conn, project_id=project_id, research_evidence_intake_item_id=evidence_item
    )
    # The authorization is about the *evidence* item, so the evidence citation
    # label must be read through the evidence endpoint's source metadata
    # revision — never the claim's. The claim's own source label is exposed
    # separately so the two are never collapsed into one ambiguous field.
    evidence_source_citation_label = _citation_label(
        conn,
        project_id=project_id,
        source_metadata_revision_id=evidence_ctx.source_metadata_revision_id,
    )
    claim_source_citation_label = _citation_label(
        conn,
        project_id=project_id,
        source_metadata_revision_id=claim_ctx.source_metadata_revision_id,
    )

    return {
        "project_id": project_id,
        "claim_intake_item_id": claim_item,
        "evidence_intake_item_id": evidence_item,
        "claim_draft_id": claim_ctx.claim_draft_id,
        "candidate_fact_revision_id": evidence_ctx.candidate_fact_revision_id,
        "current_claim_annotation_revision_id": (
            annotation.id if annotation is not None else None
        ),
        "current_claim_support_assessment_id": (
            support.id if support is not None else None
        ),
        "current_claim_review_decision_id": (
            claim_review.id if claim_review is not None else None
        ),
        "current_evidence_review_decision_id": (
            evidence_review.id if evidence_review is not None else None
        ),
        "evidence_source_citation_label": evidence_source_citation_label,
        "claim_source_citation_label": claim_source_citation_label,
        "epistemic_status": (
            annotation.epistemic_status if annotation is not None else None
        ),
        "confidence_label": (
            annotation.confidence_label if annotation is not None else None
        ),
        "limitations": (
            list(annotation.limitations) if annotation is not None else []
        ),
        "does_not_prove": (
            annotation.does_not_prove if annotation is not None else ""
        ),
        "semantic_relationship": (
            support.semantic_relationship if support is not None else None
        ),
        "decision_scope": FIXED_USAGE_SCOPE_VALUE,
        "authorization_does_not_extend_to": [
            "operator_dossier",
            "client_report",
            "exports",
            "publication",
            "any_other_usage_scope",
        ],
    }


def _expected_confirmation(project_id: str, claim_item: str, evidence_item: str) -> str:
    return f"{project_id} {claim_item} {evidence_item}"


def _check_confirmation(args, project_id, claim_item, evidence_item) -> None:
    """Require the typed confirmation to echo the exact three identities."""
    supplied = getattr(args, "confirm", None)
    expected = _expected_confirmation(project_id, claim_item, evidence_item)
    if supplied is None or supplied.strip() != expected:
        raise BridgeConfirmationError(
            "typed confirmation must exactly echo "
            "'<project_id> <claim_intake_item_id> <evidence_intake_item_id>'"
        )


def _run_authorization(command: str, args, stream, decision: str) -> int:
    from research_evidence.pack_models import (
        ResearchEvidenceUsageAuthorizationDecisionCreate,
    )
    from research_evidence.pack_service import record_usage_authorization_decision

    _require_research_evidence_enabled()

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    claim_item = _require_arg(
        getattr(args, "claim_intake_item_id", None), "--claim-intake-item-id"
    )
    evidence_item = _require_arg(
        getattr(args, "evidence_intake_item_id", None), "--evidence-intake-item-id"
    )
    request_id = _require_arg(getattr(args, "request_id", None), "--request-id")
    reason = _require_arg(getattr(args, "reason", None), "--reason")
    actor = _require_arg(getattr(args, "actor", None), "--actor")
    commit = bool(getattr(args, "commit", False))
    _require_configured_database_url()

    conn = _configure_write_connection(open_bridge_connection())
    try:
        _enforce_write_preflight(conn, args)
        preview = _authorization_preview(
            conn,
            project_id=project_id,
            claim_item=claim_item,
            evidence_item=evidence_item,
        )
        payload = _base_payload(
            command,
            dry_run=not commit,
            project_id=project_id,
            request_id=request_id,
            usage_scope=FIXED_USAGE_SCOPE_VALUE,
            authorization_preview=preview,
        )

        # Typed confirmation gates the commit for both authorize and revoke.
        confirmation_ok = True
        try:
            _check_confirmation(args, project_id, claim_item, evidence_item)
        except BridgeConfirmationError as exc:
            confirmation_ok = False
            payload["confirmation_ok"] = False
            payload["warnings"] = [str(exc)]
        else:
            payload["confirmation_ok"] = True

        if commit and not confirmation_ok:
            # Never emit a COMMIT when the confirmation fails.
            conn.rollback()
            raise BridgeConfirmationError(
                "authorization not committed: confirmation did not match"
            )

        record = record_usage_authorization_decision(
            conn,
            ResearchEvidenceUsageAuthorizationDecisionCreate(
                project_id=project_id,
                claim_intake_item_id=claim_item,
                evidence_intake_item_id=evidence_item,
                usage_scope=FIXED_USAGE_SCOPE_VALUE,
                decision=decision,
                reason=reason,
                actor=actor,
                request_id=request_id,
            ),
        )
        payload["authorization_decision_id"] = record.id

        if commit and confirmation_ok:
            conn.commit()
            payload["committed"] = True
            payload["status"] = "committed"
        else:
            conn.rollback()
            payload["committed"] = False
            payload["status"] = "dry_run"
        _emit(payload, stream)
        return EXIT_OK
    except Exception:
        _safe_rollback_close(conn)
        raise
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover
            pass


def cmd_authorize_internal_analysis(args, stream) -> int:
    return _run_authorization(
        "authorize-internal-analysis", args, stream, "authorized"
    )


def cmd_revoke_internal_analysis(args, stream) -> int:
    return _run_authorization("revoke-internal-analysis", args, stream, "revoked")


# ═══════════════════════════ command: authorization-list ═══════════════════════════


def cmd_authorization_list(args, stream) -> int:
    from research_evidence.pack_service import list_effective_project_authorizations

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")

    def build(conn) -> dict:
        _require_research_evidence_enabled()
        decisions = list_effective_project_authorizations(conn, project_id=project_id)
        rows = []
        internal_effective = 0
        for d in decisions:
            decision_value = getattr(
                getattr(d, "decision", None), "value", getattr(d, "decision", "")
            )
            scope_value = getattr(
                getattr(d, "usage_scope", None), "value", getattr(d, "usage_scope", "")
            )
            if decision_value == "authorized" and scope_value == FIXED_USAGE_SCOPE_VALUE:
                internal_effective += 1
            rows.append(
                {
                    "claim_intake_item_id": getattr(d, "claim_intake_item_id", ""),
                    "evidence_intake_item_id": getattr(d, "evidence_intake_item_id", ""),
                    "usage_scope": scope_value,
                    "decision": decision_value,
                }
            )
        return {
            "project_id": project_id,
            "counts": {
                "effective_authorization_count": len(rows),
                "internal_analysis_effective_count": internal_effective,
            },
            "authorizations": rows,
        }

    return _run_readonly("authorization-list", stream, build)


# ═══════════════════════════ command: pack-preview ═══════════════════════════


def cmd_pack_preview(args, stream) -> int:
    from research_evidence import UsageScope
    from research_evidence.pack_service import (
        ResearchEvidencePackLimitError,
        assemble_research_evidence_pack,
    )

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")

    def build(conn) -> dict:
        _require_research_evidence_enabled()
        try:
            pack = assemble_research_evidence_pack(
                conn,
                project_id=project_id,
                usage_scope=UsageScope.INTERNAL_ANALYSIS,
            )
        except ResearchEvidencePackLimitError as exc:
            return {
                "project_id": project_id,
                "usage_scope": FIXED_USAGE_SCOPE_VALUE,
                "pack_status": "WOULD_BLOCK_CAPACITY_OVERFLOW",
                "warnings": [type(exc).__name__],
            }
        semantic = {}
        for relationship in pack.relationships:
            key = getattr(
                getattr(relationship, "semantic_relationship", None),
                "value",
                str(getattr(relationship, "semantic_relationship", "")),
            )
            semantic[key] = semantic.get(key, 0) + 1
        limitations: list[str] = []
        does_not_prove: list[str] = []
        for claim in pack.claims:
            limitations.extend(claim.annotation.limitations)
            if claim.annotation.does_not_prove:
                does_not_prove.append(claim.annotation.does_not_prove)
        return {
            "project_id": project_id,
            "usage_scope": FIXED_USAGE_SCOPE_VALUE,
            "pack_status": "EMPTY" if not pack.relationships else "POPULATED",
            "counts": {
                "source_count": pack.counts.source_count,
                "claim_count": pack.counts.claim_count,
                "evidence_count": pack.counts.evidence_count,
                "relationship_count": pack.counts.relationship_count,
            },
            "source_identities": [
                {
                    "source_snapshot_id": source.source_snapshot_id,
                    "citation_label": source.citation_label,
                }
                for source in pack.sources
            ],
            "relationship_semantics": semantic,
            "limitations": limitations,
            "does_not_prove": does_not_prove,
        }

    return _run_readonly("pack-preview", stream, build)


# ═══════════════════════════ command: projection-preview ═══════════════════════════


def _classify_projection_block(projection) -> tuple[str, Optional[int]]:
    """Classify the complete rendered block against the frozen A-4A budget.

    Returns ``(block_status, rendered_utf8_bytes)``. Never truncates: an
    over-budget block yields ``WOULD_BLOCK_PROMPT_OVERFLOW`` and ``None`` bytes.
    Emptiness anchors on ``relationships`` exactly as the A-4A consumer does.
    """
    import research_evidence_context as rc

    if not projection.relationships:
        return "EMPTY", 0
    try:
        block = rc.render_research_evidence_block(projection)
    except rc.ResearchEvidencePromptBudgetError:
        return "WOULD_BLOCK_PROMPT_OVERFLOW", None
    return "WITHIN_LIMIT", len(block.encode("utf-8"))


def cmd_projection_preview(args, stream) -> int:
    import research_evidence_context as rc
    from research_evidence import UsageScope
    from research_evidence.pack_service import ResearchEvidencePackLimitError
    from research_evidence.presentation_projection_service import (
        project_research_evidence_presentation,
    )

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")

    def build(conn) -> dict:
        _require_research_evidence_enabled()
        try:
            projection = project_research_evidence_presentation(
                conn,
                project_id=project_id,
                usage_scope=UsageScope.INTERNAL_ANALYSIS,
            )
        except ResearchEvidencePackLimitError as exc:
            # No projection was assembled, so no block was rendered: OMIT
            # rendered_utf8_bytes entirely rather than fabricating a 0.
            return {
                "project_id": project_id,
                "usage_scope": FIXED_USAGE_SCOPE_VALUE,
                "block_status": "WOULD_BLOCK_CAPACITY_OVERFLOW",
                "warnings": [type(exc).__name__],
            }

        limitations: list[str] = []
        does_not_prove: list[str] = []
        for claim in projection.claims:
            limitations.extend(claim.limitations)
            if claim.does_not_prove:
                does_not_prove.append(claim.does_not_prove)
        semantic: dict[str, int] = {}
        for relationship in projection.relationships:
            key = getattr(
                getattr(relationship, "semantic_relationship", None),
                "value",
                str(getattr(relationship, "semantic_relationship", "")),
            )
            semantic[key] = semantic.get(key, 0) + 1

        common = {
            "project_id": project_id,
            "usage_scope": FIXED_USAGE_SCOPE_VALUE,
            "projection_fingerprint": projection.projection_fingerprint,
            "presentation_policy_identifier": projection.policy_identifier,
            "presentation_policy_version": projection.policy_version,
            "presentation_policy_fingerprint": projection.policy_fingerprint,
            "counts": {
                "source_count": projection.counts.source_count,
                "claim_count": projection.counts.claim_count,
                "evidence_count": projection.counts.evidence_count,
                "relationship_count": projection.counts.relationship_count,
            },
            "source_identities": [
                {
                    "source_snapshot_id": source.source_snapshot_id,
                    "citation_label": source.citation_label,
                }
                for source in projection.sources
            ],
            "relationship_semantics": semantic,
            "limitations": limitations,
            "does_not_prove": does_not_prove,
            "prompt_budget_bytes": rc.RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES,
        }

        # A-4A anchors emptiness on relationships; mirror it exactly and never
        # render a partial block.
        block_status, rendered_bytes = _classify_projection_block(projection)
        common.update(
            block_status=block_status, rendered_utf8_bytes=rendered_bytes
        )
        return common

    return _run_readonly("projection-preview", stream, build)


# ═══════════════════════════ command: trace-inspect ═══════════════════════════


# The trace records exactly what A-4A persisted; it never simulates a fresh
# consumption and never infers historical use from the current A-3 projection.
_TRACE_NOT_RECORDED = "not_recorded"
_TRACE_NOT_APPLICABLE = "not_applicable"
_TRACE_INVALID_STATE = "invalid_state"

# NOTE: the canonical ``ResearchEvidenceImpactSummary`` does not expose the
# rendered block byte size, so trace-inspect deliberately omits a per-phase
# ``rendered_utf8_bytes`` rather than emitting a fabricated 0/null. The current
# rendered byte size lives in ``projection-preview`` (``rendered_utf8_bytes``).


def _empty_phase_trace(status: str) -> dict:
    return {
        "status": status,
        "consumed": False,
        "projection_fingerprint": "",
        "policy_identifier": "",
        "policy_version": "",
        "usage_scope": FIXED_USAGE_SCOPE_VALUE,
        "blocked_reason": "",
        "counts": {
            "source_count": 0,
            "claim_count": 0,
            "evidence_count": 0,
            "relationship_count": 0,
        },
        "sources": [],
    }


def _phase_trace_from_impact(impact) -> dict:
    return {
        "status": impact.status,
        "consumed": impact.consumed,
        "projection_fingerprint": impact.projection_fingerprint,
        "policy_identifier": impact.policy_identifier,
        "policy_version": impact.policy_version,
        "usage_scope": impact.usage_scope,
        "blocked_reason": impact.blocked_reason,
        "counts": {
            "source_count": impact.source_count,
            "claim_count": impact.claim_count,
            "evidence_count": impact.evidence_count,
            "relationship_count": impact.relationship_count,
        },
        "sources": [
            {
                "source_snapshot_id": source.source_snapshot_id,
                "citation_label": source.citation_label,
            }
            for source in impact.sources
        ],
        "overview": impact.overview,
    }


def cmd_trace_inspect(args, stream) -> int:
    """Report the Research Evidence consumption A-4A *actually persisted*.

    This reads the persisted ``ProjectState`` from ``state_snapshots`` through
    the read-only bridge connection and reports the stored attestation via the
    canonical :func:`build_phase_research_evidence_impact`. It never runs the
    live A-4A consumer entrypoint, never opens a write-capable pool or creates
    tables (so it avoids the async project-store loader entirely), and never
    infers historical consumption from the current A-3 projection.

    Because the loader is avoided, ``state_snapshots`` may not exist at all. An
    absent relation is a legitimate "nothing was ever persisted" and is reported
    as ``state_present=false`` / ``state_valid=null`` with both consumer phases
    ``not_recorded`` — it is detected by a read-only existence probe, never by
    reinterpreting a database error.

    "Malformed persisted state" has two forms, both reported as
    ``state_valid=false`` / ``invalid_state`` (never a generic error, never a
    live-projection fallback): ``state_json`` that fails ``ProjectState``
    validation, and ProjectState-valid JSON whose nested persisted Research
    Evidence attestation cannot be reconstructed by the canonical impact builder
    (ProjectState does not type its policy events).
    """
    import research_evidence_context as rc
    from state import ProjectState

    project_id = _require_arg(getattr(args, "project_id", None), "--project-id")
    _require_research_evidence_enabled()

    def build(conn) -> dict:
        # ``state_snapshots`` belongs to the project store, not to this tool: it
        # is created when state persistence first initializes. Until then the
        # relation does not exist, and its absence means exactly one thing —
        # there is no persisted consumption attestation to report. That is a
        # normal absence, not an error, so it is resolved with a read-only
        # catalog existence probe BEFORE the SELECT. Probing first (rather than
        # recovering from a failed statement) keeps the read-only connection's
        # transaction valid and requires no exception handling, so no arbitrary
        # database error can ever be reinterpreted as absence. This tool still
        # never creates the relation and never uses the project-store loader.
        state_table_present = bool(
            conn.execute(
                "SELECT to_regclass('state_snapshots') IS NOT NULL"
            ).fetchone()[0]
        )
        row = (
            conn.execute(
                "SELECT state_json FROM state_snapshots WHERE project_id = %s::uuid",
                (project_id,),
            ).fetchone()
            if state_table_present
            else None
        )
        state_present = row is not None
        state = None
        state_valid: Optional[bool] = None
        warnings: list[str] = []
        if not state_table_present:
            # Bounded, non-secret: distinguishes "state persistence has never
            # initialized" from "this project simply has no persisted state".
            warnings.append(
                "state persistence is not initialized (no state_snapshots "
                "relation); no consumption attestation has been recorded"
            )
        if state_present:
            raw = row[0]
            try:
                data = raw if isinstance(raw, dict) else json.loads(raw)
                state = ProjectState.model_validate(data)
                state_valid = True
            except Exception as exc:
                # A malformed persisted state cannot attest anything. Surface it
                # as an explicit invalid state — never fabricate a consumption
                # record and never fall back to the current projection.
                state = None
                state_valid = False
                warnings.append(
                    "persisted ProjectState failed validation "
                    f"({type(exc).__name__}); phases reported as invalid_state"
                )

        # Decoding the persisted Research Evidence attestation is itself a
        # validation boundary, distinct from ProjectState model validation.
        # ProjectState does NOT type its nested policy events
        # (``policy_audit_log: list[dict]``), so a model-valid state can still
        # carry a malformed ``research_evidence_consumption`` attestation the
        # canonical builder cannot reconstruct (e.g. a non-numeric count that
        # makes ``_impact_from_event_details`` raise). That is corrupt *persisted
        # history*, exactly like a ``state_json`` that fails ProjectState
        # validation, and is reported identically: ``state_valid=False`` with
        # every consumer phase ``invalid_state``. Both phase impacts are built
        # up-front, inside this boundary, so ONE malformed attestation
        # invalidates the whole history rather than leaving a partially trusted
        # trace. The boundary wraps ONLY the pure-Python impact projection over
        # the already-read state — never the database read — so a database error
        # can never be reinterpreted as malformed state.
        phase_impacts: Optional[dict[str, Any]] = None
        if state is not None:
            try:
                phase_impacts = {
                    phase: rc.build_phase_research_evidence_impact(state, phase)
                    for phase in ("audit", "strategy")
                }
            except Exception as exc:
                phase_impacts = None
                state_valid = False
                warnings.append(
                    "persisted Research Evidence attestation could not be "
                    f"reconstructed ({type(exc).__name__}); phases reported as "
                    "invalid_state"
                )

        phases: dict[str, dict] = {}
        for phase in ("audit", "strategy"):
            if state_present and state_valid is False:
                phases[phase] = _empty_phase_trace(_TRACE_INVALID_STATE)
                continue
            impact = phase_impacts.get(phase) if phase_impacts is not None else None
            phases[phase] = (
                _phase_trace_from_impact(impact)
                if impact is not None
                else _empty_phase_trace(_TRACE_NOT_RECORDED)
            )
        # report never consumes Research Evidence.
        phases["report"] = _empty_phase_trace(_TRACE_NOT_APPLICABLE)

        payload = {
            "project_id": project_id,
            "usage_scope": FIXED_USAGE_SCOPE_VALUE,
            "consumer_phases": list(rc.RESEARCH_EVIDENCE_CONSUMER_PHASES),
            "state_present": state_present,
            "state_valid": state_valid,
            "phases": phases,
            "report_phase_consumes": phases["report"]["consumed"],
        }
        if warnings:
            payload["warnings"] = warnings
        return payload

    return _run_readonly("trace-inspect", stream, build)


# ═══════════════════════════ argparse wiring ═══════════════════════════


def _add_common(parser) -> None:
    parser.add_argument("--json", action="store_true", help="(default) JSON output")


def _add_write_common(parser) -> None:
    _add_common(parser)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="persist the write (default: dry-run / rollback)",
    )
    parser.add_argument(
        "--expect-runtime-fingerprint",
        help=(
            "required runtime identity fingerprint (from `preflight`); the write "
            "is refused unless it matches the live connection"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research_evidence_bridge",
        description=(
            "Bounded operator bridge for canonical Research Evidence "
            "(usage_scope is fixed to internal_analysis)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # preflight
    p = sub.add_parser("preflight", help="check prerequisites without mutation")
    _add_common(p)
    p.add_argument("--project-id")
    p.add_argument("--source-snapshot-id")
    p.add_argument("--expect-database")
    p.set_defaults(func=cmd_preflight)

    # project-show
    p = sub.add_parser("project-show", help="show project parent")
    _add_common(p)
    p.add_argument("--project-id", required=True)
    p.set_defaults(func=cmd_project_show)

    # source-list
    p = sub.add_parser("source-list", help="list captured source snapshots")
    _add_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_source_list)

    # source-metadata-create
    p = sub.add_parser("source-metadata-create", help="attach source metadata")
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--source-snapshot-id", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--canonical-source-locator", default="")
    p.add_argument("--publisher", default="")
    p.add_argument("--author", default="")
    p.add_argument("--citation-label", default="")
    p.add_argument("--declared-quality-tier", default="")
    p.add_argument("--declared-quality-rationale", default="")
    p.set_defaults(func=cmd_source_metadata_create)

    # fact-create (fact + metadata combined)
    p = sub.add_parser(
        "fact-create",
        help="create a validated candidate fact and its metadata revision",
    )
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--source-snapshot-id", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--fact-type", required=True)
    p.add_argument("--value")
    p.add_argument("--text")
    p.add_argument("--unit", default="")
    p.add_argument("--currency-code")
    p.add_argument("--as-of-date")
    p.add_argument("--numerator-context")
    p.add_argument("--denominator-context")
    p.add_argument("--percentage-basis")
    p.add_argument("--percentage-subtype")
    p.add_argument("--time-unit")
    p.add_argument("--counted-entity")
    p.add_argument("--citation-locator", default="")
    p.add_argument("--source-char-range")
    p.add_argument("--excerpt-hash", default="")
    p.add_argument("--stable-fact-key", default="")
    p.set_defaults(func=cmd_fact_create)

    # claim-create
    p = sub.add_parser("claim-create", help="create a draft claim")
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--claim-text", required=True)
    p.add_argument("--claim-category", default="")
    p.set_defaults(func=cmd_claim_create)

    # intake-create
    p = sub.add_parser("intake-create", help="create a draft intake")
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--source-snapshot-id", required=True)
    p.add_argument("--source-metadata-revision-id", required=True)
    p.add_argument("--selection-reason", required=True)
    p.set_defaults(func=cmd_intake_create)

    # intake-item-create
    p = sub.add_parser("intake-item-create", help="create a draft intake item")
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--research-evidence-intake-id", required=True)
    p.add_argument("--item-kind", required=True, choices=("candidate_fact", "claim_draft"))
    p.add_argument("--candidate-fact-revision-id")
    p.add_argument("--fact-metadata-revision-id")
    p.add_argument("--claim-draft-id")
    p.set_defaults(func=cmd_intake_item_create)

    # review-record
    p = sub.add_parser("review-record", help="record an item review decision")
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--research-evidence-intake-item-id", required=True)
    p.add_argument(
        "--decision-type",
        required=True,
        choices=("approved", "rejected", "needs_revision", "withdrawn"),
    )
    p.add_argument("--decision-reason", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--request-id", required=True)
    p.set_defaults(func=cmd_review_record)

    # freshness-record
    p = sub.add_parser("freshness-record", help="record an item freshness assessment")
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--research-evidence-intake-item-id", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument("--policy-identifier", required=True)
    p.add_argument("--policy-version", required=True)
    p.add_argument("--policy-parameters-json", default="{}")
    p.add_argument("--policy-fingerprint", default="")
    p.add_argument("--evaluator-version", required=True)
    p.add_argument("--basis-timestamp", required=True)
    p.add_argument("--fresh-through", required=True)
    p.add_argument(
        "--drift-status",
        required=True,
        choices=(
            "not_assessed",
            "no_material_drift",
            "material_drift",
            "indeterminate",
        ),
    )
    p.add_argument("--drift-reason", required=True)
    p.add_argument("--actor", required=True)
    p.set_defaults(func=cmd_freshness_record)

    # claim-support-record
    p = sub.add_parser("claim-support-record", help="record a pair support assessment")
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--claim-intake-item-id", required=True)
    p.add_argument("--evidence-intake-item-id", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument(
        "--locator-resolution",
        required=True,
        choices=("not_assessed", "resolvable", "unresolvable", "indeterminate"),
    )
    p.add_argument("--locator-rationale", required=True)
    p.add_argument(
        "--evidence-linkage",
        required=True,
        choices=("not_assessed", "linked", "not_linked", "indeterminate"),
    )
    p.add_argument("--evidence-linkage-rationale", required=True)
    p.add_argument(
        "--semantic-relationship",
        required=True,
        choices=(
            "not_assessed",
            "support",
            "contradiction",
            "qualification",
            "insufficient_evidence",
        ),
    )
    p.add_argument("--semantic-relationship-rationale", required=True)
    p.add_argument("--actor", required=True)
    p.set_defaults(func=cmd_claim_support_record)

    # annotation-record
    p = sub.add_parser("annotation-record", help="record a claim annotation revision")
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--claim-draft-id", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument(
        "--epistemic-status",
        required=True,
        choices=(
            "reported_fact",
            "observation",
            "estimate",
            "inference",
            "assumption",
            "hypothesis",
        ),
    )
    p.add_argument(
        "--confidence-label",
        required=True,
        choices=("high", "medium", "low", "unknown"),
    )
    p.add_argument("--decision-relevance", required=True)
    p.add_argument("--supports-statement", required=True)
    p.add_argument("--does-not-prove", required=True)
    p.add_argument("--limitations", help="JSON array or '||'-delimited")
    p.add_argument("--related-claim-draft-ids", help="JSON array or '||'-delimited")
    p.add_argument("--actor", required=True)
    p.set_defaults(func=cmd_annotation_record)

    # context-record
    p = sub.add_parser("context-record", help="record a project context revision")
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument("--research-question", required=True)
    p.add_argument("--project-limitations", help="JSON array or '||'-delimited")
    p.add_argument("--unresolved-gaps", help="JSON array or '||'-delimited")
    p.add_argument("--actor", required=True)
    p.set_defaults(func=cmd_context_record)

    # authorization-list
    p = sub.add_parser("authorization-list", help="list effective authorizations")
    _add_common(p)
    p.add_argument("--project-id", required=True)
    p.set_defaults(func=cmd_authorization_list)

    # authorize-internal-analysis
    p = sub.add_parser(
        "authorize-internal-analysis",
        help="authorize a pair for internal_analysis only (typed confirmation)",
    )
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--claim-intake-item-id", required=True)
    p.add_argument("--evidence-intake-item-id", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument(
        "--confirm",
        help="typed confirmation: '<project_id> <claim_item_id> <evidence_item_id>'",
    )
    p.set_defaults(func=cmd_authorize_internal_analysis)

    # revoke-internal-analysis
    p = sub.add_parser(
        "revoke-internal-analysis",
        help="revoke an internal_analysis authorization (typed confirmation)",
    )
    _add_write_common(p)
    p.add_argument("--project-id", required=True)
    p.add_argument("--claim-intake-item-id", required=True)
    p.add_argument("--evidence-intake-item-id", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--confirm")
    p.set_defaults(func=cmd_revoke_internal_analysis)

    # pack-preview
    p = sub.add_parser("pack-preview", help="preview the internal_analysis A-2 pack")
    _add_common(p)
    p.add_argument("--project-id", required=True)
    p.set_defaults(func=cmd_pack_preview)

    # projection-preview
    p = sub.add_parser(
        "projection-preview", help="preview the A-3 projection + byte budget"
    )
    _add_common(p)
    p.add_argument("--project-id", required=True)
    p.set_defaults(func=cmd_projection_preview)

    # trace-inspect
    p = sub.add_parser(
        "trace-inspect",
        help="report the persisted A-4A consumption attestation per phase",
    )
    _add_common(p)
    p.add_argument("--project-id", required=True)
    p.set_defaults(func=cmd_trace_inspect)

    return parser


def main(argv: Optional[list[str]] = None, *, stream=None) -> int:
    stream = stream or sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args, stream)
    except BridgeError as exc:
        _emit(
            {
                "command": getattr(args, "command", ""),
                "status": "error",
                "committed": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            stream,
        )
        return EXIT_FAILURE
    except Exception as exc:
        # Never leak DSNs/secrets: report only the exception class and a bounded
        # message, not a traceback.
        _emit(
            {
                "command": getattr(args, "command", ""),
                "status": "error",
                "committed": False,
                "error_type": type(exc).__name__,
            },
            stream,
        )
        return EXIT_FAILURE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
