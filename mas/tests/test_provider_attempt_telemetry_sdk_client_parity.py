"""Turning telemetry on must not change the provider SDK's effective client.

The AUD-2 finding. ``instrument_http_client`` was correct — it preserves the
transport configuration of whatever client it is handed — but production never
handed it the SDK's client. ``llm_client._telemetry_http_client`` built a
*replacement*::

    httpx.AsyncClient(timeout=REQUEST_TIMEOUT)

and passed it to the SDK as ``http_client=``. That is not the client either SDK
builds for itself. Both ship a ``DefaultAsyncHttpxClient`` carrying
``limits=Limits(max_connections=1000, max_keepalive_connections=100)`` and
``follow_redirects=True``; httpx's own defaults are 100 / 20 and ``False``.
Anthropic's additionally installs TCP keepalive socket options and an explicitly
constructed proxy mount table. So enabling a flag documented as observational
silently reduced the connection pool tenfold and turned redirect following off.

The remediation is that telemetry never constructs a provider's client. The SDK
is built exactly as a telemetry-off build builds it, and the client it made for
itself is then instrumented in place.

Every test here uses the genuine installed ``anthropic`` and ``openai`` SDKs and
never reaches a network: configuration is read from the constructed client's
routing table, and the tests that issue requests use a mock transport.
"""
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402
import httpx  # noqa: E402
import openai  # noqa: E402

import config  # noqa: E402
import llm_client  # noqa: E402

from provider_telemetry import transport as telemetry_transport  # noqa: E402
from provider_telemetry.models import POSTURE_STRICT  # noqa: E402
from provider_telemetry.posture import NON_EXPERIMENT_ENV  # noqa: E402
from provider_telemetry.transport import (  # noqa: E402
    TelemetrySdkShapeUnsupported,
    TelemetryStartUnavailable,
    instrument_sdk_client,
    is_instrumented,
    sdk_http_client,
)
from tests.provider_telemetry_support import make_mock_transport  # noqa: E402

POSTURE_ENV = config.PROVIDER_TELEMETRY_POSTURE_ENV
COMPAT_ENV = config.PROVIDER_ATTEMPT_TELEMETRY_ENABLED_ENV

PROXY_ENV = (
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
    "ALL_PROXY", "all_proxy",
    "NO_PROXY", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
)

# Placeholder credential. The OpenAI SDK refuses to construct without one, and
# no test in this module reaches a network.
API_KEY = "test-provider-key"

# The postures a build can be configured for. ``off`` is the baseline every
# other posture is compared against, because it is by definition the network
# behavior this change is not allowed to alter.
POSTURE_OFF = "off"
POSTURE_ON = ("observational", "strict")

ANTHROPIC_URL = httpx.URL("https://api.anthropic.com/v1/messages")
OPENAI_URL = httpx.URL("https://api.openai.com/v1/chat/completions")
PLAIN_URL = httpx.URL("http://example.internal/v1/thing")

SDKS = (
    ("anthropic", llm_client._get_anthropic, ANTHROPIC_URL),
    ("openai", llm_client._get_openai, OPENAI_URL),
)


@contextmanager
def posture_environment(posture, **extra):
    """Configure exactly one telemetry posture, clearing every related flag.

    A strict posture also has to declare itself an experiment worker, which is
    what ``strict_required()`` reads; leaving ``NON_EXPERIMENT`` set would make
    the strict cases silently exercise the observational path.
    """
    names = (POSTURE_ENV, COMPAT_ENV, NON_EXPERIMENT_ENV) + PROXY_ENV
    saved = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ.pop(name, None)
    if posture != POSTURE_OFF:
        os.environ[POSTURE_ENV] = posture
    for name, value in extra.items():
        os.environ[name] = value
    # The OpenAI SDK refuses to construct without a credential, and a CI
    # environment has none. These are placeholders that never leave the process:
    # no test here reaches a network.
    saved_keys = (llm_client.ANTHROPIC_API_KEY, llm_client.OPENAI_API_KEY)
    llm_client.ANTHROPIC_API_KEY = "test-anthropic-key"
    llm_client.OPENAI_API_KEY = "test-openai-key"
    llm_client.reset_provider_clients()
    try:
        yield
    finally:
        llm_client.ANTHROPIC_API_KEY, llm_client.OPENAI_API_KEY = saved_keys
        for name in names:
            os.environ.pop(name, None)
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value
        llm_client.reset_provider_clients()


def unwrap(transport):
    """The transport a telemetry wrapper delegates to, or the transport itself."""
    while hasattr(transport, "_inner"):
        transport = transport._inner
    return transport


def _proxy_origin(pool):
    """``scheme://host:port`` for a pool's proxy, or None when it has none."""
    proxy = getattr(pool, "_proxy_url", None)
    if proxy is None:
        return None

    def text(value):
        return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)

    return f"{text(proxy.scheme)}://{text(proxy.host)}:{proxy.port}"


def route_description(client, url) -> tuple:
    """The effective route for a URL, unwrapped past any telemetry wrapper.

    Everything in here decides where bytes go, how many connections may carry
    them, how long an idle one survives, and how the peer is authenticated.
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
        getattr(pool, "_socket_options", None),
        None if ssl_context is None else (
            ssl_context.verify_mode, ssl_context.check_hostname,
            tuple(sorted(ssl_context.get_ca_certs(binary_form=False)[0].keys()))
            if ssl_context.get_ca_certs() else (),
        ),
    )


def client_description(client) -> tuple:
    """Client-level configuration telemetry must leave exactly alone.

    ``_mounts`` is described by pattern *and* by which patterns map to ``None``:
    a ``None`` mount is how ``NO_PROXY`` says "use the default transport", and
    an instrumentation layer that turned it into a wrapper around nothing would
    change the routing of every host it exempts.
    """
    return (
        type(client).__name__,
        client.timeout,
        client.follow_redirects,
        client.max_redirects,
        client.trust_env,
        sorted(client.headers.multi_items()),
        str(client.base_url),
        tuple(sorted(
            # httpx keys mounts by ``URLPattern``, whose ``__str__`` is an
            # object repr carrying a memory address — comparing that would make
            # every description differ for the wrong reason.
            (getattr(pattern, "pattern", str(pattern)), mounted is None)
            for pattern, mounted in client._mounts.items()
        )),
    )


def sdk_description(sdk_client) -> tuple:
    """SDK-level configuration, which telemetry does not touch at all."""
    return (
        type(sdk_client).__name__,
        sdk_client.max_retries,
        str(sdk_client.base_url),
        sdk_client.timeout,
    )


def full_description(sdk_client, urls) -> tuple:
    http_client = sdk_client._client
    return (
        sdk_description(sdk_client),
        client_description(http_client),
        tuple(route_description(http_client, url) for url in urls),
    )


class _ParityCase(unittest.TestCase):
    """Every on-posture must produce the telemetry-off client, exactly."""

    URLS = (ANTHROPIC_URL, OPENAI_URL, PLAIN_URL)

    def assert_parity(self, **environment):
        for name, build, primary in SDKS:
            with posture_environment(POSTURE_OFF, **environment):
                baseline = full_description(build(), self.URLS)
                self.assertFalse(
                    is_instrumented(build()._client),
                    "telemetry-off must not instrument anything",
                )

            for posture in POSTURE_ON:
                with self.subTest(sdk=name, posture=posture):
                    with posture_environment(posture, **environment):
                        sdk_client = build()
                        self.assertEqual(
                            full_description(sdk_client, self.URLS),
                            baseline,
                            f"{posture} changes {name}'s effective client",
                        )
                        # And it really is instrumented, or parity would be
                        # trivially satisfied by not instrumenting at all.
                        self.assertTrue(is_instrumented(sdk_client._client))
                        self.assertTrue(
                            hasattr(
                                sdk_client._client._transport_for_url(primary), "_inner"
                            ),
                            "the resolved transport is not instrumented",
                        )


class SdkDefaultParityTests(_ParityCase):
    """The defaults AUD-2 found being replaced, asserted by value and by parity."""

    def test_no_proxy_environment_at_all(self):
        self.assert_parity()

    def test_the_sdk_connection_limits_survive_every_posture(self):
        """The exact numbers from the finding: 1000 / 100, not httpx's 100 / 20."""
        for name, build, url in SDKS:
            for posture in (POSTURE_OFF,) + POSTURE_ON:
                with self.subTest(sdk=name, posture=posture):
                    with posture_environment(posture):
                        pool = unwrap(build()._client._transport_for_url(url))._pool
                        self.assertEqual(pool._max_connections, 1000)
                        self.assertEqual(pool._max_keepalive_connections, 100)
                        self.assertEqual(pool._keepalive_expiry, 5.0)

    def test_follow_redirects_stays_on_in_every_posture(self):
        """httpx defaults this to False; both SDKs default it to True."""
        for name, build, _ in SDKS:
            for posture in (POSTURE_OFF,) + POSTURE_ON:
                with self.subTest(sdk=name, posture=posture):
                    with posture_environment(posture):
                        self.assertTrue(build()._client.follow_redirects)

    def test_the_configured_request_timeout_is_the_one_the_runtime_asked_for(self):
        for name, build, _ in SDKS:
            for posture in (POSTURE_OFF,) + POSTURE_ON:
                with self.subTest(sdk=name, posture=posture):
                    with posture_environment(posture):
                        sdk_client = build()
                        self.assertEqual(
                            sdk_client._client.timeout,
                            httpx.Timeout(config.REQUEST_TIMEOUT),
                        )

    def test_trust_env_stays_on_in_every_posture(self):
        for name, build, _ in SDKS:
            for posture in (POSTURE_OFF,) + POSTURE_ON:
                with self.subTest(sdk=name, posture=posture):
                    with posture_environment(posture):
                        self.assertTrue(build()._client.trust_env)

    def test_the_sdk_retry_policy_is_untouched(self):
        """Telemetry observes retries; it never changes how many there are."""
        for name, build, _ in SDKS:
            with posture_environment(POSTURE_OFF):
                expected = build().max_retries
            for posture in POSTURE_ON:
                with self.subTest(sdk=name, posture=posture):
                    with posture_environment(posture):
                        self.assertEqual(build().max_retries, expected)

    def test_the_client_is_the_sdks_own_class_and_not_a_bare_httpx_client(self):
        """The structural claim underneath every assertion above."""
        for name, build, _ in SDKS:
            module = anthropic if name == "anthropic" else openai
            for posture in (POSTURE_OFF,) + POSTURE_ON:
                with self.subTest(sdk=name, posture=posture):
                    with posture_environment(posture):
                        http_client = build()._client
                        self.assertIsInstance(
                            http_client, module.DefaultAsyncHttpxClient
                        )
                        self.assertIsNot(type(http_client), httpx.AsyncClient)


class ProxyParityTests(_ParityCase):
    """Proxy routing is decided by the SDK and the environment, never by us."""

    def test_https_proxy(self):
        self.assert_parity(HTTPS_PROXY="http://proxy.internal:3128")

    def test_http_proxy(self):
        self.assert_parity(HTTP_PROXY="http://proxy.internal:3128")

    def test_all_proxy(self):
        self.assert_parity(ALL_PROXY="http://proxy.internal:3128")

    def test_no_proxy_exemption(self):
        self.assert_parity(
            HTTPS_PROXY="http://proxy.internal:3128", NO_PROXY="api.openai.com"
        )

    def test_both_proxies_with_a_multi_host_no_proxy(self):
        self.assert_parity(
            HTTP_PROXY="http://plain.internal:3128",
            HTTPS_PROXY="http://secure.internal:3128",
            NO_PROXY="api.openai.com,example.internal",
        )

    def test_an_https_proxy_is_actually_used_and_not_merely_equal(self):
        """Guard against both sides being equally *un*proxied.

        Every parity assertion above would pass if telemetry-on and
        telemetry-off both lost the proxy, which is the MAJ-4 failure mode. This
        pins the baseline: with ``HTTPS_PROXY`` set, an instrumented SDK client
        must still resolve to a proxy pool.
        """
        for name, build, url in SDKS:
            for posture in POSTURE_ON:
                with self.subTest(sdk=name, posture=posture):
                    with posture_environment(
                        posture, HTTPS_PROXY="http://proxy.internal:3128"
                    ):
                        described = route_description(build()._client, url)
                        self.assertEqual(described[1], "AsyncHTTPProxy")
                        self.assertEqual(described[2], "http://proxy.internal:3128")

    def test_a_no_proxy_host_keeps_its_none_mount_rather_than_a_wrapper(self):
        """``NO_PROXY`` mounts are ``None``; httpx reads that as "use default"."""
        for name, build, _ in SDKS:
            for posture in POSTURE_ON:
                with self.subTest(sdk=name, posture=posture):
                    with posture_environment(
                        posture,
                        HTTPS_PROXY="http://proxy.internal:3128",
                        NO_PROXY="api.openai.com",
                    ):
                        http_client = build()._client
                        self.assertIn(None, http_client._mounts.values())
                        described = route_description(http_client, OPENAI_URL)
                        self.assertEqual(described[1], "AsyncConnectionPool")
                        self.assertIsNone(described[2])


class ExplicitlyConfiguredClientParityTests(unittest.TestCase):
    """An operator-supplied SDK client is instrumented, never replaced.

    These construct the SDK the way an operator would if they needed an explicit
    proxy, a custom transport or relaxed TLS: through the SDK's own
    ``DefaultAsyncHttpxClient``, so the SDK's defaults still apply. Telemetry
    must leave every one of those choices intact.
    """

    def _pair(self, **client_kwargs):
        """The same explicit configuration, uninstrumented and instrumented."""
        baseline = anthropic.AsyncAnthropic(
            api_key=API_KEY, timeout=config.REQUEST_TIMEOUT,
            http_client=anthropic.DefaultAsyncHttpxClient(**client_kwargs),
        )
        instrumented = anthropic.AsyncAnthropic(
            api_key=API_KEY, timeout=config.REQUEST_TIMEOUT,
            http_client=anthropic.DefaultAsyncHttpxClient(**client_kwargs),
        )
        instrument_sdk_client(instrumented, provider="anthropic")
        return baseline, instrumented

    def assert_pair_equivalent(self, urls=(ANTHROPIC_URL, OPENAI_URL), **client_kwargs):
        baseline, instrumented = self._pair(**client_kwargs)
        self.assertEqual(
            full_description(instrumented, urls), full_description(baseline, urls)
        )
        self.assertTrue(is_instrumented(instrumented._client))
        self.assertFalse(is_instrumented(baseline._client))
        return baseline, instrumented

    def test_an_explicit_proxy_argument(self):
        _, instrumented = self.assert_pair_equivalent(
            proxy="http://explicit.internal:8080"
        )
        self.assertEqual(
            route_description(instrumented._client, ANTHROPIC_URL)[2],
            "http://explicit.internal:8080",
        )

    def test_an_explicit_custom_transport(self):
        custom = make_mock_transport([200])
        baseline, instrumented = self._pair(transport=custom)
        self.assertIs(
            baseline._client._transport_for_url(ANTHROPIC_URL), custom
        )
        self.assertIs(
            unwrap(instrumented._client._transport_for_url(ANTHROPIC_URL)), custom
        )
        self.assertEqual(
            client_description(instrumented._client),
            client_description(baseline._client),
        )

    def test_tls_verification_disabled_is_preserved_rather_than_re_enabled(self):
        _, instrumented = self.assert_pair_equivalent(verify=False)
        ssl_flags = route_description(instrumented._client, ANTHROPIC_URL)[8]
        self.assertEqual(ssl_flags[0].name, "CERT_NONE")
        self.assertFalse(ssl_flags[1])

    def test_a_custom_ssl_context_is_preserved_by_identity(self):
        import ssl

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        _, instrumented = self._pair(verify=context)
        pool = unwrap(instrumented._client._transport_for_url(ANTHROPIC_URL))._pool
        self.assertIs(pool._ssl_context, context)

    def test_custom_limits_are_preserved(self):
        limits = httpx.Limits(
            max_connections=7, max_keepalive_connections=3, keepalive_expiry=11.0
        )
        _, instrumented = self.assert_pair_equivalent(limits=limits)
        described = route_description(instrumented._client, ANTHROPIC_URL)
        self.assertEqual(described[3:6], (7, 3, 11.0))

    def test_explicit_mounts_including_none_are_each_handled(self):
        special = make_mock_transport([200])
        _, instrumented = self._pair(
            mounts={"all://api.openai.com": special, "all://nothing.internal": None}
        )
        http_client = instrumented._client
        self.assertIs(unwrap(http_client._transport_for_url(OPENAI_URL)), special)
        self.assertIn(None, http_client._mounts.values())


class SdkShapePinningTests(unittest.TestCase):
    """The private boundary is asserted on every call, never assumed."""

    def test_the_installed_sdks_have_the_pinned_shape(self):
        for name, module, factory in (
            ("anthropic", anthropic, lambda: anthropic.AsyncAnthropic(api_key=API_KEY)),
            ("openai", openai, lambda: openai.AsyncOpenAI(api_key=API_KEY)),
        ):
            with self.subTest(sdk=name):
                sdk_client = factory()
                self.assertTrue(hasattr(sdk_client, "_client"))
                http_client = sdk_http_client(sdk_client)
                self.assertIsInstance(http_client, httpx.AsyncClient)
                self.assertIsInstance(http_client, module.DefaultAsyncHttpxClient)

    def test_a_client_without_the_private_attribute_fails_loudly(self):
        class Foreign:
            pass

        with self.assertRaises(TelemetrySdkShapeUnsupported):
            instrument_sdk_client(Foreign(), provider="anthropic")

    def test_a_non_httpx_client_attribute_fails_loudly(self):
        class Foreign:
            _client = object()

        with self.assertRaises(TelemetrySdkShapeUnsupported):
            instrument_sdk_client(Foreign(), provider="anthropic")

    def test_a_client_the_sdk_did_not_default_fails_loudly(self):
        """The load-bearing check: a bare httpx client is not the SDK's client.

        This is the AUD-2 substitution itself, arriving from the other
        direction. If some caller hands the SDK a plain ``httpx.AsyncClient``,
        its limits and redirect policy are httpx's, not the SDK's — and
        telemetry refuses to certify that as instrumented-and-unchanged.
        """
        sdk_client = anthropic.AsyncAnthropic(
            api_key=API_KEY, http_client=httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT)
        )
        with self.assertRaises(TelemetrySdkShapeUnsupported):
            instrument_sdk_client(sdk_client, provider="anthropic")

    def test_instrumenting_twice_is_refused_rather_than_nested(self):
        """Two wrappers would mean two attempt rows for one HTTP request."""
        sdk_client = anthropic.AsyncAnthropic(api_key=API_KEY)
        instrument_sdk_client(sdk_client, provider="anthropic")
        with self.assertRaises(TelemetrySdkShapeUnsupported.__base__):
            instrument_sdk_client(sdk_client, provider="anthropic")

    def test_telemetry_never_constructs_a_provider_client(self):
        """Structural: no telemetry module may build an httpx client at all.

        The AUD-2 defect was one call to ``httpx.AsyncClient(...)`` inside the
        telemetry package. Asserting its absence is what stops the fix from
        being undone by a future convenience helper.
        """
        package = ROOT / "provider_telemetry"
        offenders = []
        for path in sorted(package.glob("*.py")):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "httpx.AsyncClient(" in stripped or "AsyncHTTPTransport(" in stripped:
                    offenders.append(f"{path.name}:{number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_llm_client_never_passes_an_http_client_to_an_sdk(self):
        """Parsed, not grepped: prose about ``http_client=`` is not a call.

        The defect was a keyword argument. This walks every call in the module
        and requires that none of them names one.
        """
        import ast

        tree = ast.parse((ROOT / "llm_client.py").read_text(encoding="utf-8"))
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "http_client"
        ]
        self.assertEqual(offenders, [])


class RequestBehaviourParityTests(unittest.IsolatedAsyncioTestCase):
    """Issuing requests: same count, same URL, same reuse, same shutdown."""

    def _sdk(self, statuses, *, instrumented: bool):
        transport = make_mock_transport(statuses)
        sdk_client = anthropic.AsyncAnthropic(
            api_key=API_KEY, timeout=config.REQUEST_TIMEOUT,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=transport),
        )
        if instrumented:
            instrument_sdk_client(sdk_client, provider="anthropic")
        return sdk_client, transport

    async def _create(self, sdk_client):
        return await sdk_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16,
            messages=[{"role": "user", "content": "hello"}],
        )

    async def test_the_sdk_issues_the_same_number_of_http_requests(self):
        """The SDK's internal retry loop is observed, not altered."""
        with posture_environment("observational"):
            counts = {}
            for instrumented in (False, True):
                sdk_client, transport = self._sdk(
                    [429, 429, 200], instrumented=instrumented
                )
                await self._create(sdk_client)
                counts[instrumented] = len(transport.requests)
                await sdk_client.close()
            self.assertEqual(counts[True], counts[False])
            self.assertEqual(counts[False], 3, "the retry loop did not actually run")

    async def test_the_final_request_url_is_identical(self):
        with posture_environment("observational"):
            urls = {}
            for instrumented in (False, True):
                sdk_client, transport = self._sdk([200], instrumented=instrumented)
                await self._create(sdk_client)
                urls[instrumented] = str(transport.requests[-1].url)
                await sdk_client.close()
            self.assertEqual(urls[True], urls[False])
            self.assertEqual(urls[False], str(ANTHROPIC_URL))

    async def test_the_request_headers_the_sdk_sets_are_identical(self):
        """Telemetry reads three response headers and sets none on the request."""
        with posture_environment("observational"):
            headers = {}
            for instrumented in (False, True):
                sdk_client, transport = self._sdk([200], instrumented=instrumented)
                await self._create(sdk_client)
                headers[instrumented] = sorted(
                    name for name, _ in transport.requests[-1].headers.multi_items()
                )
                await sdk_client.close()
            self.assertEqual(headers[True], headers[False])

    async def test_the_client_and_its_pool_are_reused_across_requests(self):
        with posture_environment("observational"):
            sdk_client, transport = self._sdk([200, 200, 200], instrumented=True)
            http_client = sdk_client._client
            resolved = http_client._transport_for_url(ANTHROPIC_URL)
            for _ in range(3):
                await self._create(sdk_client)
            self.assertIs(sdk_client._client, http_client)
            self.assertIs(
                http_client._transport_for_url(ANTHROPIC_URL), resolved,
                "a per-request transport would mean a per-request connection pool",
            )
            self.assertEqual(len(transport.requests), 3)
            await sdk_client.close()

    async def test_closing_the_sdk_closes_the_wrapped_transports(self):
        closed = []

        class Recording(httpx.AsyncBaseTransport):
            def __init__(self, name):
                self.name = name

            async def aclose(self):
                closed.append(self.name)

            async def handle_async_request(self, request):  # pragma: no cover
                raise AssertionError("no request is issued by this test")

        sdk_client = anthropic.AsyncAnthropic(
            api_key=API_KEY, http_client=anthropic.DefaultAsyncHttpxClient(
                transport=Recording("default"),
                mounts={"all://api.openai.com": Recording("mounted")},
            ),
        )
        instrument_sdk_client(sdk_client, provider="anthropic")
        await sdk_client.close()
        self.assertEqual(sorted(closed), ["default", "mounted"])
        self.assertTrue(sdk_client._client.is_closed)


class StrictStartFailureTests(unittest.IsolatedAsyncioTestCase):
    """Strict may refuse to send. It may not reconfigure the client to do so."""

    async def test_a_failed_start_stops_the_request_without_changing_the_client(self):
        from provider_telemetry.capture import InvocationCapture, capture_scope
        import provider_telemetry.service as service

        class _RefusingSession:
            posture = POSTURE_STRICT
            strict = True

            async def persist_attempt_start(self, record):
                raise TelemetryStartUnavailable("sink is down")

        transport = make_mock_transport([200])
        sdk_client = anthropic.AsyncAnthropic(
            api_key=API_KEY, timeout=config.REQUEST_TIMEOUT, max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=transport),
        )
        instrument_sdk_client(sdk_client, provider="anthropic")
        before = full_description(sdk_client, (ANTHROPIC_URL, OPENAI_URL))

        buffer = InvocationCapture(
            invocation_id="00000000-0000-4000-8000-0000000000a1",
            call_id="00000000-0000-4000-8000-0000000000a2",
            telemetry_run_id="00000000-0000-4000-8000-0000000000a3",
            posture=POSTURE_STRICT,
            worker_id="parity-worker",
            provider="anthropic",
            requested_model="claude-sonnet-4-6",
        )
        token = service._session.set(_RefusingSession())
        try:
            with capture_scope(buffer):
                with self.assertRaises(Exception):
                    await sdk_client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=16,
                        messages=[{"role": "user", "content": "hello"}],
                    )
        finally:
            service._session.reset(token)

        # Fail-closed: nothing left for the wire.
        self.assertEqual(transport.requests, [])
        # And the client is exactly the client it was.
        self.assertEqual(full_description(sdk_client, (ANTHROPIC_URL, OPENAI_URL)), before)
        await sdk_client.close()


class ReplacementClientMutationTests(unittest.TestCase):
    """The bounded mutation: put the pre-remediation substitution back.

    This test asserts the *defect*. It fails the moment
    ``httpx.AsyncClient(timeout=REQUEST_TIMEOUT)`` stops being a materially
    different client from the one the SDK builds — which is the only way the
    parity assertions above could be vacuous.
    """

    def _mutated(self, module, factory):
        """Exactly what ``_telemetry_http_client`` used to hand the SDK."""
        replacement = httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT)
        telemetry_transport.instrument_http_client(replacement, provider="anthropic")
        return factory(replacement)

    def test_the_replacement_client_changes_connection_limits_and_redirects(self):
        for name, module, factory in (
            ("anthropic", anthropic,
             lambda hc: anthropic.AsyncAnthropic(
                 api_key=API_KEY, timeout=config.REQUEST_TIMEOUT, http_client=hc)),
            ("openai", openai,
             lambda hc: openai.AsyncOpenAI(
                 api_key=API_KEY, timeout=config.REQUEST_TIMEOUT, http_client=hc)),
        ):
            with self.subTest(sdk=name):
                with posture_environment(POSTURE_OFF):
                    correct = (
                        llm_client._get_anthropic() if name == "anthropic"
                        else llm_client._get_openai()
                    )
                    correct_pool = unwrap(
                        correct._client._transport_for_url(ANTHROPIC_URL)
                    )._pool
                    correct_limits = (
                        correct_pool._max_connections,
                        correct_pool._max_keepalive_connections,
                    )
                    correct_follow = correct._client.follow_redirects

                mutated = self._mutated(module, factory)
                mutated_pool = unwrap(
                    mutated._client._transport_for_url(ANTHROPIC_URL)
                )._pool
                mutated_limits = (
                    mutated_pool._max_connections,
                    mutated_pool._max_keepalive_connections,
                )

                # The finding's exact numbers, in both directions.
                self.assertEqual(correct_limits, (1000, 100))
                self.assertEqual(mutated_limits, (100, 20))
                self.assertNotEqual(mutated_limits, correct_limits)

                self.assertTrue(correct_follow)
                self.assertFalse(mutated._client.follow_redirects)

    def test_the_parity_suite_itself_rejects_the_replacement_client(self):
        """The mutation is load-bearing against *these* assertions, not just in
        principle: run the suite's own comparison against the mutated client and
        require it to fail, naming a connection-limit or redirect difference."""
        with posture_environment(POSTURE_OFF):
            baseline = full_description(llm_client._get_anthropic(), (ANTHROPIC_URL,))

        mutated = anthropic.AsyncAnthropic(
            api_key=API_KEY, timeout=config.REQUEST_TIMEOUT,
            http_client=httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT),
        )
        described = full_description(mutated, (ANTHROPIC_URL,))
        self.assertNotEqual(described, baseline)

        # And specifically on the two properties the audit reproduced.
        self.assertNotEqual(described[1][2], baseline[1][2])          # follow_redirects
        self.assertNotEqual(described[2][0][3:5], baseline[2][0][3:5])  # limits


if __name__ == "__main__":  # pragma: no cover - direct invocation
    unittest.main()
