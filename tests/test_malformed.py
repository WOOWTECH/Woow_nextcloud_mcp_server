"""Malformed answers never escape as non-ToolError exceptions (code mode: INVALID_RESPONSE)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastmcp.exceptions import ToolError

from fake_nextcloud import BASE_URL, USER_ID, FakeNextcloud
from nextcloud_mcp_server.client import NextcloudClient
from nextcloud_mcp_server.tools import NextcloudTools

PREFIX = "/nc/remote.php/dav/files/alice%20smith/"
INVALID = "BACKEND_INVALID_RESPONSE: "


@pytest.fixture
def coded_tools(make_settings, fake: FakeNextcloud) -> NextcloudTools:
    settings = make_settings(readonly=False, error_codes="true")
    return NextcloudTools(NextcloudClient(settings, transport=fake.transport), settings)


def _response(href: str, extra: str = "") -> str:
    rtype = (
        "<d:resourcetype><d:collection/></d:resourcetype>"
        if href.endswith("/")
        else ("<d:resourcetype/>")
    )
    return (
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{rtype}"
        f'<d:getetag>"e"</d:getetag>{extra}</d:prop>'
        "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    )


def _listing(*responses: str, declaration: str = "") -> bytes:
    return (declaration + '<d:multistatus xmlns:d="DAV:">' + "".join(responses)).encode() + (
        b"</d:multistatus>"
    )


async def _fails(tools: NextcloudTools, call) -> str:
    with pytest.raises(ToolError) as info:
        await tools.run(call)
    return str(info.value)


@pytest.mark.parametrize("encoding", ["x-bogus", "rot13"])
async def test_case1_unknown_xml_encoding(coded_tools, fake, encoding) -> None:
    body = _listing(_response(PREFIX), declaration=f'<?xml version="1.0" encoding="{encoding}"?>')
    fake.override = lambda r: httpx.Response(207, content=body)
    message = await _fails(coded_tools, coded_tools.get_file_tree(""))
    assert message == INVALID + "Nextcloud sent an answer that is not valid WebDAV XML."


async def test_case2_unparsable_href_is_skipped(coded_tools, fake) -> None:
    body = _listing(
        _response(PREFIX + "Docs/"),
        _response("http://[bad/x"),
        _response(PREFIX + "Docs/ok.txt"),
    )
    fake.override = lambda r: httpx.Response(207, content=body)
    result = await coded_tools.run(coded_tools.get_file_tree("Docs"))
    assert [e["path"] for e in result["entries"]] == ["Docs/ok.txt"]


async def test_case2_unparsable_calendar_href_is_skipped(coded_tools, fake) -> None:
    body = (
        b'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        b"<d:response><d:href>http://[bad/cal/</d:href><d:propstat><d:prop><d:resourcetype>"
        b"<d:collection/><c:calendar/></d:resourcetype></d:prop>"
        b"<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
    )
    fake.override = lambda r: httpx.Response(207, content=body)
    assert await coded_tools.run(coded_tools.list_calendars()) == {"calendars": []}


async def test_case3_overflowing_date(coded_tools, fake) -> None:
    body = _listing(
        _response(PREFIX + "Docs/"),
        _response(
            PREFIX + "Docs/old.txt",
            "<d:getlastmodified>Fri, 31 Dec 9999 23:59:59 -0100</d:getlastmodified>",
        ),
    )
    fake.override = lambda r: httpx.Response(207, content=body)
    result = await coded_tools.run(coded_tools.get_file_tree("Docs"))
    (entry,) = result["entries"]
    assert entry["path"] == "Docs/old.txt"
    assert entry["modified"] is None


def _ocs(content: bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"Content-Type": "application/json"})

    return handler


DEEP_JSON = b"[" * 200_000
SURROGATE_ID = b'{"ocs": {"data": {"id": "\\ud800"}}}'


@pytest.mark.parametrize(
    "body",
    [
        DEEP_JSON,
        SURROGATE_ID,
        json.dumps({"ocs": {"data": {"id": "a\x00b"}}}).encode(),
        json.dumps({"ocs": {"data": {"id": "x" * 256}}}).encode(),
        json.dumps({"ocs": {"data": {"id": "a\u0085b"}}}).encode(),
        b'{"ocs": {"data": {"id": "\xff\xfe"}}}',
    ],
    ids=["deep-json", "surrogate", "nul", "too-long", "c1-control", "not-utf8"],
)
async def test_cases_4_and_5_account_lookup(make_settings, body: bytes) -> None:
    settings = make_settings(error_codes="true")
    nc = NextcloudClient(settings, transport=httpx.MockTransport(_ocs(body)))
    tools = NextcloudTools(nc, settings)
    message = await _fails(tools, tools.get_file_tree(""))
    assert message.startswith(INVALID)
    assert "did not answer like a Nextcloud server" in message
    with pytest.raises(ToolError) as info:
        await nc.probe()
    assert str(info.value) == message


async def test_valid_unusual_user_id_is_accepted(make_settings) -> None:
    body = json.dumps({"ocs": {"data": {"id": "名字 #1"}}}).encode()
    nc = NextcloudClient(make_settings(), transport=httpx.MockTransport(_ocs(body)))
    assert await nc.probe() == {"ok": True, "user_id": "名字 #1"}


async def test_belt_unexpected_exception_in_tools(coded_tools, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OverflowError("internal detail")

    import nextcloud_mcp_server.tools as tools_module

    monkeypatch.setattr(tools_module, "file_entry", boom)
    message = await _fails(coded_tools, coded_tools.get_file_tree(""))
    assert message == INVALID + "Nextcloud sent an answer that could not be processed."


async def test_belt_without_code_mode(make_settings, fake: FakeNextcloud, monkeypatch) -> None:
    import nextcloud_mcp_server.tools as tools_module

    settings = make_settings(error_codes="false")
    tools = NextcloudTools(NextcloudClient(settings, transport=fake.transport), settings)
    monkeypatch.setattr(tools_module, "file_entry", lambda *a: [][1])
    assert await _fails(tools, tools.get_file_tree("")) == (
        "Nextcloud sent an answer that could not be processed."
    )


async def test_belt_in_probe(make_settings, monkeypatch) -> None:
    import nextcloud_mcp_server.client as client_module

    monkeypatch.setattr(
        client_module.json, "loads", lambda body: (_ for _ in ()).throw(MemoryError())
    )
    nc = NextcloudClient(
        make_settings(error_codes="true"),
        transport=httpx.MockTransport(_ocs(b'{"ocs": {"data": {"id": "x"}}}')),
    )
    with pytest.raises(
        ToolError, match=r"^BACKEND_INVALID_RESPONSE: Nextcloud sent an answer that"
    ):
        await nc.probe()


async def test_cancellation_is_not_swallowed(coded_tools, make_settings) -> None:
    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await coded_tools.run(cancelled())

    class Cancelling(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise asyncio.CancelledError

    nc = NextcloudClient(make_settings(), transport=Cancelling())
    with pytest.raises(asyncio.CancelledError):
        await nc.probe()


def test_constants() -> None:
    assert BASE_URL.endswith("/nc")
    assert USER_ID
