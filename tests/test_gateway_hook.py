"""Tools through a gateway ``backend_policy`` whose transport raises for non-2xx answers."""

from __future__ import annotations

import base64

import httpx
import pytest
from fastmcp.exceptions import ToolError

from fake_nextcloud import FakeNextcloud
from nextcloud_mcp_server.client import NextcloudClient, carried_status, failure_error
from nextcloud_mcp_server.errors import AUTH_LATCHED_MESSAGE, THROTTLED_MESSAGE
from nextcloud_mcp_server.tools import NextcloudTools
from test_client import (
    BackendDenied,
    BackendFailure,
    BackendHTTPError,
    GatewayTransport,
    realistic_policy,
)


@pytest.fixture
def gateway(fake: FakeNextcloud) -> GatewayTransport:
    return GatewayTransport(fake.transport)


@pytest.fixture
async def hooked(settings, gateway: GatewayTransport):
    nc = NextcloudClient(settings, policy=realistic_policy(gateway, []))
    yield NextcloudTools(nc, settings)
    await nc.aclose()


async def _message(call) -> str:
    with pytest.raises(ToolError) as info:
        await call
    message = str(info.value)
    assert "BACKEND_" not in message  # never echo gateway text
    return message


async def test_reads_work_through_the_gateway(hooked: NextcloudTools) -> None:
    tree = await hooked.get_file_tree("", depth=2)
    assert [e["path"] for e in tree["entries"]][:2] == ["Docs", "Docs/Sub"]
    assert (await hooked.read_text_file("Docs/readme.md"))["content"] == "# Hello\n"


async def test_create_existing_is_already_exists(hooked: NextcloudTools) -> None:
    message = await _message(hooked.create_text_file("Docs/readme.md", "x"))
    assert message == (
        '"Docs/readme.md" already exists; read it first and use update_text_file with its etag.'
    )


async def test_upload_create_existing(hooked: NextcloudTools) -> None:
    message = await _message(hooked.upload_file("photo.png", "AAEC"))
    assert message.startswith('"photo.png" already exists; get its etag')


async def test_update_stale_reports_current_etag(hooked: NextcloudTools, fake) -> None:
    current = fake.files["Docs/readme.md"].etag
    message = await _message(hooked.update_text_file("Docs/readme.md", "x", "stale"))
    assert message == (
        f'"Docs/readme.md" changed since it was read (current etag {current}); '
        "read it again and retry."
    )


async def test_update_missing_file(hooked: NextcloudTools) -> None:
    message = await _message(hooked.update_text_file("Docs/none.md", "x", "e"))
    assert message == '"Docs/none.md" does not exist; use create_text_file to create it.'


async def test_create_with_missing_parent(hooked: NextcloudTools) -> None:
    message = await _message(hooked.create_text_file("No/x.txt", "x"))
    assert message == 'The parent folder of "No/x.txt" does not exist.'


async def test_locked_file(hooked: NextcloudTools, fake: FakeNextcloud) -> None:
    fake.override = lambda r: httpx.Response(423) if r.method == "PUT" else None
    etag = fake.files["Docs/readme.md"].etag
    message = await _message(hooked.update_text_file("Docs/readme.md", "x", etag))
    assert message.startswith('"Docs/readme.md" is locked')


async def test_delete_checks(hooked: NextcloudTools, fake: FakeNextcloud) -> None:
    message = await _message(hooked.delete_file_checked("Docs", "folder-4"))
    assert message == '"Docs" is a folder; this tool deletes single files only.'
    assert await _message(hooked.delete_file_checked("gone.txt", "e")) == (
        '"gone.txt" does not exist.'
    )
    etag = fake.files["photo.png"].etag
    assert (await hooked.delete_file_checked("photo.png", etag))["status"] == "deleted"


async def test_read_missing(hooked: NextcloudTools) -> None:
    assert await _message(hooked.read_text_file("nope.txt")) == '"nope.txt" does not exist.'


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AUTH_LATCHED_MESSAGE),
        (429, THROTTLED_MESSAGE),
        (403, 'Permission denied for "Docs/readme.md".'),
        (500, "Nextcloud returned an error (500); try again later."),
        (507, 'Not enough storage or file too large for "Docs/readme.md".'),
        (
            302,
            "Nextcloud answered with a redirect to an unknown address; "
            "set NEXTCLOUD_MCP_BASE_URL to the final address.",
        ),
    ],
)
async def test_status_mapping(hooked: NextcloudTools, fake, status: int, expected: str) -> None:
    fake.override = lambda r: httpx.Response(status) if r.method == "PUT" else None
    etag = fake.files["Docs/readme.md"].etag
    assert await _message(hooked.update_text_file("Docs/readme.md", "x", etag)) == expected


async def test_denied_path(hooked: NextcloudTools, fake: FakeNextcloud) -> None:
    fake.add_file("100% done.txt", b"x")
    message = await _message(hooked.read_text_file("100% done.txt"))
    assert message == (
        'The gateway refused this request for "100% done.txt" (destination or path not allowed).'
    )
    message = await _message(hooked.upload_file("50%.bin", base64.b64encode(b"x").decode()))
    assert message.startswith('The gateway refused this request for "50%.bin"')


async def test_busy(hooked: NextcloudTools, gateway: GatewayTransport) -> None:
    gateway.busy = True
    assert await _message(hooked.get_file_tree("")) == "Nextcloud is busy; try again later."


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (BackendFailure("BACKEND_DESTINATION_DENIED"), "The gateway refused this request"),
        (BackendFailure("BACKEND_BUSY"), "Nextcloud is busy; try again later."),
        (BackendFailure("BACKEND_TIMEOUT"), "Could not reach Nextcloud (timed out)."),
        (BackendFailure("BACKEND_UNAVAILABLE"), "Could not reach Nextcloud (connection failed)."),
        (BackendFailure("BACKEND_STREAM_ERROR"), "Nextcloud returned an error (BackendFailure)"),
        (BackendDenied(), "The gateway refused this request"),
        (ValueError("BACKEND_DESTINATION_DENIED"), "The gateway refused this request"),
        (httpx.HTTPError("BACKEND_BUSY"), "Nextcloud is busy"),
        (ValueError("secret backend text"), "Nextcloud returned an error (ValueError)"),
        (RuntimeError("secret backend text"), "Nextcloud returned an error (RuntimeError)"),
        (httpx.ConnectError("x"), "Could not reach Nextcloud (connection failed)."),
    ],
)
def test_failure_error(exc: Exception, expected: str) -> None:
    message = str(failure_error(exc, "p"))
    assert message.startswith(expected)
    assert "secret" not in message
    assert "BACKEND_" not in message


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (BackendHTTPError(412), 412),
        (BackendFailure("BACKEND_HTTP_ERROR status=423"), 423),
        (BackendFailure("BACKEND_HTTP_ERROR status=abc"), None),
        (BackendFailure("BACKEND_BUSY"), None),
        (httpx.ConnectError("x"), None),
    ],
)
def test_carried_status(exc: Exception, status: int | None) -> None:
    assert carried_status(exc) == status


def test_carried_status_rejects_odd_values() -> None:
    odd = ValueError("x")
    odd.status = True  # type: ignore[attr-defined]
    assert carried_status(odd) is None
    odd.status = 99  # type: ignore[attr-defined]
    assert carried_status(odd) is None
    odd.status = "404"  # type: ignore[attr-defined]
    assert carried_status(odd) is None


async def test_status_while_streaming_body(settings) -> None:
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x"
            raise BackendFailure("BACKEND_TIMEOUT")

    nc = NextcloudClient(
        settings, transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Broken()))
    )
    with pytest.raises(ToolError, match=r"^Could not reach Nextcloud \(timed out\)\.$"):
        await nc.send("GET", "https://cloud.example.com/nc/x", label="x", op="read")


async def test_build_request_failure_is_a_tool_error(settings, monkeypatch) -> None:
    nc = NextcloudClient(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    http = await nc.http()

    def boom(*args, **kwargs):
        raise BackendDenied()

    monkeypatch.setattr(http, "build_request", boom)
    with pytest.raises(ToolError, match="The gateway refused this request"):
        await nc.send("GET", "https://cloud.example.com/nc/x", label="x", op="read")
    await nc.aclose()
