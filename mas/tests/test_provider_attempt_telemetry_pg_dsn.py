"""Regression: the telemetry PostgreSQL suite must accept every libpq DSN form.

``.github/workflows/tests.yml`` sets ``TEST_EVIDENCE_PG_DSN`` to a
``postgresql://`` URI. The suite's original role-DSN helper rebuilt the DSN with
``dict(item.split("=", 1) for item in dsn.split() if "=" in item)``, which sees
a URI as one token with no ``=`` and yields *nothing*: no host, no port, no
dbname, no credentials. libpq then fell back to a local Unix socket and all 45
tests in ``test_provider_attempt_telemetry_pg.py`` failed before reaching the
contracts they exist to prove.

These tests pin the parsing and override behaviour itself, so they fail against
that ad-hoc parser without needing a cluster. The connection-level proof lives
in ``test_provider_attempt_telemetry_pg.py``, which now runs under either form.
"""
from __future__ import annotations

import importlib
import sys
import traceback
import unittest
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")

from tests import pg_dsn  # noqa: E402

URI_DSN = "postgresql://postgres:postgres@localhost:5432/v4_test"
KEYWORD_DSN = "host=localhost port=5432 dbname=v4_test user=postgres password=postgres"
SOCKET_DSN = "host=/var/run/postgresql port=5432 dbname=v4_test user=postgres"
SECRET = "p@ss w0rd/+&"
SECRET_PERCENT_ENCODED = "p%40ss%20w0rd%2F%2B%26"


class DsnFormTests(unittest.TestCase):
    """Requirements 1-4: every accepted form parses to the same coordinates."""

    def test_a_postgresql_uri_yields_host_port_and_database(self):
        parameters = pg_dsn.connection_parameters(URI_DSN)
        self.assertEqual(parameters["host"], "localhost")
        self.assertEqual(parameters["port"], "5432")
        self.assertEqual(parameters["dbname"], "v4_test")
        self.assertEqual(parameters["user"], "postgres")

    def test_a_postgres_scheme_uri_is_accepted_too(self):
        parameters = pg_dsn.connection_parameters(
            "postgres://postgres:postgres@localhost:5432/v4_test"
        )
        self.assertEqual(parameters["host"], "localhost")
        self.assertEqual(parameters["dbname"], "v4_test")

    def test_the_uri_and_keyword_forms_describe_the_same_server(self):
        self.assertEqual(
            pg_dsn.connection_parameters(URI_DSN),
            pg_dsn.connection_parameters(KEYWORD_DSN),
        )

    def test_a_keyword_value_dsn_still_parses(self):
        parameters = pg_dsn.connection_parameters(KEYWORD_DSN)
        self.assertEqual(parameters["host"], "localhost")
        self.assertEqual(parameters["dbname"], "v4_test")

    def test_a_unix_socket_keyword_value_dsn_keeps_its_directory_host(self):
        parameters = pg_dsn.connection_parameters(SOCKET_DSN)
        self.assertEqual(parameters["host"], "/var/run/postgresql")
        self.assertEqual(parameters["dbname"], "v4_test")
        self.assertNotIn("password", parameters)

    def test_a_percent_encoded_uri_credential_is_decoded_by_libpq(self):
        parameters = pg_dsn.connection_parameters(
            f"postgresql://tel_user:{SECRET_PERCENT_ENCODED}@127.0.0.1:5432/v4_test"
        )
        self.assertEqual(parameters["user"], "tel_user")
        self.assertEqual(parameters["password"], SECRET)
        self.assertEqual(parameters["host"], "127.0.0.1")

    def test_a_percent_encoded_unix_socket_uri_host_is_decoded(self):
        parameters = pg_dsn.connection_parameters(
            "postgresql://postgres@%2Fvar%2Frun%2Fpostgresql/v4_test"
        )
        self.assertEqual(parameters["host"], "/var/run/postgresql")

    def test_a_bracketed_ipv6_uri_host_loses_only_its_brackets(self):
        parameters = pg_dsn.connection_parameters(
            "postgresql://postgres@[2001:db8::1234]:5432/v4_test"
        )
        self.assertEqual(parameters["host"], "2001:db8::1234")
        self.assertEqual(parameters["port"], "5432")

    def test_the_ad_hoc_parser_would_have_produced_nothing_for_a_uri(self):
        """The exact defect, stated as an assertion."""
        ad_hoc = dict(
            item.split("=", 1) for item in URI_DSN.split() if "=" in item
        )
        self.assertEqual(ad_hoc, {})
        self.assertNotEqual(pg_dsn.connection_parameters(URI_DSN), ad_hoc)


class MalformedDsnTests(unittest.TestCase):
    """Requirement 5: an unparseable DSN fails loudly, never silently."""

    def test_a_truncated_ipv6_uri_raises_instead_of_falling_back(self):
        with self.assertRaises(pg_dsn.MalformedDsn):
            pg_dsn.connection_parameters("postgresql://postgres@[::1")

    def test_an_unknown_connection_option_raises(self):
        with self.assertRaises(pg_dsn.MalformedDsn):
            pg_dsn.connection_parameters("host=localhost nosuchoption=1")

    def test_an_unknown_uri_query_parameter_raises(self):
        with self.assertRaises(pg_dsn.MalformedDsn):
            pg_dsn.connection_parameters(f"{URI_DSN}?nosuchoption=1")

    def test_an_empty_dsn_raises(self):
        for value in ("", "   "):
            with self.subTest(value=value):
                with self.assertRaises(pg_dsn.MalformedDsn):
                    pg_dsn.connection_parameters(value)

    def test_an_override_naming_no_real_parameter_raises(self):
        with self.assertRaises(pg_dsn.MalformedDsn):
            pg_dsn.connection_parameters(URI_DSN, nosuchoption="1")

    def test_the_failure_names_the_environment_variable_it_came_from(self):
        with self.assertRaises(pg_dsn.MalformedDsn) as raised:
            pg_dsn.connection_parameters(
                "postgresql://postgres@[::1", source="TEST_EVIDENCE_PG_DSN"
            )
        self.assertIn("TEST_EVIDENCE_PG_DSN", str(raised.exception))


class OverrideTests(unittest.TestCase):
    """Requirement 6: an override replaces one field and preserves the rest."""

    RICH_URI = (
        "postgresql://ci_user:ci_secret@10.0.0.7:6543/v4_test"
        "?sslmode=require&options=-c%20statement_timeout%3D5s"
        "&connect_timeout=7&application_name=mas"
    )
    RICH_KEYWORD = (
        "host=10.0.0.7 port=6543 dbname=v4_test user=ci_user password=ci_secret "
        "sslmode=require options='-c statement_timeout=5s' connect_timeout=7 "
        "application_name=mas"
    )

    def _assert_preserved(self, parameters):
        self.assertEqual(parameters["host"], "10.0.0.7")
        self.assertEqual(parameters["port"], "6543")
        self.assertEqual(parameters["dbname"], "v4_test")
        self.assertEqual(parameters["sslmode"], "require")
        self.assertEqual(parameters["connect_timeout"], "7")
        self.assertEqual(parameters["application_name"], "mas")
        self.assertIn("-c statement_timeout=5s", parameters["options"])

    def test_a_role_override_preserves_every_other_uri_field(self):
        parameters = pg_dsn.connection_parameters(
            self.RICH_URI, user="workflow_provider_telemetry_writer", password=SECRET
        )
        self._assert_preserved(parameters)
        self.assertEqual(parameters["user"], "workflow_provider_telemetry_writer")
        self.assertEqual(parameters["password"], SECRET)

    def test_a_role_override_preserves_every_other_keyword_field(self):
        parameters = pg_dsn.connection_parameters(
            self.RICH_KEYWORD,
            user="workflow_provider_telemetry_writer",
            password=SECRET,
        )
        self._assert_preserved(parameters)
        self.assertEqual(parameters["user"], "workflow_provider_telemetry_writer")

    def test_both_forms_override_to_the_same_parameters(self):
        self.assertEqual(
            pg_dsn.connection_parameters(self.RICH_URI, user="r", password=SECRET),
            pg_dsn.connection_parameters(self.RICH_KEYWORD, user="r", password=SECRET),
        )

    def test_an_added_option_is_appended_rather_than_replacing_the_dsn_options(self):
        parameters = pg_dsn.connection_parameters(
            self.RICH_URI, options="-c search_path=tel_abc"
        )
        self.assertIn("-c statement_timeout=5s", parameters["options"])
        self.assertIn("-c search_path=tel_abc", parameters["options"])

    def test_an_option_is_set_when_the_dsn_carried_none(self):
        parameters = pg_dsn.connection_parameters(
            URI_DSN, options="-c search_path=tel_abc"
        )
        self.assertEqual(parameters["options"], "-c search_path=tel_abc")

    def test_an_empty_password_override_drops_the_field_entirely(self):
        for value in (None, ""):
            with self.subTest(value=value):
                parameters = pg_dsn.connection_parameters(URI_DSN, password=value)
                self.assertNotIn("password", parameters)
                self.assertEqual(parameters["host"], "localhost")

    def test_the_merged_parameters_round_trip_through_libpq(self):
        """Whatever is handed to ``psycopg.connect`` is a valid conninfo."""
        parameters = pg_dsn.connection_parameters(
            self.RICH_URI, user="r", password=SECRET
        )
        rendered = psycopg.conninfo.make_conninfo(**parameters)
        self.assertEqual(psycopg.conninfo.conninfo_to_dict(rendered), parameters)


class CredentialSafetyTests(unittest.TestCase):
    """Requirement 7: no credential reaches an assertion or a diagnostic."""

    def test_the_redacted_view_hides_the_password_and_keeps_the_coordinates(self):
        parameters = pg_dsn.connection_parameters(URI_DSN, password=SECRET)
        redacted = pg_dsn.redacted(parameters)
        self.assertEqual(redacted["password"], "<redacted>")
        self.assertEqual(redacted["host"], "localhost")
        self.assertNotIn(SECRET, str(redacted))

    def test_the_description_never_contains_a_credential(self):
        description = pg_dsn.describe(
            pg_dsn.connection_parameters(URI_DSN, password=SECRET)
        )
        self.assertNotIn(SECRET, description)
        self.assertIn("host=localhost", description)
        self.assertIn("password=<redacted>", description)

    def test_a_malformed_uri_diagnostic_echoes_neither_the_dsn_nor_its_password(self):
        malformed = f"postgresql://ci_user:{SECRET}@[::1"
        with self.assertRaises(pg_dsn.MalformedDsn) as raised:
            pg_dsn.connection_parameters(malformed, source="TEST_EVIDENCE_PG_DSN")
        message = str(raised.exception)
        self.assertNotIn(SECRET, message)
        self.assertNotIn("ci_user", message)
        self.assertNotIn(malformed, message)

    def test_a_malformed_uri_leaks_nothing_through_the_chained_traceback(self):
        """The rendered traceback is what a failing CI job actually prints."""
        malformed = f"postgresql://ci_user:{SECRET}@[::1"
        try:
            pg_dsn.connection_parameters(malformed)
        except pg_dsn.MalformedDsn as exc:
            rendered = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
        else:  # pragma: no cover - the call must raise
            self.fail("a malformed DSN must raise")
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn("ci_user", rendered)
        self.assertNotIn(malformed, rendered)

    def test_an_unrecognised_libpq_diagnostic_is_withheld_rather_than_filtered(self):
        with self.assertRaises(pg_dsn.MalformedDsn) as raised:
            pg_dsn.connection_parameters(f"postgresql://ci_user:{SECRET}@[::1")
        self.assertIn("withheld", str(raised.exception))

    def test_a_diagnostic_naming_only_a_parameter_keeps_its_useful_detail(self):
        with self.assertRaises(pg_dsn.MalformedDsn) as raised:
            pg_dsn.connection_parameters(
                f"host=localhost password='{SECRET}' nosuchoption=1"
            )
        message = str(raised.exception)
        self.assertIn("nosuchoption", message)
        self.assertNotIn(SECRET, message)
        self.assertNotIn("withheld", message)

    def test_a_password_bearing_dsn_never_reaches_the_description(self):
        """``describe`` is the only rendering the suite may put in a message."""
        parameters = pg_dsn.connection_parameters(
            f"postgresql://ci_user:{SECRET_PERCENT_ENCODED}@localhost/v4_test"
        )
        self.assertEqual(parameters["password"], SECRET)
        self.assertNotIn(SECRET, pg_dsn.describe(parameters))


class TelemetrySuiteCallSiteTests(unittest.TestCase):
    """The defect as it actually bit: the suite's own role-DSN construction."""

    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module("tests.test_provider_attempt_telemetry_pg")

    def _role_parameters(self, dsn, role, password, **overrides):
        with mock.patch.dict(
            "os.environ", {self.module.DSN_ENV: dsn}, clear=False
        ), mock.patch.dict(self.module._CREDENTIALS, {role: password}, clear=False):
            return self.module._role_parameters(role, **overrides)

    def test_the_suite_reaches_the_ci_server_when_the_dsn_is_a_uri(self):
        parameters = self._role_parameters(
            URI_DSN, self.module.WRITER_ROLE, SECRET
        )
        self.assertEqual(parameters["host"], "localhost")
        self.assertEqual(parameters["port"], "5432")
        self.assertEqual(parameters["dbname"], "v4_test")
        self.assertEqual(parameters["user"], self.module.WRITER_ROLE)
        self.assertEqual(parameters["password"], SECRET)

    def test_the_suite_produces_identical_parameters_for_both_dsn_forms(self):
        self.assertEqual(
            self._role_parameters(URI_DSN, self.module.READER_ROLE, SECRET),
            self._role_parameters(KEYWORD_DSN, self.module.READER_ROLE, SECRET),
        )

    def test_the_suite_keeps_the_socket_directory_host(self):
        parameters = self._role_parameters(
            SOCKET_DSN, self.module.READER_ROLE, SECRET
        )
        self.assertEqual(parameters["host"], "/var/run/postgresql")
        self.assertEqual(parameters["dbname"], "v4_test")

    def test_the_export_search_path_option_does_not_discard_the_uri_fields(self):
        parameters = self._role_parameters(
            f"{URI_DSN}?sslmode=prefer",
            self.module.READER_ROLE,
            SECRET,
            options="-c search_path=tel_abc",
        )
        self.assertEqual(parameters["host"], "localhost")
        self.assertEqual(parameters["sslmode"], "prefer")
        self.assertEqual(parameters["options"], "-c search_path=tel_abc")

    def test_a_role_without_a_credential_sends_no_empty_password(self):
        parameters = self._role_parameters(URI_DSN, self.module.READER_ROLE, "")
        self.assertNotIn("password", parameters)

    def test_a_malformed_dsn_fails_the_suite_instead_of_reaching_a_local_socket(self):
        with self.assertRaises(pg_dsn.MalformedDsn):
            self._role_parameters(
                "postgresql://postgres@[::1", self.module.READER_ROLE, SECRET
            )
