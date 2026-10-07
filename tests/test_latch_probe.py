"""Authentication latch (brute-force protection), the probe API and client-build errors."""

from __future__ import annotations

import base64

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from fake_nextcloud import USER_ID, FakeNextcloud
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
