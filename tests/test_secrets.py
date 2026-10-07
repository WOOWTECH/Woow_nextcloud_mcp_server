"""The app password (or the Basic auth header built from it) never leaks."""

from __future__ import annotations

import base64
import logging
import traceback

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from fake_nextcloud import LOGIN, PASSWORD, FakeNextcloud
from nextcloud_mcp_server.server import create_server
from nextcloud_mcp_server.settings import SettingsError, load_settings
from nextcloud_mcp_server.tools import NextcloudTools

TOKEN = base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
SECRETS = (PASSWORD, TOKEN)


def _assert_clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


def _records_text(records: list[logging.LogRecord]) -> str:
    parts = []
    for record in records:
        parts.append(record.getMessage())
        if record.exc_info:
            parts.append("".join(traceback.format_exception(*record.exc_info)))
        if record.exc_text:
            parts.append(record.exc_text)
    return "\n".join(parts)


FAILURES = [
    lambda r: httpx.Response(401, text=f"bad {r.headers.get('Authorization')}"),
    lambda r: httpx.Response(403, text=r.headers.get("Authorization", "")),
    lambda r: httpx.Response(500, text=r.headers.get("Authorization", "")),
    lambda r: httpx.Response(302, headers={"Location": f"https://{LOGIN}:{PASSWORD}@x.example/"}),
    lambda r: (_ for _ in ()).throw(httpx.ConnectError(f"refused {PASSWORD}")),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
    lambda r: httpx.Response(207, content=b"<not-xml " + PASSWORD.encode()),
]


@pytest.mark.parametrize("failure", FAILURES)
async def test_no_secret_in_errors_or_logs(
    tools: NextcloudTools, fake: FakeNextcloud, failure, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    fake.override = failure
    calls = [
        lambda: tools.get_file_tree(""),
        lambda: tools.read_text_file("Docs/readme.md"),
        lambda: tools.create_text_file("x.txt", "x"),
        lambda: tools.update_text_file("Docs/readme.md", "x", "e"),
        lambda: tools.upload_file("x.bin", "AAEC", "e"),
        lambda: tools.delete_file_checked("photo.png", "e"),
        lambda: tools.list_calendars(),
    ]
    for call in calls:
        try:
            await call()
        except ToolError as exc:
            _assert_clean(str(exc))
            _assert_clean("".join(traceback.format_exception(exc)))
    _assert_clean(_records_text(caplog.records))


async def test_no_secret_through_mcp(settings, fake: FakeNextcloud, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    fake.override = FAILURES[0]
    async with Client(create_server(settings, transport=fake.transport)) as client:
        result = await client.call_tool_mcp("read_text_file", {"path": "Docs/readme.md"})
    assert result.isError is True
    assert result.content[0].text.startswith("Nextcloud rejected the username or app password.")
    _assert_clean(str(result))
    _assert_clean(_records_text(caplog.records))


def test_settings_never_show_password(settings) -> None:
    _assert_clean(repr(settings))
    _assert_clean(str(settings))
    _assert_clean(str(settings.model_dump()))


def test_settings_error_never_shows_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXTCLOUD_MCP_BASE_URL", f"https://{LOGIN}:{PASSWORD}@cloud.example.com")
    monkeypatch.setenv("NEXTCLOUD_MCP_USERNAME", LOGIN)
    monkeypatch.setenv("NEXTCLOUD_MCP_APP_PASSWORD", PASSWORD)
    with pytest.raises(SettingsError) as info:
        load_settings()
    _assert_clean(str(info.value))
    _assert_clean("".join(traceback.format_exception(info.value)))
