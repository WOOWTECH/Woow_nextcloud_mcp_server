"""Known credentials are masked before Sabre diagnostics are folded or capped."""

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
from nextcloud_mcp_server.webdav import server_message

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
    text = await response_text(make_settings, PASSWORD, f"storage refused; {variants[form]}; retry")
    assert text == PREFIX + "storage refused; [REDACTED]; retry."


@pytest.mark.parametrize("case", ["whitespace", "cut"])
async def test_mask_precedes_whitespace_and_200_character_cap(make_settings, case) -> None:
    password = "FAKE-paas-pass  spaced-4917" if case == "whitespace" else PASSWORD
    prefix = "storage refused; " if case == "whitespace" else "x" * 184
    text = await response_text(make_settings, password, prefix + password + "; retry")
    assert text == PREFIX + (prefix + "[REDACTED]; retry")[:200] + "."
    assert password[:16] not in text


async def test_existing_500_and_benign_detail_contract(make_settings) -> None:
    assert await response_text(make_settings, PASSWORD, PASSWORD, 500) == (
        "Nextcloud returned an error (500); try again later."
    )
    assert await response_text(make_settings, PASSWORD, "SAFE \n " * 60) == (
        PREFIX + ("SAFE " * 60).strip()[:200] + "."
    )


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


def test_optional_credentials_and_single_pass_replacement() -> None:
    assert server_message(sabre("normal diagnostic")) == "normal diagnostic"
    assert server_message(sabre("normal diagnostic"), credentials=("",)) == "normal diagnostic"
    assert server_message(sabre("long-token token"), credentials=("token", "long-token")) == (
        "[REDACTED] [REDACTED]"
    )
    assert server_message(sabre("REDACTED"), credentials=("REDACTED",)) == "[REDACTED]"
