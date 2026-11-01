"""Backend diagnostics never reflect known or unknown credentials."""

from __future__ import annotations

import base64
import json
from urllib.parse import quote
from xml.sax.saxutils import escape

import httpx
import pytest

from fake_nextcloud import BASE_URL, LOGIN
from nextcloud_mcp_server.client import NextcloudClient
from nextcloud_mcp_server.errors import NextcloudHTTPError
from nextcloud_mcp_server.webdav import safe_server_diagnostic

PASSWORD = 'FAKE-paas-pass/+"quoted-4917ÿ'
PREFIX = 'Nextcloud refused the request for "x" (422): '


def sabre(message: str) -> bytes:
    return (
        '<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
        f"<s:message>{escape(message)}</s:message></d:error>"
    ).encode()


async def response_text(make_settings, password: str, detail: str, status: int = 422) -> str:
    settings = make_settings(app_password=password)
    client = NextcloudClient(
        settings,
        transport=httpx.MockTransport(lambda _: httpx.Response(status, content=sabre(detail))),
    )
    try:
        with pytest.raises(NextcloudHTTPError) as error:
            await client.send("PROPFIND", BASE_URL + "/dav/x", label="x", op="read")
        assert error.value.status == status
        return str(error.value)
    finally:
        await client.aclose()


@pytest.mark.parametrize("with_username", [False, True], ids=["password", "basic-pair"])
@pytest.mark.parametrize(
    "form", ["raw", "url", "json", "json-unicode", "base64", "base64url", "unpadded"]
)
async def test_422_masks_known_credentials(make_settings, with_username, form) -> None:
    credential = f"{LOGIN}:{PASSWORD}" if with_username else PASSWORD
    variants = {
        "raw": credential,
        "url": quote(credential, safe=""),
        "json": json.dumps(credential)[1:-1],
        "json-unicode": json.dumps(credential, ensure_ascii=False)[1:-1],
        "base64": base64.b64encode(credential.encode()).decode(),
        "base64url": base64.urlsafe_b64encode(credential.encode()).decode(),
        "unpadded": base64.urlsafe_b64encode(credential.encode()).decode().rstrip("="),
    }
    text = await response_text(make_settings, PASSWORD, f"quota exhausted; {variants[form]}; retry")
    assert text == PREFIX + "storage quota exhausted."
    assert credential not in text and variants[form] not in text


@pytest.mark.parametrize("case", ["whitespace", "cut"])
async def test_long_or_folded_secret_is_not_reflected(make_settings, case) -> None:
    password = "FAKE-paas-pass  spaced-4917" if case == "whitespace" else PASSWORD
    prefix = "quota exhausted; " if case == "whitespace" else "quota exhausted; " + "x" * 184
    text = await response_text(make_settings, password, prefix + password + "; retry")
    assert text == PREFIX + "storage quota exhausted."
    assert password[:16] not in text


async def test_5xx_and_unrecognized_detail_contract(make_settings) -> None:
    assert await response_text(make_settings, PASSWORD, PASSWORD, 500) == (
        "Nextcloud returned an error (500); try again later."
    )
    assert await response_text(make_settings, PASSWORD, "quota exhausted", 500) == (
        "Nextcloud returned an error (500); try again later. "
        "Backend diagnostic: storage quota exhausted."
    )
    assert await response_text(make_settings, PASSWORD, "SAFE \n " * 60) == PREFIX[:-2] + "."


async def test_successful_file_content_is_not_masked(make_settings) -> None:
    payload = PASSWORD.encode()
    client = NextcloudClient(
        make_settings(app_password=PASSWORD),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload)),
    )
    try:
        reply = await client.send("GET", BASE_URL + "/dav/x", label="x", op="read")
        assert reply.body == payload
    finally:
        await client.aclose()


def test_unknown_backend_text_is_not_exposed() -> None:
    assert safe_server_diagnostic(sabre("normal diagnostic")) is None
    assert safe_server_diagnostic(sabre("long-token token")) is None
    assert safe_server_diagnostic(sabre("REDACTED")) is None
