"""The one disposable-PostgreSQL harness the telemetry suites share.

Role provisioning has to live in a module both test files reach through the
*same* import path. pytest imports test modules under a package prefix derived
from the rootdir (``mas.tests.…``) while one test module importing another by
name gets ``tests.…``, and the two are different module objects with different
``_CREDENTIALS`` dictionaries — so the second suite to run would find the roles
already created, have no password for them, and fail every login in a way that
looks exactly like a DSN defect. Both suites import *this* module the same way,
so the generated passwords are shared.

The migration deliberately creates none of the roles it requires, which is why
they are provisioned here rather than by the SQL.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")

from tools import provider_attempt_telemetry_migrate as migrate_tool  # noqa: E402
from tests import pg_dsn  # noqa: E402

DSN_ENV = "TEST_EVIDENCE_PG_DSN"

OWNER_ROLE = migrate_tool.OWNER_ROLE
WRITER_ROLE = migrate_tool.WRITER_ROLE
READER_ROLE = migrate_tool.READER_ROLE
MIGRATION_ROLE = "workflow_provider_telemetry_migrator"

# Populated by _provision_roles the first time it runs in this process.
CREDENTIALS: dict[str, str] = {}


def dsn() -> str:
    value = os.environ.get(DSN_ENV, "").strip()
    if not value:
        pytest.skip(f"{DSN_ENV} is not set; provider-telemetry PostgreSQL tests need one")
    return value


def superuser():
    conn = psycopg.connect(dsn())
    conn.autocommit = True
    return conn


def role_parameters(role: str, **overrides) -> dict[str, str]:
    """Connection parameters for one of the provisioned login roles.

    ``TEST_EVIDENCE_PG_DSN`` may be in any form libpq accepts — CI supplies a
    ``postgresql://`` URI — so parsing goes through libpq itself. Swapping in
    the role's credentials leaves host, port, dbname, sslmode and options
    exactly as the DSN set them.
    """
    return pg_dsn.connection_parameters(
        dsn(),
        source=DSN_ENV,
        user=role,
        password=CREDENTIALS.get(role) or None,
        **overrides,
    )


def connect_as(role: str, schema: str, *, autocommit: bool = True):
    conn = psycopg.connect(**role_parameters(role))
    conn.autocommit = autocommit
    conn.execute(psycopg.sql.SQL("SET search_path TO {}").format(psycopg.sql.Identifier(schema)))
    identity = conn.execute("SELECT current_user").fetchone()[0]
    if identity != role:
        raise RuntimeError(f"expected to authenticate as {role}, got {identity}")
    return conn


def provision_roles() -> None:
    """Create the roles the migration requires but deliberately never creates."""
    with superuser() as conn:
        for role, login in (
            (OWNER_ROLE, False),
            (WRITER_ROLE, True),
            (READER_ROLE, True),
            (MIGRATION_ROLE, True),
        ):
            exists = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            ).fetchone()
            if not exists:
                password = secrets.token_urlsafe(24)
                CREDENTIALS[role] = password
                conn.execute(
                    psycopg.sql.SQL(
                        "CREATE ROLE {} {} NOINHERIT NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                    ).format(
                        psycopg.sql.Identifier(role),
                        psycopg.sql.SQL("LOGIN" if login else "NOLOGIN"),
                        psycopg.sql.Literal(password),
                    )
                )
            CREDENTIALS.setdefault(role, "")
        conn.execute(
            psycopg.sql.SQL(
                "GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
            ).format(
                psycopg.sql.Identifier(OWNER_ROLE), psycopg.sql.Identifier(MIGRATION_ROLE)
            )
        )


def fresh_schema() -> str:
    schema = f"tel_{uuid.uuid4().hex[:16]}"
    with superuser() as conn:
        conn.execute(
            psycopg.sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                psycopg.sql.Identifier(schema), psycopg.sql.Identifier(MIGRATION_ROLE)
            )
        )
        for role in (OWNER_ROLE, WRITER_ROLE, READER_ROLE):
            conn.execute(
                psycopg.sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    psycopg.sql.Identifier(schema), psycopg.sql.Identifier(role)
                )
            )
        conn.execute(
            psycopg.sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(
                psycopg.sql.Identifier(schema), psycopg.sql.Identifier(OWNER_ROLE)
            )
        )
    return schema


def drop_schema(schema: str) -> None:
    with superuser() as conn:
        conn.execute(
            psycopg.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                psycopg.sql.Identifier(schema)
            )
        )


def apply(schema: str, *, argv=("apply",)) -> tuple[int, dict]:
    """Run the authoritative migration tool against ``schema``."""
    stream = io.StringIO()

    def opener():
        return connect_as(MIGRATION_ROLE, schema)

    original = migrate_tool.open_migration_connection
    migrate_tool.open_migration_connection = opener
    try:
        code = migrate_tool.main(list(argv), stream=stream)
    finally:
        migrate_tool.open_migration_connection = original
    return code, json.loads(stream.getvalue())
