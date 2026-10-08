"""Authentication latch (brute-force protection), the probe API and client-build errors."""

from __future__ import annotations

import base64

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from fake_nextcloud import PASSWORD, USER_ID, FakeNextcloud
from nextcloud_mcp_server.client import NextcloudClient
from nextcloud_mcp_server.errors import AUTH_LATCHED_MESSAGE, THROTTLED_MESSAGE
from nextcloud_mcp_server.server import create_server
from nextcloud_mcp_server.tools import NextcloudTools
from test_client import BackendDenied, GatewayTransport, realistic_policy


def _ocs_status(status: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    return handler


@pytest.mark.parametrize(
    ("status", "message"), [(401, AUTH_LATCHED_MESSAGE), (429, THROTTLED_MESSAGE)]
)
async def test_latch_after_failed_login(settings, status: int, message: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status)

    nc = NextcloudClient(settings, transport=httpx.MockTransport(handler))
    tools = NextcloudTools(nc, settings)
    with pytest.raises(ToolError) as first:
        await tools.get_file_tree("")
    assert str(first.value) == message
    assert len(seen) == 1
    for call in (
        tools.get_file_tree(""),
        tools.read_text_file("a.txt"),
        tools.create_text_file("a.txt", "x"),
        nc.probe(),
        nc.user_id(),
    ):
        with pytest.raises(ToolError) as again:
            await call
        assert str(again.value) == message
    assert len(seen) == 1  # nothing more reached the backend
    # the latch is process-wide: a new client (same process) does not log in either
    other = NextcloudClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ToolError, match="restart"):
        await other.probe()
    assert len(seen) == 1
    await nc.aclose()


async def test_throttle_message_has_no_base_url_hint(settings) -> None:
    nc = NextcloudClient(settings, transport=httpx.MockTransport(_ocs_status(429)))
    with pytest.raises(ToolError) as info:
        await nc.user_id()
    assert "BASE_URL" not in str(info.value)
    assert str(info.value) == THROTTLED_MESSAGE


async def test_latch_after_401_on_a_dav_request(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    await tools.get_file_tree("")  # user id cached, listing fine
    fake.override = lambda r: httpx.Response(401)
    with pytest.raises(ToolError, match="Fix the credentials"):
        await tools.read_text_file("Docs/readme.md")
    fake.override = None  # even if the credentials were fixed, nothing is sent any more
    before = len(fake.requests)
    with pytest.raises(ToolError, match="Fix the credentials"):
        await tools.get_file_tree("")
    with pytest.raises(ToolError, match="Fix the credentials"):
        await tools.nc.probe()
    assert len(fake.requests) == before


async def test_412_follow_up_reports_the_latch(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    def override(request: httpx.Request) -> httpx.Response | None:
        if request.method == "PUT":
            return httpx.Response(412)
        return httpx.Response(401)

    await tools.nc.user_id()
    fake.override = override
    with pytest.raises(ToolError, match="Fix the credentials"):
        await tools.update_text_file("Docs/readme.md", "x", "e")


@pytest.mark.parametrize(
    ("status", "message"), [(401, AUTH_LATCHED_MESSAGE), (429, THROTTLED_MESSAGE)]
)
async def test_latch_through_gateway_hook(settings, fake: FakeNextcloud, status, message) -> None:
    fake.override = lambda r: httpx.Response(status)
    gateway = GatewayTransport(fake.transport)
    calls: list[dict] = []
    nc = NextcloudClient(settings, policy=realistic_policy(gateway, calls))
    tools = NextcloudTools(nc, settings)
    await nc.user_id()  # OCS is not overridden by the fake
    with pytest.raises(ToolError) as info:
        await tools.get_file_tree("")
    assert str(info.value) == message
    before = len(fake.requests)
    for call in (tools.get_file_tree(""), nc.probe(), tools.upload_file("x.bin", "AAEC")):
        with pytest.raises(ToolError, match="restart the server"):
            await call
    assert len(fake.requests) == before
    await nc.aclose()


async def test_probe(settings, fake: FakeNextcloud) -> None:
    nc = NextcloudClient(settings, transport=fake.transport)
    assert await nc.probe() == {"ok": True, "user_id": USER_ID}
    assert await nc.probe() == {"ok": True, "user_id": USER_ID}
    assert fake.user_requests == 2  # every probe checks the backend
    assert await nc.user_id() == USER_ID
    assert fake.user_requests == 2  # and fills the shared user-id cache
    assert all("/ocs/" in str(r.url) for r in fake.requests)  # no file or calendar data
    await nc.aclose()


async def test_probe_errors_like_tools(settings) -> None:
    nc = NextcloudClient(settings, transport=httpx.MockTransport(_ocs_status(503)))
    with pytest.raises(ToolError, match=r"Could not read the Nextcloud account \(503\)"):
        await nc.probe()


async def test_probe_on_server_is_not_a_tool(settings, fake: FakeNextcloud) -> None:
    server = create_server(settings, transport=fake.transport)
    assert await server.nextcloud_client.probe() == {"ok": True, "user_id": USER_ID}
    async with Client(server) as client:
        names = [tool.name for tool in await client.list_tools()]
    assert "probe" not in " ".join(names)


async def test_hook_refusing_base_url_at_client_build(settings, fake: FakeNextcloud) -> None:
    def async_client(*, base_url: str, **kwargs):
        raise BackendDenied()  # e.g. a base URL the gateway's policy does not allow

    nc = NextcloudClient(settings, policy=async_client)
    tools = NextcloudTools(nc, settings)
    for call in (nc.probe(), tools.get_file_tree(""), tools.read_text_file("a.txt")):
        with pytest.raises(ToolError, match="The gateway refused this request"):
            await call


async def test_tree_skips_folders_the_gateway_refuses(settings, fake: FakeNextcloud) -> None:
    fake.add_file("100% done/inside.txt", b"x")
    nc = NextcloudClient(settings, policy=realistic_policy(GatewayTransport(fake.transport), []))
    tools = NextcloudTools(nc, settings)
    result = await tools.get_file_tree("", depth=2)
    paths = [e["path"] for e in result["entries"]]
    assert "100% done" in paths  # listed itself (its name came from the parent listing)
    assert "100% done/inside.txt" not in paths
    assert "Docs/readme.md" in paths  # the other folders are still listed
    assert result["truncated"] is True
    shallow = await tools.get_file_tree("", depth=1)
    assert shallow["truncated"] is False
    await nc.aclose()


async def test_upload_without_etag_never_overwrites(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    before = fake.files["photo.png"].data
    with pytest.raises(ToolError, match=r'^"photo\.png" already exists'):
        await tools.upload_file("photo.png", base64.b64encode(b"new").decode())
    put = [r for r in fake.requests if r.method == "PUT"][-1]
    assert put.headers["If-None-Match"] == "*"
    assert "If-Match" not in put.headers
    assert fake.files["photo.png"].data == before


async def test_fake_refuses_unconditional_overwrite(fake: FakeNextcloud) -> None:
    async with httpx.AsyncClient(transport=fake.transport) as http:
        answer = await http.put(
            "https://cloud.example.com/nc/remote.php/dav/files/alice%20smith/photo.png",
            content=b"x",
        )
    assert answer.status_code == 428


# ----------------------------------------------------------------- per-credentials latch


def _counting(status_for_password: dict[str, int]):
    """Handler answering by password: 200 with a user id, or the given error status."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].split()[1]
        password = base64.b64decode(token).decode().split(":", 1)[1]
        seen.append(password)
        status = status_for_password.get(password, 200)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"ocs": {"data": {"id": USER_ID}}})

    return handler, seen


async def test_other_credentials_are_not_latched(make_settings) -> None:
    handler, seen = _counting({"wrong-pass": 401})
    bad = NextcloudClient(
        make_settings(app_password="wrong-pass"), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ToolError, match="Fix the credentials"):
        await bad.probe()
    with pytest.raises(ToolError, match="Fix the credentials"):
        await bad.probe()
    assert seen == ["wrong-pass"]

    # the operator saves the correct password: a new client with it is not blocked
    good = NextcloudClient(
        make_settings(app_password="right-pass"), transport=httpx.MockTransport(handler)
    )
    assert await good.probe() == {"ok": True, "user_id": USER_ID}
    assert seen == ["wrong-pass", "right-pass"]

    # a new client with the known-bad password still never retries
    again = NextcloudClient(
        make_settings(app_password="wrong-pass"), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ToolError, match="Fix the credentials"):
        await again.probe()
    assert seen == ["wrong-pass", "right-pass"]


@pytest.mark.parametrize(
    "change",
    [{"username": "bob"}, {"base_url": "https://other.example.com"}],
)
async def test_latch_is_keyed_by_user_and_server(make_settings, change) -> None:
    handler, seen = _counting({"pw": 401})
    first = NextcloudClient(
        make_settings(app_password="pw"), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ToolError):
        await first.probe()
    other = NextcloudClient(
        make_settings(app_password="pw", **change), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ToolError, match="Fix the credentials"):
        await other.probe()
    assert len(seen) == 2  # the other identity was tried once


def test_latch_key_holds_no_password(make_settings) -> None:
    from nextcloud_mcp_server.client import latch_key

    secret = "Very-Secret-App-Password-123"
    key = latch_key(make_settings(app_password=secret, base_url="HTTPS://Cloud.Example.com/NC"))
    assert secret not in repr(key)
    assert key[0] == "https://cloud.example.com/NC"
    assert len(key[2]) == 64
    assert key == latch_key(
        make_settings(app_password=secret, base_url="https://cloud.example.com/NC/")
    )


async def test_latch_log_has_no_key(make_settings, caplog) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    handler, _ = _counting({"secret-pw": 401})
    nc = NextcloudClient(
        make_settings(app_password="secret-pw"), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ToolError):
        await nc.probe()
    text = " ".join(r.getMessage() for r in caplog.records)
    assert nc.latch_key[2] not in text
    assert "secret-pw" not in text


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        (None, 300),
        ("120", 120),
        ("0", 1),
        ("99999", 900),
        ("soon", 300),
    ],
)
def test_retry_after_seconds(retry_after, expected) -> None:
    from nextcloud_mcp_server.client import retry_after_seconds

    assert retry_after_seconds(retry_after) == expected


def test_retry_after_http_date() -> None:
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    from nextcloud_mcp_server.client import retry_after_seconds

    future = format_datetime(datetime.now(UTC) + timedelta(seconds=600), usegmt=True)
    assert 590 <= retry_after_seconds(future) <= 600
    assert retry_after_seconds(format_datetime(datetime(2000, 1, 1, tzinfo=UTC), usegmt=True)) == 1


@pytest.mark.parametrize(("retry_after", "wait"), [(None, 300), ("60", 60), ("3600", 900)])
async def test_429_latch_expires(settings, monkeypatch, retry_after, wait) -> None:
    import nextcloud_mcp_server.client as client_module

    now = [1000.0]
    monkeypatch.setattr(client_module, "_clock", lambda: now[0])
    answers = [429, 200]
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = answers[len(seen)]
        seen.append(status)
        if status == 429:
            headers = {"Retry-After": retry_after} if retry_after else {}
            return httpx.Response(429, headers=headers)
        return httpx.Response(200, json={"ocs": {"data": {"id": USER_ID}}})

    nc = NextcloudClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ToolError, match="throttling"):
        await nc.probe()
    now[0] += wait - 1
    with pytest.raises(ToolError, match="throttling"):
        await nc.probe()
    assert len(seen) == 1  # still latched
    now[0] += 1
    assert await nc.probe() == {"ok": True, "user_id": USER_ID}
    assert len(seen) == 2


async def test_429_from_gateway_hook_uses_default_expiry(settings, fake, monkeypatch) -> None:
    import nextcloud_mcp_server.client as client_module

    now = [0.0]
    monkeypatch.setattr(client_module, "_clock", lambda: now[0])
    fake.override = lambda r: httpx.Response(429)
    nc = NextcloudClient(settings, policy=realistic_policy(GatewayTransport(fake.transport), []))
    await nc.user_id()
    with pytest.raises(ToolError, match="throttling"):
        await NextcloudTools(nc, settings).get_file_tree("")
    before = len(fake.requests)
    now[0] = 299
    with pytest.raises(ToolError, match="throttling"):
        await NextcloudTools(nc, settings).get_file_tree("")
    assert len(fake.requests) == before
    now[0] = 300
    fake.override = None
    assert (await NextcloudTools(nc, settings).get_file_tree(""))["entries"]
    await nc.aclose()


async def test_401_never_expires(settings, monkeypatch) -> None:
    import nextcloud_mcp_server.client as client_module

    now = [0.0]
    monkeypatch.setattr(client_module, "_clock", lambda: now[0])
    handler, seen = _counting({PASSWORD: 401})
    nc = NextcloudClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ToolError):
        await nc.probe()
    now[0] = 10**9
    with pytest.raises(ToolError, match="Fix the credentials"):
        await nc.probe()
    assert len(seen) == 1
