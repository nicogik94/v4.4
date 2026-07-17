"""Test-only PostgreSQL support for Slice A evidence-snapshot tests.

Applies the real bootstrap ordering (init.sql -> outcomes.sql ->
v47_evidence_snapshot_foundation.sql) against a genuine PostgreSQL database and
provides schema introspection used purely by test assertions for bootstrap,
complete-reapply, and partial-schema state.

This helper exists only under tests/. It is NOT a production migration runner and
ships no runtime schema-state management. The disposable database DSN is supplied
via the TEST_EVIDENCE_PG_DSN environment variable (dependency injection); when it
is unset the Slice A PostgreSQL tests are skipped. R1.6A v59 migrations use the
separate TEST_EVIDENCE_MIGRATION_PG_DSN variable so PostgreSQL authenticates a
genuine workflow_migration_owner login instead of a bootstrap session using
SET ROLE.
"""
from __future__ import annotations

import contextlib
import os
import secrets
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Iterator, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"

INIT_SQL = SQL_DIR / "init.sql"
OUTCOMES_SQL = SQL_DIR / "outcomes.sql"
V47_SQL = SQL_DIR / "v47_evidence_snapshot_foundation.sql"
V48_SQL = SQL_DIR / "v48_automation_roi_foundation.sql"
V49_SQL = SQL_DIR / "v49_automation_roi_calculation_idempotency.sql"
V51_RESEARCH_SQL = SQL_DIR / "v51_research_evidence_sidecar_foundation.sql"
V52_RESEARCH_SQL = SQL_DIR / "v52_research_evidence_audit_integrity.sql"
V53_RESEARCH_INTAKE_SQL = SQL_DIR / "v53_research_evidence_intake_foundation.sql"
V54_RESEARCH_REVIEW_SQL = SQL_DIR / "v54_research_evidence_review_foundation.sql"
V55_RESEARCH_FRESHNESS_SQL = (
    SQL_DIR / "v55_research_evidence_freshness_foundation.sql"
)
V56_RESEARCH_CLAIM_SUPPORT_SQL = (
    SQL_DIR / "v56_research_evidence_claim_support_foundation.sql"
)
V57_RESEARCH_BINDING_SQL = (
    SQL_DIR / "v57_research_evidence_binding_foundation.sql"
)
V58_RESEARCH_SCENARIO_INPUT_EVALUATION_SQL = (
    SQL_DIR
    / "v58_research_evidence_scenario_input_evaluation_foundation.sql"
)
V59_RESEARCH_AUTOMATION_ROI_USE_SQL = (
    SQL_DIR / "v59_research_evidence_automation_roi_input_snapshot.sql"
)
V60_RESEARCH_AUTOMATION_ROI_EXECUTION_SQL = (
    SQL_DIR / "v60_research_evidence_automation_roi_execution.sql"
)
V61_RESEARCH_EVIDENCE_PACK_SQL = (
    SQL_DIR / "v61_research_evidence_pack_foundation.sql"
)

# Slice B (Automation ROI) objects — used by the v48 schema tests.
SLICE_B_TABLES = (
    "candidate_fact_extraction_context",
    "candidate_fact_approval_decision",
    "approved_calculation_input",
    "calculation_result",
    "calculation_result_input",
)

DSN_ENV = "TEST_EVIDENCE_PG_DSN"
MIGRATION_DSN_ENV = "TEST_EVIDENCE_MIGRATION_PG_DSN"
MIGRATION_OWNER = "workflow_migration_owner"
RUNTIME_DSN_ENV = "TEST_EVIDENCE_RUNTIME_PG_DSN"
RUNTIME_ROLE = "workflow_automation_roi_runtime"

EXTERNAL_ROLE_MANIFEST = {
    "workflow_migration_owner": {
        "login": True, "inherit": False, "superuser": False,
        "createdb": False, "createrole": False, "replication": False,
        "bypassrls": False,
    },
    "workflow_research_evidence_owner": {
        "login": False, "inherit": False, "superuser": False,
        "createdb": False, "createrole": False, "replication": False,
        "bypassrls": False,
    },
    "workflow_automation_roi_runtime": {
        "login": True, "inherit": False, "superuser": False,
        "createdb": False, "createrole": False, "replication": False,
        "bypassrls": False,
    },
}
EXTERNAL_MEMBERSHIP_MANIFEST = (
    (
        "workflow_research_evidence_owner",
        "workflow_migration_owner",
        False,
        False,
        True,
    ),
)
_EXTERNAL_ROLE_NAMES = tuple(EXTERNAL_ROLE_MANIFEST)


class ExternalRoleInfrastructureError(RuntimeError):
    """Disposable-cluster role prerequisites are absent or divergent."""


def _role_rows(conn):
    return conn.execute(
        """SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,
                  rolcreaterole,rolreplication,rolbypassrls,rolconnlimit,
                  rolvaliduntil
           FROM pg_catalog.pg_roles
           WHERE rolname=ANY(%s::text[]) ORDER BY rolname""",
        (list(_EXTERNAL_ROLE_NAMES),),
    ).fetchall()


def external_role_attributes(conn):
    attributes = {}
    for row in _role_rows(conn):
        attributes[row[0]] = {
            "login": row[1], "inherit": row[2], "superuser": row[3],
            "createdb": row[4], "createrole": row[5],
            "replication": row[6], "bypassrls": row[7],
        }
    return attributes


def external_role_memberships(conn):
    return tuple(conn.execute(
        """SELECT granted.rolname,member.rolname,membership.admin_option,
                  membership.inherit_option,membership.set_option
           FROM pg_catalog.pg_auth_members membership
           JOIN pg_catalog.pg_roles granted ON granted.oid=membership.roleid
           JOIN pg_catalog.pg_roles member ON member.oid=membership.member
           WHERE granted.rolname=ANY(%s::text[])
              OR member.rolname=ANY(%s::text[])
           ORDER BY granted.rolname,member.rolname""",
        (list(_EXTERNAL_ROLE_NAMES), list(_EXTERNAL_ROLE_NAMES)),
    ).fetchall())


def external_role_snapshot(conn):
    roles = tuple(conn.execute(
        """SELECT oid,rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,
                  rolcreaterole,rolreplication,rolbypassrls,rolconnlimit,
                  rolvaliduntil
           FROM pg_catalog.pg_roles
           WHERE rolname=ANY(%s::text[]) ORDER BY rolname""",
        (list(_EXTERNAL_ROLE_NAMES),),
    ).fetchall())
    memberships = tuple(conn.execute(
        """SELECT membership.roleid,membership.member,membership.grantor,
                  membership.admin_option,membership.inherit_option,
                  membership.set_option
           FROM pg_catalog.pg_auth_members membership
           JOIN pg_catalog.pg_roles granted ON granted.oid=membership.roleid
           JOIN pg_catalog.pg_roles member ON member.oid=membership.member
           WHERE granted.rolname=ANY(%s::text[])
              OR member.rolname=ANY(%s::text[])
           ORDER BY membership.roleid,membership.member""",
        (list(_EXTERNAL_ROLE_NAMES), list(_EXTERNAL_ROLE_NAMES)),
    ).fetchall())
    return roles, memberships


def cluster_role_oids(conn):
    return tuple(row[0] for row in conn.execute(
        "SELECT oid FROM pg_catalog.pg_roles ORDER BY oid"
    ).fetchall())


class ExternalRoleHarness:
    def __init__(self, cluster_identity, connection_parameters, *, credentials=True):
        self.cluster_identity = cluster_identity
        self._connection_parameters = dict(connection_parameters)
        self._credentials = {}
        self._created_roles = set()
        self._schemas = set()
        self.credential_generation_counts = {}
        if credentials:
            for role in (MIGRATION_OWNER, RUNTIME_ROLE):
                self._credentials[role] = secrets.token_urlsafe(32)
                self.credential_generation_counts[role] = 1

    @classmethod
    def for_unknown_provenance_test(cls, conn):
        return cls(
            ("unknown-provenance", uuid.uuid4().hex),
            _connection_parameters(conn), credentials=False,
        )

    def bind_schema(self, schema):
        self._schemas.add(schema)

    def provision(self, conn):
        psycopg = psycopg_module()
        prior = _begin_autocommit(conn)
        try:
            actual = external_role_attributes(conn)
            for role, expected in EXTERNAL_ROLE_MANIFEST.items():
                if role in actual:
                    if actual[role] != expected:
                        raise ExternalRoleInfrastructureError(
                            f"external role attribute divergence: {role}"
                        )
                    if expected["login"] and (
                        role not in self._created_roles
                        or role not in self._credentials
                    ):
                        raise ExternalRoleInfrastructureError(
                            f"external login role credential provenance is unknown: {role}"
                        )
                    continue
                role_sql = psycopg.sql.SQL(
                    "CREATE ROLE {} {} NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(
                    psycopg.sql.Identifier(role),
                    psycopg.sql.SQL("LOGIN" if expected["login"] else "NOLOGIN"),
                )
                if expected["login"]:
                    role_sql += psycopg.sql.SQL(" PASSWORD {}").format(
                        psycopg.sql.Literal(self._credentials[role])
                    )
                conn.execute(role_sql)
                self._created_roles.add(role)

            memberships = external_role_memberships(conn)
            if not memberships:
                conn.execute(
                    psycopg.sql.SQL(
                        "GRANT {} TO {} "
                        "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
                    ).format(
                        psycopg.sql.Identifier("workflow_research_evidence_owner"),
                        psycopg.sql.Identifier(MIGRATION_OWNER),
                    )
                )
            elif memberships != EXTERNAL_MEMBERSHIP_MANIFEST:
                raise ExternalRoleInfrastructureError(
                    "external role membership divergence"
                )

            database = conn.execute(
                "SELECT pg_catalog.current_database()"
            ).fetchone()[0]
            if conn.execute(
                "SELECT has_database_privilege(%s,%s,'CREATE')",
                (MIGRATION_OWNER, database),
            ).fetchone()[0]:
                raise ExternalRoleInfrastructureError(
                    "workflow_migration_owner unexpectedly has database CREATE"
                )
            if external_role_attributes(conn) != EXTERNAL_ROLE_MANIFEST:
                raise ExternalRoleInfrastructureError(
                    "external role provisioning verification failed"
                )
            if external_role_memberships(conn) != EXTERNAL_MEMBERSHIP_MANIFEST:
                raise ExternalRoleInfrastructureError(
                    "external membership provisioning verification failed"
                )
        finally:
            _restore_autocommit(conn, prior)
        return self

    @contextlib.contextmanager
    def _role_connection(self, role, schema, *, autocommit):
        psycopg = psycopg_module()
        connection = self.authenticated_connection(role)
        try:
            connection.autocommit = autocommit
            connection.execute(
                psycopg.sql.SQL("SET search_path TO {}").format(
                    psycopg.sql.Identifier(schema)
                )
            )
            identity = connection.execute(
                "SELECT session_user,current_user"
            ).fetchone()
            if identity != (role, role):
                raise ExternalRoleInfrastructureError(
                    f"genuine external role authentication failed: {role}"
                )
            if not autocommit:
                connection.commit()
            yield connection
        finally:
            with contextlib.suppress(Exception):
                connection.rollback()
            connection.close()

    def migration_connection(self, schema):
        return self._role_connection(MIGRATION_OWNER, schema, autocommit=True)

    def runtime_connection(self, schema):
        return self._role_connection(RUNTIME_ROLE, schema, autocommit=False)

    def authenticated_connection(self, role):
        if role not in self._credentials or role not in self._created_roles:
            raise ExternalRoleInfrastructureError(
                f"external login role credential provenance is unknown: {role}"
            )
        parameters = dict(self._connection_parameters)
        parameters.update(user=role, password=self._credentials[role])
        return _actual_psycopg_module().connect(**parameters)


_external_role_lock = threading.Lock()
_external_role_harnesses = {}
_external_schema_harnesses = {}
_active_external_role_harness = None
_MIGRATION_CONNECTION_TOKEN = "in-memory-role:workflow-migration-owner"
_RUNTIME_CONNECTION_TOKEN = "in-memory-role:workflow-automation-roi-runtime"


def _connection_parameters(conn):
    return {
        "host": conn.info.host,
        "port": conn.info.port,
        "dbname": conn.info.dbname,
    }


def _cluster_identity(conn):
    system_identifier = conn.execute(
        "SELECT system_identifier::text FROM pg_catalog.pg_control_system()"
    ).fetchone()[0]
    return system_identifier, conn.info.host, conn.info.port


def ensure_external_role_prerequisites(conn):
    global _active_external_role_harness
    identity = _cluster_identity(conn)
    with _external_role_lock:
        harness = _external_role_harnesses.get(identity)
        if harness is None:
            harness = ExternalRoleHarness(identity, _connection_parameters(conn))
            harness.provision(conn)
            _external_role_harnesses[identity] = harness
        else:
            harness.provision(conn)
        _active_external_role_harness = harness
        os.environ[MIGRATION_DSN_ENV] = _MIGRATION_CONNECTION_TOKEN
        os.environ[RUNTIME_DSN_ENV] = _RUNTIME_CONNECTION_TOKEN
        return harness


class _PsycopgProxy:
    def __init__(self, module):
        self._module = module

    def __getattr__(self, name):
        return getattr(self._module, name)

    def connect(self, conninfo="", **kwargs):
        if conninfo in (_MIGRATION_CONNECTION_TOKEN, _RUNTIME_CONNECTION_TOKEN):
            harness = _active_external_role_harness
            if harness is None:
                raise ExternalRoleInfrastructureError(
                    "in-memory external-role credential owner is unavailable"
                )
            role = (
                MIGRATION_OWNER
                if conninfo == _MIGRATION_CONNECTION_TOKEN
                else RUNTIME_ROLE
            )
            return harness.authenticated_connection(role)
        return self._module.connect(conninfo, **kwargs)


_psycopg_proxy = None


def _actual_psycopg_module():
    try:
        import psycopg
    except ImportError:  # pragma: no cover - environment guard
        pytest.skip("psycopg is not installed; Slice A PostgreSQL tests require it")
    return psycopg


@contextlib.contextmanager
def dedicated_negative_role_cluster():
    psycopg = psycopg_module()
    container = f"mas-r2a1-negative-role-{uuid.uuid4().hex[:12]}"
    database = f"mas_research_evidence_test_negative_{uuid.uuid4().hex[:10]}"
    bootstrap_password = secrets.token_urlsafe(32)
    environment = os.environ.copy()
    environment["POSTGRES_PASSWORD"] = bootstrap_password
    command = [
        "docker", "run", "-d", "--pull=never", "--name", container,
        "-e", "POSTGRES_PASSWORD", "-e", f"POSTGRES_DB={database}",
        "-p", "127.0.0.1::5432", "--tmpfs", "/var/lib/postgresql/data",
        "postgres:16-alpine",
    ]
    subprocess.run(
        command, env=environment, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            ready = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", "postgres",
                 "-d", database],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.05)
        else:
            raise ExternalRoleInfrastructureError(
                "dedicated negative-role PostgreSQL cluster did not become ready"
            )
        port_output = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        port = int(port_output.rsplit(":", 1)[1])
        connection = None
        for _ in range(100):
            try:
                connection = psycopg.connect(
                    host="127.0.0.1", port=port, dbname=database,
                    user="postgres", password=bootstrap_password,
                )
                break
            except psycopg.OperationalError:
                time.sleep(0.05)
        if connection is None:
            raise ExternalRoleInfrastructureError(
                "dedicated negative-role PostgreSQL connection was unavailable"
            ) from None
        connection.autocommit = True
        try:
            yield connection
        finally:
            connection.close()
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

SLICE_A_TABLES = (
    "source_blob",
    "source_snapshot",
    "candidate_fact_revision",
    "evidence_retention_event",
    "ingest_operation",
)
IMMUTABLE_TABLES = (
    "source_blob",
    "source_snapshot",
    "candidate_fact_revision",
    "evidence_retention_event",
)
SLICE_A_TRIGGERS = (
    ("trg_source_blob_no_mutation", "source_blob"),
    ("trg_source_snapshot_no_mutation", "source_snapshot"),
    ("trg_cfr_no_mutation", "candidate_fact_revision"),
    ("trg_retention_no_mutation", "evidence_retention_event"),
)
XOR_CONSTRAINT = "ck_retention_single_target"
REJECT_FUNCTION = "slicea_reject_mutation"


def dsn() -> Optional[str]:
    return os.getenv(DSN_ENV)


def require_dsn() -> str:
    value = dsn()
    if not value:
        pytest.skip(f"{DSN_ENV} not set; Slice A PostgreSQL tests require a disposable database")
    return value


def psycopg_module():
    global _psycopg_proxy
    if _psycopg_proxy is None:
        _psycopg_proxy = _PsycopgProxy(_actual_psycopg_module())
    return _psycopg_proxy


def connect(*, schema: Optional[str] = None, autocommit: bool = False):
    """Open a fresh connection, optionally pinned to a test schema search_path."""
    psycopg = psycopg_module()
    conn = psycopg.connect(require_dsn())
    conn.autocommit = autocommit
    ensure_external_role_prerequisites(conn)
    if schema is not None:
        conn.execute(f'SET search_path TO "{schema}"')
        if not autocommit:
            conn.commit()
    return conn


def _begin_autocommit(conn) -> bool:
    """Switch to autocommit, leaving any in-progress transaction first."""
    prior = conn.autocommit
    if not prior:
        conn.rollback()  # autocommit cannot be toggled while INTRANS
    conn.autocommit = True
    return prior


def _restore_autocommit(conn, prior: bool) -> None:
    try:
        conn.autocommit = prior
    except Exception:
        # An aborted (INERROR) transaction blocks toggling; clear and retry.
        conn.rollback()
        conn.autocommit = prior


def _run_script(conn, path: Path) -> None:
    # psycopg3 permits multiple statements in one execute() when there are no
    # parameters; the connection is in autocommit so the script's own
    # transaction boundary (v47) is respected.
    conn.execute(path.read_text(encoding="utf-8"))


def apply_full_schema(conn, schema: str) -> None:
    """Create an isolated schema and apply init -> outcomes -> v47 into it."""
    prior = _begin_autocommit(conn)
    conn.execute(f'CREATE SCHEMA "{schema}"')
    conn.execute(f'SET search_path TO "{schema}"')
    _run_script(conn, INIT_SQL)
    _run_script(conn, OUTCOMES_SQL)
    _run_script(conn, V47_SQL)
    _restore_autocommit(conn, prior)


def apply_v47(conn) -> None:
    """(Re)apply only the Slice A migration into the current search_path schema."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V47_SQL)
    _restore_autocommit(conn, prior)


def apply_v48(conn) -> None:
    """(Re)apply only the Slice B migration into the current search_path schema."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V48_SQL)
    _restore_autocommit(conn, prior)


def apply_v49(conn) -> None:
    """(Re)apply only the Slice B v49 idempotency migration into the current schema."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V49_SQL)
    _restore_autocommit(conn, prior)


def apply_v51_research(conn) -> None:
    """(Re)apply only the R1.1 research sidecar migration into the current schema."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V51_RESEARCH_SQL)
    _restore_autocommit(conn, prior)


def apply_v52_research(conn) -> None:
    """(Re)apply only the v52 research audit-integrity migration."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V52_RESEARCH_SQL)
    _restore_autocommit(conn, prior)


def apply_v53_research_intake(conn) -> None:
    """(Re)apply only the v53 controlled research-intake migration."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V53_RESEARCH_INTAKE_SQL)
    _restore_autocommit(conn, prior)


def apply_v54_research_review(conn) -> None:
    """(Re)apply only the v54 controlled item-review migration."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V54_RESEARCH_REVIEW_SQL)
    _restore_autocommit(conn, prior)


def apply_v55_research_freshness(conn) -> None:
    """(Re)apply only the v55 item freshness/drift migration."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V55_RESEARCH_FRESHNESS_SQL)
    _restore_autocommit(conn, prior)


def apply_v56_research_claim_support(conn) -> None:
    """(Re)apply only the v56 pair-scoped claim-support migration."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V56_RESEARCH_CLAIM_SUPPORT_SQL)
    _restore_autocommit(conn, prior)


def apply_v57_research_binding(conn) -> None:
    """(Re)apply only the v57 consumer-input binding migration."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V57_RESEARCH_BINDING_SQL)
    _restore_autocommit(conn, prior)


def apply_v58_research_scenario_input_evaluation(conn) -> None:
    """(Re)apply only the v58 scenario-input evaluation migration."""
    prior = _begin_autocommit(conn)
    try:
        _run_script(conn, V58_RESEARCH_SCENARIO_INPUT_EVALUATION_SQL)
    except Exception:
        conn.rollback()
        raise
    finally:
        _restore_autocommit(conn, prior)


def assign_v59_upstream_migration_ownership(conn, schema: str) -> None:
    """Model production ownership after bootstrap creates disposable topology."""
    psycopg = psycopg_module()
    harness = ensure_external_role_prerequisites(conn)
    harness.bind_schema(schema)
    _external_schema_harnesses[schema] = harness
    prior = _begin_autocommit(conn)
    try:
        conn.execute(
            psycopg.sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(
                psycopg.sql.Identifier(schema),
                psycopg.sql.Identifier(MIGRATION_OWNER),
            )
        )
        for relation in (
            "projects",
            "approved_calculation_input",
            "research_evidence_consumer_input_binding",
            "research_evidence_consumer_input_binding_sequence_allocator",
        ):
            conn.execute(
                psycopg.sql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(
                    psycopg.sql.Identifier(schema),
                    psycopg.sql.Identifier(relation),
                    psycopg.sql.Identifier(MIGRATION_OWNER),
                )
            )
        conn.execute(
            psycopg.sql.SQL("ALTER FUNCTION {}.{}() OWNER TO {}").format(
                psycopg.sql.Identifier(schema),
                psycopg.sql.Identifier("slicea_reject_mutation"),
                psycopg.sql.Identifier(MIGRATION_OWNER),
            )
        )
    finally:
        _restore_autocommit(conn, prior)


def provision_v59_dedicated_schema(conn) -> None:
    """Pre-provision the empty trusted schema without database CREATE grants."""
    psycopg = psycopg_module()
    schema = "research_evidence_automation_roi"
    owner = "workflow_research_evidence_owner"
    runtime = "workflow_automation_roi_runtime"
    prior = _begin_autocommit(conn)
    try:
        conn.execute(
            psycopg.sql.SQL(
                "CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}"
            ).format(
                psycopg.sql.Identifier(schema),
                psycopg.sql.Identifier(owner),
            )
        )
        conn.execute(
            psycopg.sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(
                psycopg.sql.Identifier(schema),
                psycopg.sql.Identifier(owner),
            )
        )
        conn.execute(
            psycopg.sql.SQL("SET ROLE {}").format(
                psycopg.sql.Identifier(owner)
            )
        )
        try:
            conn.execute(
                psycopg.sql.SQL(
                    "REVOKE ALL ON SCHEMA {} FROM PUBLIC"
                ).format(psycopg.sql.Identifier(schema))
            )
            conn.execute(
                psycopg.sql.SQL(
                    "GRANT USAGE ON SCHEMA {} TO {}"
                ).format(
                    psycopg.sql.Identifier(schema),
                    psycopg.sql.Identifier(runtime),
                )
            )
            conn.execute(
                "ALTER DEFAULT PRIVILEGES "
                "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
            )
        finally:
            conn.execute("RESET ROLE")
    finally:
        _restore_autocommit(conn, prior)


def assert_single_v59_upstream_schema(conn) -> str:
    """Require one OID-validated v59 upstream before applying the migration."""
    rows = conn.execute(
        """
        SELECT upstream_namespace.nspname
        FROM pg_catalog.pg_constraint constraint_info
        JOIN pg_catalog.pg_class binding_relation
          ON binding_relation.oid = constraint_info.conrelid
        JOIN pg_catalog.pg_namespace binding_namespace
          ON binding_namespace.oid = binding_relation.relnamespace
        JOIN pg_catalog.pg_class upstream_relation
          ON upstream_relation.oid = constraint_info.confrelid
        JOIN pg_catalog.pg_namespace upstream_namespace
          ON upstream_namespace.oid = upstream_relation.relnamespace
        WHERE constraint_info.conname =
                  'fk_recib_calculation_input_role'
          AND constraint_info.contype = 'f'
          AND constraint_info.connamespace = binding_namespace.oid
          AND binding_relation.relname =
                  'research_evidence_consumer_input_binding'
          AND binding_relation.relkind = 'r'
          AND upstream_relation.relname = 'approved_calculation_input'
          AND upstream_relation.relkind = 'r'
          AND upstream_namespace.oid = binding_namespace.oid
        ORDER BY upstream_namespace.oid
        """
    ).fetchall()
    if len(rows) != 1 or rows[0][0] is None:
        raise RuntimeError(
            "v59 requires exactly one validated upstream schema "
            f"(pytest harness found {len(rows)})"
        )
    return rows[0][0]


@contextlib.contextmanager
def v59_migration_connection(upstream_schema: str):
    """Open the genuine migration-owner login required by the v59 contract."""
    harness = _external_schema_harnesses.get(upstream_schema)
    if harness is not None:
        with harness.migration_connection(upstream_schema) as connection:
            yield connection
        return
    migration_dsn = os.getenv(MIGRATION_DSN_ENV)
    if not migration_dsn:
        raise RuntimeError(f"{MIGRATION_DSN_ENV} is required for v59")
    psycopg = psycopg_module()
    connection = psycopg.connect(migration_dsn)
    try:
        connection.autocommit = True
        connection.execute(
            psycopg.sql.SQL("SET search_path TO {}").format(
                psycopg.sql.Identifier(upstream_schema)
            )
        )
        identity = connection.execute(
            """
            SELECT
                session_user = 'workflow_migration_owner',
                current_user = 'workflow_migration_owner'
            """
        ).fetchone()
        if identity != (True, True):
            raise RuntimeError(
                "v59 requires a genuine workflow_migration_owner login"
            )
        yield connection
    finally:
        with contextlib.suppress(Exception):
            connection.rollback()
        connection.close()


@contextlib.contextmanager
def runtime_connection(upstream_schema: str):
    """Open the genuine runtime login used by R1.6A functional tests."""
    harness = _external_schema_harnesses.get(upstream_schema)
    if harness is not None:
        with harness.runtime_connection(upstream_schema) as connection:
            yield connection
        return
    runtime_dsn = os.getenv(RUNTIME_DSN_ENV)
    if not runtime_dsn:
        raise RuntimeError(f"{RUNTIME_DSN_ENV} is required for R1.6A runtime tests")
    psycopg = psycopg_module()
    connection = psycopg.connect(runtime_dsn)
    try:
        connection.execute(
            psycopg.sql.SQL("SET search_path TO {}").format(
                psycopg.sql.Identifier(upstream_schema)
            )
        )
        identity = connection.execute(
            """
            SELECT
                session_user = 'workflow_automation_roi_runtime',
                current_user = 'workflow_automation_roi_runtime'
            """
        ).fetchone()
        if identity != (True, True):
            raise RuntimeError(
                "R1.6A functional tests require a genuine runtime login"
            )
        connection.commit()
        yield connection
    finally:
        with contextlib.suppress(Exception):
            connection.rollback()
        connection.close()


def apply_v59_research_automation_roi_use(conn) -> None:
    """Apply v59 through a genuine migration-owner login connection."""
    prior = _begin_autocommit(conn)
    try:
        upstream_schema = assert_single_v59_upstream_schema(conn)
    finally:
        _restore_autocommit(conn, prior)
    try:
        with v59_migration_connection(upstream_schema) as migration:
            _run_script(migration, V59_RESEARCH_AUTOMATION_ROI_USE_SQL)
    finally:
        # v59 used to execute on this bootstrap session and its final
        # RESET ROLE restored session_user. Preserve that fixture boundary
        # now that migration execution uses a separate authenticated login.
        prior = _begin_autocommit(conn)
        try:
            conn.execute("RESET ROLE")
        finally:
            _restore_autocommit(conn, prior)


def apply_v60_research_automation_roi_execution(conn) -> None:
    """Apply v60 through the genuine migration-owner login connection."""
    prior = _begin_autocommit(conn)
    try:
        upstream_schema = assert_single_v59_upstream_schema(conn)
    finally:
        _restore_autocommit(conn, prior)
    with v59_migration_connection(upstream_schema) as migration:
        _run_script(migration, V60_RESEARCH_AUTOMATION_ROI_EXECUTION_SQL)


def apply_v51_through_v60_research_topology(conn, schema: str) -> None:
    """Apply the complete approved predecessor topology required by v61.

    ``apply_full_schema`` supplies init.sql -> outcomes.sql -> v47.  The v48
    and v49 foundations are part of the approved disposable baseline because
    v57 has a foreign-key dependency on ``approved_calculation_input``.
    """
    apply_v48(conn)
    apply_v49(conn)
    apply_v51_research(conn)
    apply_v52_research(conn)
    apply_v53_research_intake(conn)
    apply_v54_research_review(conn)
    apply_v55_research_freshness(conn)
    apply_v56_research_claim_support(conn)
    apply_v57_research_binding(conn)
    apply_v58_research_scenario_input_evaluation(conn)
    assign_v59_upstream_migration_ownership(conn, schema)
    provision_v59_dedicated_schema(conn)
    apply_v59_research_automation_roi_use(conn)
    apply_v60_research_automation_roi_execution(conn)


def apply_v61_research_evidence_pack(conn) -> None:
    """(Re)apply only the R2.0A-1 canonical evidence-pack foundation."""
    prior = _begin_autocommit(conn)
    try:
        _run_script(conn, V61_RESEARCH_EVIDENCE_PACK_SQL)
    finally:
        _restore_autocommit(conn, prior)


def slice_b_tables_present(conn, schema: str) -> int:
    """Count how many of the five Slice B tables exist in ``schema``."""
    return sum(1 for t in SLICE_B_TABLES if table_exists(conn, schema, t))


def drop_schema(conn, schema: str) -> None:
    prior = _begin_autocommit(conn)
    conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    _restore_autocommit(conn, prior)


@contextlib.contextmanager
def fresh_schema(conn) -> Iterator[str]:
    """Provide a freshly bootstrapped isolated schema; drop it on exit."""
    schema = f"slicea_test_{uuid.uuid4().hex[:16]}"
    try:
        apply_full_schema(conn, schema)
        conn.autocommit = False
        conn.execute(f'SET search_path TO "{schema}"')
        conn.commit()
        yield schema
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        drop_schema(conn, schema)


# ─────────────────────────────── Introspection ───────────────────────────────

def table_exists(conn, schema: str, table: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s)", (f'"{schema}".{table}',)).fetchone()
    return row[0] is not None


def trigger_exists(conn, schema: str, trigger: str, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND t.tgname = %s
        """,
        (schema, table, trigger),
    ).fetchone()
    return row is not None


def constraint_exists(conn, schema: str, constraint: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM pg_constraint con
        JOIN pg_namespace n ON n.oid = con.connamespace
        WHERE n.nspname = %s AND con.conname = %s
        """,
        (schema, constraint),
    ).fetchone()
    return row is not None


def function_exists(conn, schema: str, function: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = %s AND p.proname = %s
        """,
        (schema, function),
    ).fetchone()
    return row is not None


def classify_schema(conn, schema: str) -> str:
    """Classify Slice A schema state as 'none', 'complete', or 'partial'.

    Test-only introspection: 'none' = no Slice A objects present; 'complete' =
    all five tables, four triggers, the XOR constraint, and the reject function
    present; anything in between is 'partial' (divergent).
    """
    tables_present = [t for t in SLICE_A_TABLES if table_exists(conn, schema, t)]
    triggers_present = [
        trg for trg, tbl in SLICE_A_TRIGGERS if trigger_exists(conn, schema, trg, tbl)
    ]
    xor_present = constraint_exists(conn, schema, XOR_CONSTRAINT)
    fn_present = function_exists(conn, schema, REJECT_FUNCTION)

    nothing = not tables_present and not triggers_present and not fn_present
    if nothing:
        return "none"
    complete = (
        len(tables_present) == len(SLICE_A_TABLES)
        and len(triggers_present) == len(SLICE_A_TRIGGERS)
        and xor_present
        and fn_present
    )
    return "complete" if complete else "partial"


def insert_project(conn, *, name: str = "Slice A project", project_id: Optional[str] = None) -> str:
    if project_id is None:
        row = conn.execute(
            "INSERT INTO projects (name, brief) VALUES (%s, %s) RETURNING id::text",
            (name, ""),
        ).fetchone()
        return row[0]
    conn.execute(
        "INSERT INTO projects (id, name, brief) VALUES (%s, %s, %s)",
        (project_id, name, ""),
    )
    return project_id
