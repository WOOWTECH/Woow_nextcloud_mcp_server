from __future__ import annotations

import asyncio
import ssl
import sys
import types
from collections.abc import Callable

import httpx
import pytest
from fastmcp.exceptions import ToolError

from fake_nextcloud import BASE_URL, LOGIN, PASSWORD, USER_ID, FakeNextcloud
from nextcloud_mcp_server import __version__
from nextcloud_mcp_server.client import (
    USER_AGENT,
    BodyTooLarge,
    NextcloudClient,
    _redirect_target,
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
    with pytest.raises(ToolError, match=r"^Nextcloud rejected the username or app password\.$"):
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


async def test_backend_policy_hook_is_used(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, fake: FakeNextcloud
) -> None:
    calls: list[dict] = []

    def async_client(*, base_url: str, **kwargs):
        calls.append({"base_url": base_url, **kwargs})
        return httpx.AsyncClient(base_url=base_url, **kwargs)

    module = types.ModuleType("backend_policy")
    module.async_client = async_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend_policy", module)

    nc = NextcloudClient(settings, transport=fake.transport)
    assert await nc.user_id() == USER_ID
    assert len(calls) == 1
    call = calls[0]
    assert call["base_url"] == BASE_URL
    assert set(call) == {
        "base_url",
        "auth",
        "follow_redirects",
        "trust_env",
        "verify",
        "timeout",
        "headers",
        "transport",
    }
    assert call["follow_redirects"] is False
    assert call["trust_env"] is False
    assert call["headers"] == {"User-Agent": USER_AGENT}
    await nc.aclose()


async def test_backend_policy_async_factory(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, fake: FakeNextcloud
) -> None:
    async def async_client(*, base_url: str, **kwargs):
        return httpx.AsyncClient(base_url=base_url, **kwargs)

    module = types.ModuleType("backend_policy")
    module.async_client = async_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend_policy", module)
    nc = NextcloudClient(settings, transport=fake.transport)
    assert await nc.user_id() == USER_ID
    await nc.aclose()


async def test_backend_policy_without_factory_falls_back(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, fake: FakeNextcloud
) -> None:
    monkeypatch.setitem(sys.modules, "backend_policy", types.ModuleType("backend_policy"))
    nc = NextcloudClient(settings, transport=fake.transport)
    assert await nc.user_id() == USER_ID
    await nc.aclose()


async def test_backend_policy_broken_import_is_not_hidden(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path
) -> None:
    (tmp_path / "backend_policy.py").write_text("import module_that_does_not_exist_xyz\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    nc = NextcloudClient(settings)
    with pytest.raises(ModuleNotFoundError):
        await nc.http()


def test_login_and_password_are_used(settings: Settings) -> None:
    auth = NextcloudClient(settings).client_kwargs()["auth"]
    request = next(auth.auth_flow(httpx.Request("GET", BASE_URL)))
    import base64

    expected = base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
    assert request.headers["Authorization"] == f"Basic {expected}"
