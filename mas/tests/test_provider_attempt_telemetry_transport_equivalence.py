"""Telemetry must not change the effective httpx transport configuration.

The finding: ``instrumented_http_client`` built its own bare
``httpx.AsyncHTTPTransport()`` and handed it to ``httpx.AsyncClient`` as
``transport=``. httpx decides at construction time whether to read the
environment's proxy settings::

    allow_env_proxies = trust_env and transport is None

so supplying a transport turned ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY``
off for the entire client, silently. A deployment behind a mandatory egress
proxy would take one network path with telemetry off and a different one with
telemetry on — decided by a flag documented as observational, and reported
nowhere.

Everything below is no-network: the comparisons are made against the routing
table and the transport configuration httpx builds, and the one test that
actually issues a request uses a mock transport. The pattern throughout is to
construct the client the way a telemetry-*off* build would, construct it the way
each telemetry posture does, and require the effective route to be the same
object graph either way.
"""
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from provider_telemetry import capture, transport as telemetry_transport  # noqa: E402
from provider_telemetry.models import (  # noqa: E402
    POSTURE_OBSERVATIONAL,
    POSTURE_STRICT,
)
from provider_telemetry.transport import (  # noqa: E402
    TelemetryTransportUnsupported,
    build_telemetry_transport,
    instrument_http_client,
)
from tests.provider_telemetry_support import make_mock_transport  # noqa: E402


def instrumented_http_client(*, provider: str, **client_kwargs):
    """Build an httpx client from these kwargs, then instrument it in place.

    The package used to export a function of this name, and production used it to
    hand the provider SDK a client telemetry had built. That was the AUD-2
    defect: the client it built was not the client the SDK builds for itself.
    The function now lives here, in the tests, because "instrument whatever
    client this configuration produces" is exactly what these equivalence tests
    exercise — and because keeping it out of the package removes the only code
    path on which telemetry could construct a provider's client.
    """
    return instrument_http_client(httpx.AsyncClient(**client_kwargs), provider=provider)

PROXY_ENV = (
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
    "ALL_PROXY", "all_proxy",
    "NO_PROXY", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
)

ANTHROPIC_URL = httpx.URL("https://api.anthropic.com/v1/messages")
OPENAI_URL = httpx.URL("https://api.openai.com/v1/chat/completions")
PLAIN_URL = httpx.URL("http://example.internal/v1/thing")

# The three postures a build can be in. Telemetry-off never constructs an
# instrumented client at all, so it is represented by the plain client the SDK
# would otherwise get, and is the baseline every other posture is compared to.
POSTURES = (POSTURE_OBSERVATIONAL, POSTURE_STRICT)


@contextmanager
def proxy_environment(**values):
    """Set exactly the given proxy/TLS variables, clearing every other one.

    Clearing matters: a developer machine with ``HTTPS_PROXY`` already exported
    would otherwise make the "no proxy configured" cases pass for the wrong
    reason.
    """
    saved = {name: os.environ.get(name) for name in PROXY_ENV}
    for name in PROXY_ENV:
        os.environ.pop(name, None)
    for name, value in values.items():
        os.environ[name] = value
    try:
        yield
    finally:
        for name in PROXY_ENV:
            os.environ.pop(name, None)
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


def unwrap(transport):
    """The transport a wrapped one delegates to, or the transport itself."""
    while isinstance(transport, telemetry_transport._http_transport_base()) and hasattr(
        transport, "_inner"
    ):
        transport = transport._inner
    return transport


def _proxy_origin(pool) -> "str | None":
    """``scheme://host:port`` for a pool's proxy, or None when it has none.

    ``httpcore``'s URL has no round-trippable ``__str__``, so the origin is
    rebuilt from its parts rather than compared as a repr.
    """
    proxy = getattr(pool, "_proxy_url", None)
    if proxy is None:
        return None

    def text(value) -> str:
        return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)

    return f"{text(proxy.scheme)}://{text(proxy.host)}:{proxy.port}"


def route_description(client, url) -> tuple:
    """A comparable description of the transport a URL actually resolves to.

    This is the *effective* route: the transport httpx would hand the request
    to, unwrapped past any telemetry wrapper, described by the properties that
    decide where the bytes go and how they are protected.
    """
    resolved = unwrap(client._transport_for_url(url))
    pool = getattr(resolved, "_pool", None)
    ssl_context = getattr(pool, "_ssl_context", None)
    return (
        type(resolved).__name__,
        type(pool).__name__,
        _proxy_origin(pool),
        getattr(pool, "_max_connections", None),
        getattr(pool, "_max_keepalive_connections", None),
        getattr(pool, "_keepalive_expiry", None),
        getattr(pool, "_retries", None),
        None if ssl_context is None else (
            ssl_context.verify_mode, ssl_context.check_hostname
        ),
    )


def client_description(client) -> tuple:
    """Client-level configuration that telemetry must leave exactly alone."""
    return (
        client.timeout,
        client.follow_redirects,
        client.max_redirects,
        client.trust_env,
        sorted(client.headers.multi_items()),
        str(client.base_url),
        len(client._mounts),
    )


def plain_client(**kwargs) -> httpx.AsyncClient:
    """What the SDK gets with telemetry off."""
    return httpx.AsyncClient(**kwargs)


def posture_client(posture: str, **kwargs) -> httpx.AsyncClient:
    """What the SDK gets in a telemetry-on posture.

    Both on-postures build the client through the same call: the posture
    changes what happens to an attempt *record*, never how the request is
    routed, and that is exactly the property under test.
    """
    assert posture in POSTURES
    return instrumented_http_client(provider="anthropic", **kwargs)


class _EquivalenceCase(unittest.TestCase):
    """Assert every posture routes identically to a telemetry-off build."""

    def assert_equivalent(self, urls=(ANTHROPIC_URL, OPENAI_URL, PLAIN_URL), **kwargs):
        # No request is issued against either client, so neither opens a socket
        # and neither needs closing: only the routing table is read.
        baseline = plain_client(**kwargs)
        expected_routes = {str(url): route_description(baseline, url) for url in urls}
        expected_client = client_description(baseline)

        for posture in POSTURES:
            with self.subTest(posture=posture):
                client = posture_client(posture, **kwargs)
                for url in urls:
                    self.assertEqual(
                        route_description(client, url),
                        expected_routes[str(url)],
                        f"{posture} posture routes {url} differently from telemetry-off",
                    )
                self.assertEqual(client_description(client), expected_client)
                # And it is genuinely instrumented, or the equivalence would be
                # trivially satisfied by not instrumenting at all.
                self.assertTrue(
                    hasattr(client._transport_for_url(urls[0]), "_inner"),
                    "the resolved transport is not instrumented",
                )


class EnvironmentProxyEquivalenceTests(_EquivalenceCase):
    """HTTP_PROXY / HTTPS_PROXY / NO_PROXY behave identically in every posture."""

    def test_no_proxy_environment_at_all(self):
        with proxy_environment():
            self.assert_equivalent()

    def test_https_proxy(self):
        with proxy_environment(HTTPS_PROXY="http://proxy.internal:3128"):
            self.assert_equivalent()

    def test_http_proxy(self):
        with proxy_environment(HTTP_PROXY="http://proxy.internal:3128"):
            self.assert_equivalent()

    def test_all_proxy(self):
        with proxy_environment(ALL_PROXY="http://proxy.internal:3128"):
            self.assert_equivalent()

    def test_no_proxy_exemption(self):
        with proxy_environment(
            HTTPS_PROXY="http://proxy.internal:3128", NO_PROXY="api.openai.com"
        ):
            self.assert_equivalent()

    def test_trust_env_false_ignores_the_environment_in_every_posture(self):
        with proxy_environment(HTTPS_PROXY="http://proxy.internal:3128"):
            self.assert_equivalent(trust_env=False)

    def test_an_https_proxy_is_actually_used_and_not_merely_equal(self):
        """Guard against both sides being equally *un*proxied.

        Every comparison above would pass if telemetry and non-telemetry clients
        both lost the proxy. This pins the baseline: with HTTPS_PROXY set, the
        instrumented client must resolve to a proxy pool.
        """
        with proxy_environment(HTTPS_PROXY="http://proxy.internal:3128"):
            client = posture_client(POSTURE_STRICT)
            described = route_description(client, ANTHROPIC_URL)
            self.assertEqual(described[1], "AsyncHTTPProxy")
            self.assertEqual(described[2], "http://proxy.internal:3128")

    def test_a_no_proxy_host_resolves_to_the_default_pool_not_a_wrapper_of_none(self):
        """``NO_PROXY`` mounts are ``None``; httpx reads that as "use default"."""
        with proxy_environment(
            HTTPS_PROXY="http://proxy.internal:3128", NO_PROXY="api.openai.com"
        ):
            client = posture_client(POSTURE_OBSERVATIONAL)
            self.assertIn(None, client._mounts.values())
            described = route_description(client, OPENAI_URL)
            self.assertEqual(described[1], "AsyncConnectionPool")
            self.assertIsNone(described[2])


class ExplicitConfigurationEquivalenceTests(_EquivalenceCase):
    """Explicit proxy, TLS, timeout, limits and transport are all preserved."""

    def test_an_explicit_proxy_argument(self):
        with proxy_environment():
            self.assert_equivalent(proxy="http://explicit.internal:8080")

    def test_an_explicit_proxy_argument_wins_over_the_environment(self):
        with proxy_environment(HTTPS_PROXY="http://environment.internal:3128"):
            self.assert_equivalent(proxy="http://explicit.internal:8080")
            client = posture_client(POSTURE_STRICT, proxy="http://explicit.internal:8080")
            self.assertEqual(
                route_description(client, ANTHROPIC_URL)[2],
                "http://explicit.internal:8080",
            )

    def test_tls_verification_disabled_is_preserved_rather_than_re_enabled(self):
        with proxy_environment():
            self.assert_equivalent(verify=False)
            client = posture_client(POSTURE_STRICT, verify=False)
            _, _, _, _, _, _, _, ssl_flags = route_description(client, ANTHROPIC_URL)
            self.assertEqual(ssl_flags[0].name, "CERT_NONE")
            self.assertFalse(ssl_flags[1])

    def test_a_custom_ssl_context_is_preserved(self):
        import ssl

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with proxy_environment():
            self.assert_equivalent(verify=context)
            client = posture_client(POSTURE_OBSERVATIONAL, verify=context)
            pool = unwrap(client._transport_for_url(ANTHROPIC_URL))._pool
            self.assertIs(pool._ssl_context, context)

    def test_custom_timeout_and_limits_are_preserved(self):
        limits = httpx.Limits(
            max_connections=7, max_keepalive_connections=3, keepalive_expiry=11.0
        )
        timeout = httpx.Timeout(connect=1.5, read=2.5, write=3.5, pool=4.5)
        with proxy_environment():
            self.assert_equivalent(limits=limits, timeout=timeout)
            client = posture_client(POSTURE_STRICT, limits=limits, timeout=timeout)
            described = route_description(client, ANTHROPIC_URL)
            self.assertEqual(described[3:6], (7, 3, 11.0))
            self.assertEqual(client.timeout, timeout)

    def test_custom_headers_and_base_url_are_preserved(self):
        with proxy_environment():
            self.assert_equivalent(
                base_url="https://gateway.internal/v1",
                headers={"x-tenant": "acme"},
                follow_redirects=True,
                max_redirects=3,
            )

    def test_an_explicitly_supplied_custom_transport_is_still_instrumented(self):
        """A caller who supplies a transport already gets httpx's env-proxy-off
        behavior; telemetry must preserve that rather than change it back."""
        with proxy_environment(HTTPS_PROXY="http://proxy.internal:3128"):
            custom = make_mock_transport([200])
            baseline = plain_client(transport=custom)
            client = instrumented_http_client(provider="anthropic", transport=custom)

            # Same routing decision: an explicit transport disables env proxies
            # on both sides, so neither client has a proxy mount.
            self.assertEqual(len(baseline._mounts), len(client._mounts))
            self.assertIs(unwrap(client._transport_for_url(ANTHROPIC_URL)), custom)
            self.assertIs(baseline._transport_for_url(ANTHROPIC_URL), custom)
            self.assertTrue(hasattr(client._transport_for_url(ANTHROPIC_URL), "_inner"))

    def test_explicit_mounts_are_each_instrumented_and_left_in_place(self):
        with proxy_environment():
            special = make_mock_transport([200])
            client = instrumented_http_client(
                provider="anthropic",
                mounts={"all://api.openai.com": special, "all://": None},
            )
            self.assertIs(unwrap(client._transport_for_url(OPENAI_URL)), special)
            # The ``None`` mount must survive as None, or every URL it covers
            # would stop falling through to the default transport.
            self.assertIn(None, client._mounts.values())


class ClientLifecycleEquivalenceTests(unittest.IsolatedAsyncioTestCase):
    """Reuse and shutdown semantics are whatever httpx made them."""

    async def test_closing_the_client_closes_every_wrapped_transport(self):
        closed: list[str] = []

        class Recording(httpx.AsyncBaseTransport):
            def __init__(self, name: str) -> None:
                self.name = name

            async def aclose(self) -> None:
                closed.append(self.name)

            async def handle_async_request(self, request):  # pragma: no cover
                raise AssertionError("no request is issued by this test")

        with proxy_environment():
            client = instrumented_http_client(
                provider="anthropic",
                transport=Recording("default"),
                mounts={"all://api.openai.com": Recording("mounted")},
            )
            await client.aclose()
        self.assertEqual(sorted(closed), ["default", "mounted"])
        self.assertTrue(client.is_closed)

    async def test_the_client_is_reused_across_requests_and_not_rebuilt(self):
        with proxy_environment():
            inner = make_mock_transport([200, 200, 200])
            client = instrumented_http_client(provider="anthropic", transport=inner)
            wrapper = client._transport_for_url(ANTHROPIC_URL)
            for _ in range(3):
                response = await client.get("https://api.anthropic.com/v1/messages")
                self.assertEqual(response.status_code, 200)
            # Same wrapper object every time: no per-request construction, and
            # therefore one connection pool exactly as with telemetry off.
            self.assertIs(client._transport_for_url(ANTHROPIC_URL), wrapper)
            self.assertEqual(len(inner.requests), 3)
            await client.aclose()

    async def test_the_context_manager_protocol_still_works(self):
        with proxy_environment():
            inner = make_mock_transport([200])
            async with instrumented_http_client(
                provider="anthropic", transport=inner
            ) as client:
                response = await client.get("https://api.anthropic.com/v1/messages")
                self.assertEqual(response.status_code, 200)
            self.assertTrue(client.is_closed)


class NoSecondClientTests(unittest.TestCase):
    """Telemetry adds no client, no transport instance and no pool of its own."""

    def test_instrumentation_wraps_the_clients_own_transports_only(self):
        with proxy_environment(HTTPS_PROXY="http://proxy.internal:3128"):
            client = httpx.AsyncClient()
            originals = [client._transport] + [
                mounted for mounted in client._mounts.values() if mounted is not None
            ]
            instrument_http_client(client, provider="anthropic")
            wrapped = [client._transport] + [
                mounted for mounted in client._mounts.values() if mounted is not None
            ]
            self.assertEqual(len(originals), len(wrapped))
            for original, wrapper in zip(originals, wrapped):
                self.assertIs(wrapper._inner, original)

    def test_a_client_without_the_expected_internals_fails_loudly(self):
        """Never return something that looks instrumented and is not routed."""

        class Foreign:
            pass

        with self.assertRaises(TelemetryTransportUnsupported):
            instrument_http_client(Foreign(), provider="anthropic")


class TransportOpacityTests(unittest.IsolatedAsyncioTestCase):
    """Telemetry sees a method and a path. Not a query, credential or header."""

    async def test_the_wrapper_never_reads_headers_credentials_or_the_query(self):
        from provider_telemetry.capture import InvocationCapture, capture_scope

        inner = make_mock_transport([200])
        client = instrumented_http_client(provider="anthropic", transport=inner)
        buffer = InvocationCapture(
            invocation_id="00000000-0000-4000-8000-00000000000a",
            call_id="00000000-0000-4000-8000-00000000000b",
            telemetry_run_id="00000000-0000-4000-8000-00000000000c",
            posture=POSTURE_OBSERVATIONAL,
            worker_id="test-worker",
            provider="anthropic",
            requested_model="claude-sonnet-4-6",
        )

        class _NoSession:
            posture = POSTURE_OBSERVATIONAL
            strict = False

            async def persist_attempt_start(self, record):
                return None

        import provider_telemetry.service as service

        token = service._session.set(_NoSession())
        try:
            with capture_scope(buffer):
                await client.get(
                    "https://api.anthropic.com/v1/messages?api_key=SECRET&q=hello",
                    headers={"authorization": "Bearer sk-ant-secret", "x-tenant": "acme"},
                )
        finally:
            service._session.reset(token)
            await client.aclose()

        record = buffer.http_attempts[0]
        self.assertEqual(record.request_method, "GET")
        self.assertEqual(record.request_path, "/v1/messages")
        self.assertNotIn("SECRET", record.request_path)
        self.assertNotIn("api_key", record.request_path)

        # The credential really was on the wire — the wrapper is transparent to
        # it, which is the point: it passes the header through without ever
        # reading it, and nothing derived from it reaches the attempt record.
        request = inner.requests[0]
        self.assertEqual(request.headers["authorization"], "Bearer sk-ant-secret")
        for field in (record.request_path, record.request_method, record.provider,
                      record.requested_model):
            self.assertNotIn("sk-ant", field)
            self.assertNotIn("acme", field)


class ProxyLossMutationTests(unittest.TestCase):
    """The bounded mutation that reproduces the finding.

    Constructing the client the way the pre-remediation code did — one bare
    transport handed to ``httpx.AsyncClient`` as ``transport=`` — is enough to
    lose every environment proxy. This test *asserts the defect*, so it fails
    the moment the old construction stops being wrong, and it is the reason the
    equivalence assertions above are not vacuous.
    """

    def test_the_pre_remediation_construction_loses_environment_proxies(self):
        with proxy_environment(HTTPS_PROXY="http://proxy.internal:3128"):
            correct = instrumented_http_client(provider="anthropic")
            self.assertEqual(
                route_description(correct, ANTHROPIC_URL)[1], "AsyncHTTPProxy"
            )

            # The exact previous body of instrumented_http_client.
            inner = httpx.AsyncHTTPTransport()
            mutated = httpx.AsyncClient(
                transport=build_telemetry_transport(inner, provider="anthropic")
            )
            self.assertEqual(mutated._mounts, {})
            self.assertEqual(
                route_description(mutated, ANTHROPIC_URL)[1],
                "AsyncConnectionPool",
                "the pre-remediation construction was expected to route directly",
            )
            self.assertIsNone(route_description(mutated, ANTHROPIC_URL)[2])

    def test_the_mutation_also_loses_trust_env_derived_tls_settings(self):
        """Same root cause, different symptom: the bare transport reads nothing.

        ``httpx.AsyncHTTPTransport()`` built by hand takes no ``verify`` from
        the client it is later attached to, so a client configured with
        ``verify=False`` would silently keep full verification.
        """
        with proxy_environment():
            inner = httpx.AsyncHTTPTransport()
            mutated = httpx.AsyncClient(
                transport=build_telemetry_transport(inner, provider="anthropic"),
                verify=False,
            )
            _, _, _, _, _, _, _, ssl_flags = route_description(mutated, ANTHROPIC_URL)
            self.assertEqual(
                ssl_flags[0].name, "CERT_REQUIRED",
                "the pre-remediation construction was expected to ignore verify=False",
            )
            correct = instrumented_http_client(provider="anthropic", verify=False)
            self.assertEqual(
                route_description(correct, ANTHROPIC_URL)[7][0].name, "CERT_NONE"
            )


class GuardedConstructionTests(unittest.TestCase):
    """A client that cannot be instrumented never reaches the SDK half-built."""

    def test_the_llm_client_helper_swallows_the_failure_in_observational_mode(self):
        """``_instrument_provider_client`` runs inside the capture guard.

        An httpx build this module cannot instrument raises, the guard turns it
        into a capture failure, and the SDK keeps the client it built for itself
        — the same client a telemetry-off build gets. Returning a partially
        instrumented client, or a substitute one, would be the failure mode
        MAJ-4 and AUD-2 describe, one layer up.
        """
        def explode():
            raise TelemetryTransportUnsupported("no _mounts on this httpx build")

        self.assertIsNone(capture.guarded(explode, None, reason="http_client"))


if __name__ == "__main__":  # pragma: no cover - direct invocation
    unittest.main()
