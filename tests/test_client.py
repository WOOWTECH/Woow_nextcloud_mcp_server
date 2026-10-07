from __future__ import annotations

import asyncio
import re
import ssl
import sys
import types
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastmcp.exceptions import ToolError

from fake_nextcloud import BASE_URL, LOGIN, PASSWORD, USER_ID, FakeNextcloud
from nextcloud_mcp_server import __version__
from nextcloud_mcp_server.client import (
    MAX_XML_BYTES,
    USER_AGENT,
    BackendPolicyError,
    BodyTooLarge,
    NextcloudClient,
    _redirect_target,
    load_backend_policy,
)
from nextcloud_mcp_server.errors import NextcloudHTTPError, network_message
from nextcloud_mcp_server.settings import Settings


def _client(settings: Settings, handler: Callable[[httpx.Request], httpx.Response]):
    return NextcloudClient(settings, transport=httpx.MockTransport(handler))


async def test_client_kwargs_and_headers(settings: Settings, fake: FakeNextcloud) -> None:
    nc = NextcloudClient(settings, transport=fake.transport)
    kwargs = nc.client_kwargs()
    assert kwargs["follow_redirects"] is False
    assert kwargs["trust_env"] is False
    assert kwargs["verify"] is True
    assert kwargs["timeout"] == httpx.Timeout(30.0, connect=10.0)
    assert kwargs["headers"] == {"User-Agent": f"woow-nextcloud-mcp-server/{__version__}"}
    assert isinstance(kwargs["transport"], httpx.MockTransport)
    http = await nc.http()
    assert http is await nc.http()  # one client per lifetime
    assert str(http.base_url) == BASE_URL + "/"
    assert http.follow_redirects is False
    assert await nc.user_id() == USER_ID
    request = fake.requests[0]
    assert request.headers["User-Agent"] == USER_AGENT
    assert request.headers["OCS-APIRequest"] == "true"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Authorization"].startswith("Basic ")
    assert str(request.url) == f"{BASE_URL}/ocs/v2.php/cloud/user?format=json"
    await nc.aclose()
    await nc.aclose()  # idempotent


def test_timeouts_follow_settings(make_settings) -> None:
    nc = NextcloudClient(make_settings(request_timeout=7, verify_tls=False))
    kwargs = nc.client_kwargs()
    assert kwargs["timeout"] == httpx.Timeout(7.0, connect=10.0)
    assert kwargs["verify"] is False
    assert "transport" not in kwargs


async def test_plain_client_without_transport(make_settings) -> None:
    nc = NextcloudClient(make_settings())
    http = await nc.http()
    assert isinstance(http, httpx.AsyncClient)
    assert http.headers["User-Agent"] == USER_AGENT
    await nc.aclose()


async def test_user_id_is_cached_and_resolved_lazily(
    settings: Settings, fake: FakeNextcloud
) -> None:
    nc = NextcloudClient(settings, transport=fake.transport)
    assert fake.requests == []  # nothing at construction
    ids = await asyncio.gather(nc.user_id(), nc.user_id(), nc.user_id())
    assert ids == [USER_ID] * 3
    assert await nc.files_home() == f"{BASE_URL}/remote.php/dav/files/alice%20smith/"
    assert await nc.calendars_home() == f"{BASE_URL}/remote.php/dav/calendars/alice%20smith/"
    assert await nc.file_url("a #1/b?.md") == (
        f"{BASE_URL}/remote.php/dav/files/alice%20smith/a%20%231/b%3F.md"
    )
    assert fake.user_requests == 1
    await nc.aclose()


async def test_user_id_401(settings: Settings) -> None:
    nc = _client(settings, lambda r: httpx.Response(401, text="nope"))
    with pytest.raises(ToolError, match=r"^Nextcloud rejected the username or app password\. Fix"):
        await nc.user_id()


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404, text="not here"), "Could not read the Nextcloud account (404)"),
        (httpx.Response(200, text="<html>login</html>"), "did not answer like a Nextcloud"),
        (httpx.Response(200, json={"ocs": {"data": {}}}), "did not answer like a Nextcloud"),
        (httpx.Response(200, json={"ocs": {"data": {"id": ""}}}), "did not answer like"),
        (httpx.Response(200, json=[1, 2]), "did not answer like a Nextcloud"),
        (httpx.Response(200, content=b"x" * (1024 * 1024 + 1)), "unexpectedly large"),
    ],
)
async def test_user_id_errors(settings: Settings, response: httpx.Response, message: str) -> None:
    nc = _client(settings, lambda r: response)
    with pytest.raises(ToolError, match=message.replace("(", r"\(").replace(")", r"\)")):
        await nc.user_id()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_redirect_is_refused(settings: Settings, status: int) -> None:
    location = "https://user:pw@login.example.com:8443/nc/index.php/login?redirect_url=%2Fsecret#x"
    nc = _client(settings, lambda r: httpx.Response(status, headers={"Location": location}))
    with pytest.raises(ToolError) as info:
        await nc.user_id()
    assert str(info.value) == (
        "Nextcloud answered with a redirect to https://login.example.com:8443/nc/index.php/login; "
        "set NEXTCLOUD_MCP_BASE_URL to the final address."
    )


def test_redirect_target_variants() -> None:
    assert _redirect_target("https://a.example/x/y", "/login") == "https://a.example/login"
    assert _redirect_target("https://a.example/x/y", None) == "an unknown address"
    assert _redirect_target("https://a.example/x/y", "http://[bad") == "an unknown address"


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (httpx.ConnectTimeout("t"), "timed out"),
        (httpx.ReadTimeout("t"), "timed out"),
        (httpx.ConnectError("[Errno 111] Connection refused"), "connection failed"),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] bad"), "TLS certificate"),
        (httpx.ConnectError("SSL: WRONG_VERSION_NUMBER"), "TLS error"),
        (httpx.RemoteProtocolError("closed"), "closed unexpectedly"),
        (httpx.ReadError("boom"), "network error ReadError"),
    ],
)
async def test_network_errors(settings: Settings, exc: Exception, reason: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    nc = _client(settings, handler)
    with pytest.raises(ToolError) as info:
        await nc.user_id()
    message = str(info.value)
    assert message.startswith("Could not reach Nextcloud (")
    assert message.endswith(").")
    assert reason in message


def test_network_message_ssl_cause() -> None:
    error = httpx.ConnectError("handshake")
    error.__cause__ = ssl.SSLCertVerificationError("x")
    assert "TLS certificate verification failed" in network_message(error)
    other = httpx.ConnectError("handshake")
    other.__cause__ = ssl.SSLError("x")
    assert "TLS error" in network_message(other)


async def test_error_while_streaming_body(settings: Settings) -> None:
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            raise httpx.ReadError("cut")

    nc = _client(settings, lambda r: httpx.Response(200, stream=Broken()))
    with pytest.raises(ToolError, match=r"Could not reach Nextcloud \(network error ReadError\)"):
        await nc.send("GET", BASE_URL + "/x", label="x", op="read")


async def test_body_cap(settings: Settings) -> None:
    nc = _client(settings, lambda r: httpx.Response(200, content=b"abcdef"))
    with pytest.raises(BodyTooLarge):
        await nc.send("GET", BASE_URL + "/x", label="x", op="read", max_body=5)
    reply = await nc.send("GET", BASE_URL + "/x", label="x", op="read", max_body=6)
    assert reply.body == b"abcdef"

    class NoLength(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"abc"
            yield b"def"

    nc2 = _client(settings, lambda r: httpx.Response(200, stream=NoLength()))
    with pytest.raises(BodyTooLarge):
        await nc2.send("GET", BASE_URL + "/x", label="x", op="read", max_body=4)


async def test_error_body_is_capped_and_message_extracted(settings: Settings) -> None:
    body = (
        b'<?xml version="1.0"?><d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
        b"<s:message>Bad chunk</s:message></d:error>"
    )
    nc = _client(settings, lambda r: httpx.Response(400, content=body))
    with pytest.raises(NextcloudHTTPError) as info:
        await nc.send("GET", BASE_URL + "/x", label="x", op="read")
    assert str(info.value) == 'Nextcloud refused the request for "x" (400): Bad chunk.'
    assert info.value.status == 400

    huge = _client(settings, lambda r: httpx.Response(400, content=b"y" * 200_000))
    with pytest.raises(NextcloudHTTPError) as info:
        await huge.send("GET", BASE_URL + "/x", label="x", op="read")
    assert str(info.value) == 'Nextcloud refused the request for "x" (400).'


# ----------------------------------------------------------------------- backend_policy hook


class BackendHTTPError(httpx.HTTPError):
    """Like the gateway's: non-2xx answers are raised, carrying only ``status``."""

    def __init__(self, status: int) -> None:
        self.status = int(status)
        super().__init__(f"BACKEND_HTTP_ERROR status={self.status}")


class BackendDenied(ValueError):
    def __init__(self) -> None:
        super().__init__("BACKEND_DESTINATION_DENIED")


class BackendBusy(httpx.HTTPError):
    def __init__(self) -> None:
        super().__init__("BACKEND_BUSY")


class BackendFailure(httpx.HTTPError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GatewayTransport(httpx.AsyncBaseTransport):
    """Mirror of a gateway transport around an inner (fake) transport.

    Refuses paths containing an encoded '%' (or '.'/'..' segments) with BackendDenied
    and turns every non-2xx answer into BackendHTTPError (headers and body are lost).
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.busy = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        from urllib.parse import unquote

        path = unquote(request.url.path)
        if "%" in path or any(part in (".", "..") for part in path.split("/")):
            raise BackendDenied()
        if self.busy:
            raise BackendBusy()
        response = await self.inner.handle_async_request(request)
        if not 200 <= response.status_code < 300:
            await response.aclose()
            raise BackendHTTPError(response.status_code)
        return response


def realistic_policy(transport: httpx.AsyncBaseTransport, calls: list[dict]):
    """Mirror of a gateway hook: it owns transport, trust_env and follow_redirects.

    Passing any of those again makes ``httpx.AsyncClient`` raise ``TypeError: got
    multiple values for keyword argument`` - exactly what a real gateway hook does.
    The transport behaves like the gateway's (see :class:`GatewayTransport`).
    """
    gateway = transport if isinstance(transport, GatewayTransport) else GatewayTransport(transport)

    def async_client(*, base_url: str, **kwargs):
        calls.append({"base_url": base_url, **kwargs})
        kwargs.pop("limits", None)
        return httpx.AsyncClient(
            base_url=base_url,
            transport=gateway,
            trust_env=False,
            follow_redirects=False,
            **kwargs,
        )

    return async_client


def test_realistic_policy_rejects_duplicates(fake: FakeNextcloud) -> None:
    hook = realistic_policy(fake.transport, [])
    with pytest.raises(TypeError, match="multiple values"):
        hook(base_url=BASE_URL, follow_redirects=False)


async def test_backend_policy_hook_is_used(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, fake: FakeNextcloud
) -> None:
    calls: list[dict] = []
    module = types.ModuleType("backend_policy")
    module.async_client = realistic_policy(fake.transport, calls)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend_policy", module)

    nc = NextcloudClient(settings, transport=httpx.MockTransport(lambda r: httpx.Response(599)))
    assert await nc.user_id() == USER_ID  # served by the hook's transport, not ours
    (call,) = calls
    assert set(call) == {"base_url", "auth", "timeout", "headers"}
    assert call["base_url"] == BASE_URL
    assert call["headers"] == {"User-Agent": USER_AGENT}
    assert call["timeout"] == httpx.Timeout(30.0, connect=10.0)
    http = await nc.http()
    assert http.follow_redirects is False
    await nc.aclose()


async def test_backend_policy_async_factory(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, fake: FakeNextcloud
) -> None:
    sync_hook = realistic_policy(fake.transport, [])

    async def async_client(*, base_url: str, **kwargs):
        return sync_hook(base_url=base_url, **kwargs)

    module = types.ModuleType("backend_policy")
    module.async_client = async_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend_policy", module)
    nc = NextcloudClient(settings)
    assert await nc.user_id() == USER_ID
    await nc.aclose()


def test_plain_client_kwargs_are_complete(settings: Settings, fake: FakeNextcloud) -> None:
    nc = NextcloudClient(settings, transport=fake.transport)
    assert set(nc.policy_kwargs()) == {"auth", "timeout", "headers"}
    assert set(nc.client_kwargs()) == {
        "auth",
        "timeout",
        "headers",
        "follow_redirects",
        "trust_env",
        "verify",
        "transport",
    }


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("x = 1\n", "no callable async_client"),
        ("async_client = 'not callable'\n", "no callable async_client"),
        ("import module_that_does_not_exist_xyz\n", "missing module"),
        ("raise RuntimeError('boom')\n", "could not be imported (RuntimeError)"),
        ("def broken(:\n", "could not be imported (SyntaxError)"),
    ],
)
def test_unusable_backend_policy_fails_at_construction(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path, source: str, fragment: str
) -> None:
    (tmp_path / "backend_policy.py").write_text(source)
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(BackendPolicyError, match=re.escape(fragment)):
        NextcloudClient(settings)


def test_backend_policy_absent(settings: Settings) -> None:
    assert load_backend_policy() is None
    assert NextcloudClient(settings).policy is None


async def test_explicit_policy_argument(settings: Settings, fake: FakeNextcloud) -> None:
    used: list[str] = []

    hook = realistic_policy(fake.transport, [])

    def factory(*, base_url: str, **kwargs):
        used.append(base_url)
        return hook(base_url=base_url, **kwargs)

    nc = NextcloudClient(settings, policy=factory)
    await nc.user_id()
    assert used == [BASE_URL]
    await nc.aclose()


def test_login_and_password_are_used(settings: Settings) -> None:
    auth = NextcloudClient(settings).client_kwargs()["auth"]
    request = next(auth.auth_flow(httpx.Request("GET", BASE_URL)))
    import base64

    expected = base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
    assert request.headers["Authorization"] == f"Basic {expected}"


@pytest.mark.parametrize(
    "exc",
    [httpx.DecodingError("bad gzip"), httpx.StreamConsumed(), httpx.InvalidURL("x")],
)
async def test_non_transport_httpx_errors_become_tool_errors(settings: Settings, exc) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    nc = _client(settings, handler)
    with pytest.raises(ToolError) as info:
        await nc.send("GET", BASE_URL + "/x", label="x", op="read")
    assert (
        str(info.value) == f"Nextcloud returned an error ({type(exc).__name__}); try again later."
    )


async def test_decoding_error_while_reading_body(settings: Settings) -> None:
    nc = _client(
        settings,
        lambda r: httpx.Response(200, content=b"not gzip", headers={"Content-Encoding": "gzip"}),
    )
    with pytest.raises(ToolError, match=r"Nextcloud returned an error \(DecodingError\)"):
        await nc.send("GET", BASE_URL + "/x", label="x", op="read")


async def test_body_too_large_is_a_tool_error_with_label(settings: Settings) -> None:
    nc = _client(settings, lambda r: httpx.Response(200, content=b"abcdef"))
    with pytest.raises(ToolError) as info:
        await nc.send("PUT", BASE_URL + "/x", label="Docs/x.txt", op="create", max_body=3)
    assert isinstance(info.value, BodyTooLarge)
    assert str(info.value) == (
        'Nextcloud\'s answer for "Docs/x.txt" is larger than 3 bytes and was not processed.'
    )


def test_xml_cap_is_8_mib() -> None:
    assert MAX_XML_BYTES == 8 * 1024 * 1024


def test_ca_bundle_builds_ssl_context(make_settings, tmp_path) -> None:
    import ssl

    import certifi

    bundle = tmp_path / "ca.pem"
    bundle.write_bytes(Path(certifi.where()).read_bytes())
    nc = NextcloudClient(make_settings(ca_bundle=str(bundle)))
    assert isinstance(nc.client_kwargs()["verify"], ssl.SSLContext)
    insecure = NextcloudClient(make_settings(ca_bundle=str(bundle), verify_tls=False))
    assert insecure.client_kwargs()["verify"] is False
