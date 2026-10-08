"""Code mode: public error codes prefixed to backend-caused errors (and nothing else)."""

from __future__ import annotations

import sys
import types
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import nextcloud_mcp_server.client as client_module
from fake_nextcloud import FakeNextcloud
from nextcloud_mcp_server.client import NextcloudClient, reset_auth_latch
from nextcloud_mcp_server.server import create_server
from nextcloud_mcp_server.tools import NextcloudTools
from test_client import GatewayTransport, realistic_policy

Scenario = Callable[[NextcloudTools, FakeNextcloud, pytest.MonkeyPatch], Awaitable[Any]]


def _fake() -> FakeNextcloud:
    fake = FakeNextcloud()
    fake.add_folder("Docs")
    fake.add_file("Docs/readme.md", b"# Hello\n", "text/markdown")
    fake.add_file("photo.png", b"\x89PNG", "image/png")
    fake.add_file("big.txt", b"x" * 100)
    return fake


def _put(status: int):
    return lambda r: httpx.Response(status) if r.method == "PUT" else None


async def s_create_existing(t, f, m):
    return await t.create_text_file("Docs/readme.md", "x")


async def s_upload_existing(t, f, m):
    return await t.upload_file("photo.png", "AAEC")


async def s_update_stale(t, f, m):
    return await t.update_text_file("Docs/readme.md", "x", "stale")


async def s_update_missing(t, f, m):
    return await t.update_text_file("Docs/none.md", "x", "e")


async def s_read_missing(t, f, m):
    return await t.read_text_file("none.txt")


async def s_403(t, f, m):
    f.override = _put(403)
    return await t.create_text_file("new.txt", "x")


async def s_405(t, f, m):
    return await t.create_text_file("Docs", "x")


async def s_409(t, f, m):
    f.override = _put(409)
    return await t.create_text_file("new.txt", "x")


async def s_404_parent(t, f, m):
    return await t.create_text_file("No/new.txt", "x")


async def s_423(t, f, m):
    f.override = _put(423)
    return await t.update_text_file("Docs/readme.md", "x", f.files["Docs/readme.md"].etag)


async def s_413(t, f, m):
    f.override = _put(413)
    return await t.upload_file("new.bin", "AAEC")


async def s_507(t, f, m):
    f.override = _put(507)
    return await t.upload_file("new.bin", "AAEC")


async def s_500(t, f, m):
    f.override = lambda r: httpx.Response(500)
    return await t.get_file_tree("")


async def s_no_calendar_home(t, f, m):
    f.override = lambda r: httpx.Response(404) if "/calendars/" in str(r.url) else None
    return await t.list_calendars()


async def s_redirect(t, f, m):
    f.override = lambda r: httpx.Response(302, headers={"Location": "https://x.example/a"})
    return await t.get_file_tree("")


async def s_listing_too_large(t, f, m):
    m.setattr(client_module, "MAX_XML_BYTES", 10)
    return await t.get_file_tree("")


async def s_text_too_large(t, f, m):
    t.settings.max_text_bytes = 10
    return await t.read_text_file("big.txt")


async def s_invalid_xml(t, f, m):
    f.override = lambda r: httpx.Response(207, content=b"<oops")
    return await t.get_file_tree("")


async def s_foreign_hrefs(t, f, m):
    xml = (
        b'<d:multistatus xmlns:d="DAV:">'
        b"<d:response><d:href>/x/a</d:href><d:propstat><d:prop><d:resourcetype/></d:prop>"
        b"<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        b"<d:response><d:href>/x/b</d:href><d:propstat><d:prop><d:resourcetype/></d:prop>"
        b"<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
    )
    f.override = lambda r: httpx.Response(207, content=xml)
    return await t.get_file_tree("")


def _raise(exc: Exception):
    def override(request: httpx.Request) -> httpx.Response:
        raise exc

    return override


async def s_timeout(t, f, m):
    f.override = _raise(httpx.ReadTimeout("slow"))
    return await t.get_file_tree("")


async def s_unavailable(t, f, m):
    f.override = _raise(httpx.ConnectError("refused"))
    return await t.get_file_tree("")


async def s_undecodable(t, f, m):
    f.override = _raise(httpx.DecodingError("bad"))
    return await t.get_file_tree("")


async def s_latch_401(t, f, m):
    f.override = lambda r: httpx.Response(401)
    return await t.get_file_tree("")


async def s_latch_429(t, f, m):
    f.override = lambda r: httpx.Response(429)
    return await t.get_file_tree("")


async def s_local_etag_mismatch(t, f, m):
    return await t.delete_file_checked("photo.png", "not-the-etag")


async def s_bad_path(t, f, m):
    return await t.read_text_file("../x")


async def s_bad_base64(t, f, m):
    return await t.upload_file("x.bin", "!!!")


async def s_content_too_large(t, f, m):
    t.settings.max_text_bytes = 2
    return await t.create_text_file("x.txt", "hello")


async def s_folder_refusal(t, f, m):
    return await t.delete_file_checked("Docs", "e")


SCENARIOS: list[tuple[str, Scenario, str | None]] = [
    ("create existing (412)", s_create_existing, "BACKEND_HTTP_ERROR status=412"),
    ("upload existing (412)", s_upload_existing, "BACKEND_HTTP_ERROR status=412"),
    ("update stale (412)", s_update_stale, "BACKEND_HTTP_ERROR status=412"),
    ("update missing (412 then 404)", s_update_missing, "BACKEND_HTTP_ERROR status=404"),
    ("read missing (404)", s_read_missing, "BACKEND_HTTP_ERROR status=404"),
    ("403", s_403, "BACKEND_HTTP_ERROR status=403"),
    ("405", s_405, "BACKEND_HTTP_ERROR status=405"),
    ("409", s_409, "BACKEND_HTTP_ERROR status=409"),
    ("404 parent", s_404_parent, "BACKEND_HTTP_ERROR status=404"),
    ("423", s_423, "BACKEND_HTTP_ERROR status=423"),
    ("413", s_413, "BACKEND_HTTP_ERROR status=413"),
    ("507", s_507, "BACKEND_HTTP_ERROR status=507"),
    ("500", s_500, "BACKEND_HTTP_ERROR status=500"),
    ("no calendar home", s_no_calendar_home, "BACKEND_HTTP_ERROR status=404"),
    ("redirect", s_redirect, "BACKEND_HTTP_ERROR status=302"),
    ("listing too large", s_listing_too_large, "BACKEND_INVALID_RESPONSE"),
    ("text file too large", s_text_too_large, "BACKEND_INVALID_RESPONSE"),
    ("invalid XML", s_invalid_xml, "BACKEND_INVALID_RESPONSE"),
    ("webroot mismatch", s_foreign_hrefs, "BACKEND_INVALID_RESPONSE"),
    ("timeout", s_timeout, "BACKEND_TIMEOUT"),
    ("connection failed", s_unavailable, "BACKEND_UNAVAILABLE"),
    ("undecodable answer", s_undecodable, "BACKEND_INVALID_RESPONSE"),
    ("latched 401", s_latch_401, "BACKEND_HTTP_ERROR status=401"),
    ("latched 429", s_latch_429, "BACKEND_HTTP_ERROR status=429"),
    ("local etag pre-check", s_local_etag_mismatch, "ETAG_MISMATCH"),
    ("bad path", s_bad_path, None),
    ("bad base64", s_bad_base64, None),
    ("content too large", s_content_too_large, None),
    ("folder refusal", s_folder_refusal, None),
]


async def _message(make_settings, monkeypatch, scenario: Scenario, codes: str) -> str:
    reset_auth_latch()
    fake = _fake()
    settings = make_settings(readonly=False, allow_delete=True, error_codes=codes)
    nc = NextcloudClient(settings, transport=fake.transport)
    tools = NextcloudTools(nc, settings)
    await nc.user_id()
    try:
        with pytest.raises(ToolError) as info:
            await tools.run(scenario(tools, fake, monkeypatch))
    finally:
        await nc.aclose()
    message = str(info.value)
    assert "BACKEND_HTTP_ERROR status=" not in message.split(": ", 1)[-1]
    return message


@pytest.mark.parametrize(("name", "scenario", "code"), SCENARIOS, ids=[s[0] for s in SCENARIOS])
async def test_error_codes(make_settings, monkeypatch, name, scenario, code) -> None:
    plain = await _message(make_settings, monkeypatch, scenario, "false")
    with_codes = await _message(make_settings, monkeypatch, scenario, "true")
    assert not plain.startswith(("BACKEND_", "ETAG_MISMATCH"))
    if code is None:
        assert with_codes == plain
    else:
        assert with_codes == f"{code}: {plain}"


async def test_account_lookup_codes(make_settings) -> None:
    cases = [
        (httpx.Response(200, text="<html>"), "BACKEND_INVALID_RESPONSE"),
        (httpx.Response(200, content=b"x" * (1024 * 1024 + 1)), "BACKEND_INVALID_RESPONSE"),
        (httpx.Response(404), "BACKEND_HTTP_ERROR status=404"),
    ]
    for response, code in cases:
        nc = NextcloudClient(
            make_settings(error_codes="true"),
            transport=httpx.MockTransport(lambda r, response=response: response),
        )
        with pytest.raises(ToolError, match=f"^{code}: ") as info:
            await nc.probe()
        assert "BACKEND" not in str(info.value).split(": ", 1)[1]


def test_code_mode_selection(make_settings, fake: FakeNextcloud) -> None:
    hook = realistic_policy(fake.transport, [])
    assert NextcloudClient(make_settings()).code_mode is False
    assert NextcloudClient(make_settings(), policy=hook).code_mode is True
    assert NextcloudClient(make_settings(error_codes="false"), policy=hook).code_mode is False
    assert NextcloudClient(make_settings(error_codes="true")).code_mode is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [("auto", "auto"), ("TRUE", "true"), ("1", "true"), ("off", "false"), (" no ", "false")],
)
def test_error_codes_setting(make_settings, value, expected) -> None:
    assert make_settings(error_codes=value).error_codes == expected


def test_error_codes_setting_rejects_garbage(make_settings) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_settings(error_codes="sometimes")


class StreamError(Exception):
    pass


async def test_hook_public_backend_error_is_used(
    settings, fake: FakeNextcloud, monkeypatch
) -> None:
    gateway = GatewayTransport(fake.transport)
    module = types.ModuleType("backend_policy")
    module.async_client = realistic_policy(gateway, [])  # type: ignore[attr-defined]

    def public_backend_error(exc: BaseException) -> str:
        if isinstance(exc, StreamError):
            return "BACKEND_STREAM_ERROR"
        if type(exc).__name__ == "BackendDenied":
            return "BACKEND_DESTINATION_DENIED"
        return "not a valid code!"

    module.public_backend_error = public_backend_error  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend_policy", module)
    nc = NextcloudClient(settings)
    assert nc.code_mode is True  # auto: the hook is in use
    tools = NextcloudTools(nc, settings)
    await nc.user_id()

    fake.override = _raise(StreamError("secret text"))
    with pytest.raises(ToolError) as info:
        await tools.run(tools.get_file_tree(""))
    assert str(info.value) == (
        "BACKEND_STREAM_ERROR: Nextcloud returned an error (StreamError); try again later."
    )
    fake.override = None
    fake.add_file("50% off.txt", b"x")
    with pytest.raises(ToolError, match=r"^BACKEND_DESTINATION_DENIED: The gateway refused"):
        await tools.run(tools.read_text_file("50% off.txt"))
    fake.override = _raise(httpx.ConnectError("x"))
    with pytest.raises(ToolError, match=r"^BACKEND_UNAVAILABLE: "):  # invalid hook code ignored
        await tools.run(tools.get_file_tree(""))
    await nc.aclose()


async def test_hook_public_error_that_raises_is_ignored(settings, fake: FakeNextcloud) -> None:
    def broken(exc: BaseException) -> str:
        raise RuntimeError("boom")

    gateway = GatewayTransport(fake.transport)
    nc = NextcloudClient(settings, policy=realistic_policy(gateway, []), public_error=broken)
    tools = NextcloudTools(nc, settings)
    gateway.busy = True
    with pytest.raises(ToolError, match=r"^BACKEND_BUSY: Nextcloud is busy"):
        await tools.run(tools.get_file_tree(""))


async def test_unsafe_server_etag_is_not_echoed(make_settings, fake: FakeNextcloud) -> None:
    settings = make_settings(readonly=False, error_codes="true")
    nc = NextcloudClient(settings, transport=fake.transport)
    tools = NextcloudTools(nc, settings)
    for bad in ("café", "x" * 300, 'a"b'):
        fake.files["Docs/readme.md"] = type(fake.files["photo.png"])(b"x", bad)
        with pytest.raises(ToolError) as info:
            await tools.run(tools.update_text_file("Docs/readme.md", "y", "old"))
        assert str(info.value) == (
            'BACKEND_HTTP_ERROR status=412: "Docs/readme.md" changed since it was read '
            "(current etag unknown); read it again and retry."
        )


async def test_code_mode_through_mcp_with_hook(settings, fake: FakeNextcloud, monkeypatch) -> None:
    module = types.ModuleType("backend_policy")
    module.async_client = realistic_policy(GatewayTransport(fake.transport), [])  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend_policy", module)
    async with Client(create_server(settings)) as client:
        result = await client.call_tool_mcp(
            "create_text_file", {"path": "Docs/readme.md", "content": "x"}
        )
        assert result.isError is True
        assert result.content[0].text.startswith("BACKEND_HTTP_ERROR status=412: ")
        bad = await client.call_tool_mcp("read_text_file", {"path": "../x"})
        assert not bad.content[0].text.startswith("BACKEND_")
