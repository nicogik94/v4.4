"""Test-only libpq connection-string handling shared by the PostgreSQL suites.

Every DSN form libpq itself accepts must behave identically here:

* ``postgresql://`` and ``postgres://`` URIs;
* URIs with percent-encoded credentials and with bracketed IPv6 hosts;
* keyword/value strings (``host=... port=... dbname=...``);
* Unix-socket keyword/value strings (``host=/var/run/postgresql``).

GitHub Actions supplies ``TEST_EVIDENCE_PG_DSN`` in URI form, so a helper that
understands only one form silently connects somewhere else (a local Unix
socket) instead of failing. Parsing is therefore delegated to libpq's own
conninfo parser through :mod:`psycopg.conninfo`; nothing here splits a DSN on
``=``, ``/`` or ``@``.

This centralises the behaviour already established by ``evidence_snapshot_pg``,
where connection parameters come from libpq (there, from a live ``conn.info``)
and a role's ``user``/``password`` is layered on top without disturbing any
other field. That helper cannot be reused directly for disposable telemetry
roles — it exposes no credential override and provisions the unrelated Research
Evidence role manifest on every connect — so the pattern is centralised here
instead of being restated ad hoc.

Test-only: no production code imports this module.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

import pytest

__all__ = [
    "MalformedDsn",
    "connection_parameters",
    "describe",
    "redacted",
]

SECRET_KEYS = frozenset({"password", "passfile", "sslpassword"})

# libpq quotes the offending *text* back at you, and for a URI that text is the
# whole connection string — credentials included. Only diagnostics whose one
# variable part is a parameter *name* are safe to repeat, and the message is
# rebuilt from the captured name rather than passed through.
_SAFE_DETAILS = (
    (
        re.compile(r'invalid connection option "([A-Za-z0-9_]+)"'),
        'invalid connection option "{}"',
    ),
    (
        re.compile(r'invalid URI query parameter: "([A-Za-z0-9_]+)"'),
        'invalid URI query parameter "{}"',
    ),
)
_WITHHELD = "detail withheld: libpq diagnostics can echo credentials"


class MalformedDsn(ValueError):
    """libpq refused a connection string.

    Raised instead of letting a caller fall back to libpq defaults. The message
    never carries the connection string or a credential.
    """


def _psycopg():
    try:
        import psycopg  # noqa: PLC0415 - optional test dependency
        import psycopg.conninfo  # noqa: F401,PLC0415 - libpq's own parser
    except ImportError:  # pragma: no cover - environment guard
        pytest.skip("psycopg is not installed; PostgreSQL DSN handling needs it")
    return psycopg


def _safe_detail(exc: BaseException) -> str:
    """An allowlisted, rebuilt rendering of a libpq diagnostic.

    Anything not on the allowlist is withheld rather than filtered: libpq quotes
    the offending text back, and for a URI syntax error that text is the entire
    connection string.
    """
    message = str(exc)
    for pattern, template in _SAFE_DETAILS:
        found = pattern.search(message)
        if found is not None:
            return template.format(found.group(1))
    return _WITHHELD


def _parse(dsn: str, *, source: Optional[str]) -> dict[str, str]:
    psycopg = _psycopg()
    parsed = None
    detail = ""
    try:
        parsed = psycopg.conninfo.conninfo_to_dict(dsn)
    except psycopg.Error as exc:
        detail = _safe_detail(exc)
    # Raised outside the ``except`` block on purpose: raising inside it would
    # attach libpq's unfiltered message to ``__context__``.
    if parsed is None:
        origin = f" from {source}" if source else ""
        raise MalformedDsn(
            f"libpq rejected the PostgreSQL connection string{origin} ({detail})"
        )
    return {
        key: str(value)
        for key, value in parsed.items()
        if value is not None and str(value) != ""
    }


def connection_parameters(
    dsn: str, *, source: Optional[str] = None, **overrides: Any
) -> dict[str, str]:
    """libpq-parsed parameters for ``dsn`` with ``overrides`` layered on top.

    Accepts every DSN form libpq accepts. Overrides preserve every field they
    do not name — host, port, dbname, sslmode and the rest survive a
    user/password swap. ``None`` or ``""`` drops a field; ``options`` is
    appended to any options the DSN already carried, because libpq treats it as
    a whitespace-separated command line rather than a single value.

    Raises :class:`MalformedDsn` rather than returning a partial mapping, so a
    DSN this helper cannot parse can never degrade into libpq's local defaults.
    """
    if not isinstance(dsn, str) or not dsn.strip():
        origin = f" ({source})" if source else ""
        raise MalformedDsn(f"the PostgreSQL connection string is empty{origin}")

    parameters = _parse(dsn, source=source)
    for key, value in overrides.items():
        if value is None or str(value) == "":
            parameters.pop(key, None)
            continue
        value = str(value)
        if key == "options" and parameters.get("options"):
            parameters[key] = f"{parameters['options']} {value}"
        else:
            parameters[key] = value

    # Validate the merged result through libpq as well: an override may name a
    # parameter that does not exist.
    psycopg = _psycopg()
    merged = None
    detail = ""
    try:
        merged = psycopg.conninfo.make_conninfo(**parameters)
    except psycopg.Error as exc:
        detail = _safe_detail(exc)
    if merged is None:
        raise MalformedDsn(
            f"libpq rejected the merged connection parameters ({detail})"
        )
    return parameters


def redacted(parameters: Mapping[str, Any]) -> dict[str, str]:
    """``parameters`` with every credential replaced by a fixed placeholder."""
    return {
        key: ("<redacted>" if key.lower() in SECRET_KEYS else str(value))
        for key, value in parameters.items()
    }


def describe(parameters: Mapping[str, Any]) -> str:
    """A stable, credential-free one-line rendering for assertion messages."""
    redacted_parameters = redacted(parameters)
    return " ".join(
        f"{key}={redacted_parameters[key]}" for key in sorted(redacted_parameters)
    )
