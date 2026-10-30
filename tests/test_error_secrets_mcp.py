"""Echoed credentials in Nextcloud's error detail, through the real server over HTTP.

The PaaS (woow-mcp-server) and HA add-on suites send seven credential forms through their
real child processes: raw, Basic (base64 of ``user:password``), URL-quoted, JSON-escaped,
base64, a password with repeated whitespace (``folded``) and one that crosses the
length cap (``cut``). Here the same forms, plus every other form the masking knows, are
echoed by an in-memory Nextcloud in a 422 Sabre ``<s:message>`` and read back through
the Streamable HTTP transport in both answer modes (SSE and JSON), with and without code
mode. The other statuses and body shapes of the HA suite (403, 500; JSON, OCS and plain
text bodies) are reported by this server without the backend's text, which is checked
too. The cap is 200 characters here (the HA add-on raises it to 1000).
"""

from __future__ import annotations

import base64
import itertools
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape

import httpx
import pytest

from fake_nextcloud import LOGIN, FakeNextcloud
from nextcloud_mcp_server.server import create_server, http_guard
from nextcloud_mcp_server.settings import Settings

PASSWORD = 'FAKE-v016-pass/+"quoted-4917ÿ~'
SPACED = "FAKE-v016-pass  spaced-4917ÿ~"
DIAGNOSTIC = "FAKE storage temporarily unavailable"
PREFIX = 'Nextcloud refused the request for "x" (422): '
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "secrets-test", "version": "1"},
    },
}


def b64(value: str, *, urlsafe: bool = False, padded: bool = True) -> str:
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    text = encode(value.encode()).decode()
    return text if padded else text.rstrip("=")


def json_escaped(value: str, *, ascii_only: bool = True) -> str:
    return json.dumps(value, ensure_ascii=ascii_only)[1:-1]


FORMS: dict[str, Callable[[str], str]] = {
    # The seven forms of the PaaS and HA suites.
    "raw": lambda p: p,
    "basic": lambda p: b64(f"{LOGIN}:{p}"),
    "url": lambda p: quote(p, safe=""),
    "json": json_escaped,
    "base64": b64,
    "folded": lambda p: " ".join(p.split()),
    "cut": lambda p: p[:16],
    # The other forms that are masked.
    "json-unicode": lambda p: json_escaped(p, ascii_only=False),
    "base64-unpadded": lambda p: b64(p, padded=False),
    "base64url": lambda p: b64(p, urlsafe=True),
    "base64url-unpadded": lambda p: b64(p, urlsafe=True, padded=False),
    "basic-url": lambda p: quote(f"{LOGIN}:{p}", safe=""),
    "basic-base64url-unpadded": lambda p: b64(f"{LOGIN}:{p}", urlsafe=True, padded=False),
}


def error_body(shape: str, detail: str) -> bytes:
    if shape == "sabre":
        return (
            '<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
            "<s:exception>Sabre\\DAV\\Exception</s:exception>"
            f"<s:message>{escape(detail)}</s:message></d:error>"
        ).encode()
    if shape == "ocs":
        return json.dumps({"ocs": {"meta": {"status": "failure", "message": detail}}}).encode()
    if shape == "json":
        return json.dumps({"message": detail}).encode()
    return detail.encode()


Call = Callable[[str, dict[str, object]], Awaitable[str]]


@asynccontextmanager
async def mcp_session(
    settings: Settings, fake: FakeNextcloud, *, json_response: bool
) -> AsyncIterator[Call]:
    """An initialized Streamable HTTP session; ``call`` returns an error result's text."""
    server = create_server(settings, transport=fake.transport)
    app = server.http_app(
        path="/mcp", json_response=json_response, **http_guard(settings, "127.0.0.1")
    )
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:3000") as http,
    ):
        init = await http.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE)
        assert init.status_code == 200
        headers = {**MCP_HEADERS, "Mcp-Session-Id": init.headers["mcp-session-id"]}
        initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert (await http.post("/mcp", headers=headers, json=initialized)).status_code == 202
        ids = itertools.count(2)

        async def call(name: str, arguments: dict[str, object]) -> str:
            request = {
                "jsonrpc": "2.0",
                "id": next(ids),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
            response = await http.post("/mcp", headers=headers, json=request)
            assert response.status_code == 200
            kind = response.headers["content-type"]
            if json_response:
                assert kind.startswith("application/json")
                result = response.json()["result"]
            else:
                assert kind.startswith("text/event-stream")
                result = next(
                    json.loads(line[len("data: ") :])["result"]
                    for line in response.text.splitlines()
                    if line.startswith("data: ") and '"result"' in line
                )
            assert result["isError"] is True
            return "\n".join(block.get("text", "") for block in result["content"])

        yield call


@pytest.mark.parametrize("code_mode", [False, True], ids=["plain", "code-mode"])
@pytest.mark.parametrize("json_response", [False, True], ids=["sse", "json"])
@pytest.mark.parametrize("form", list(FORMS))
async def test_echoed_credential_is_masked_in_tool_errors(
    make_settings,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    form: str,
    json_response: bool,
    code_mode: bool,
) -> None:
    caplog.set_level(logging.DEBUG)
    password = SPACED if form == "folded" else PASSWORD
    exposed = FORMS[form](password)
    # The folded and cut forms are what formatting would leave of the raw password.
    original = password if form in ("folded", "cut") else exposed
    detail = f"{DIAGNOSTIC}; credential echo {original}; end diagnostic"
    if form == "cut":
        detail = DIAGNOSTIC + "x" * (184 - len(DIAGNOSTIC)) + password
    mode: dict[str, Any] = {"status": 422, "shape": "sabre", "detail": detail}

    fake = FakeNextcloud()

    def fail(request: httpx.Request) -> httpx.Response | None:
        if request.method != "PROPFIND":
            return None  # the account lookup succeeds
        body = error_body(mode["shape"], mode["detail"])
        return httpx.Response(
            mode["status"], content=body, headers={"Content-Type": "application/xml"}
        )

    fake.override = fail
    settings = make_settings(app_password=password, error_codes=str(code_mode).lower())

    def coded(status: int, text: str) -> str:
        return f"BACKEND_HTTP_ERROR status={status}: {text}" if code_mode else text

    masked = " ".join(detail.replace(original, "[REDACTED]").split())[:200]
    refused = 'Nextcloud refused the request for "x" (422).'
    calls = [
        ((422, "sabre", detail), coded(422, PREFIX + masked + ".")),
        ((403, "sabre", detail), coded(403, 'Permission denied for "x".')),
        ((500, "sabre", detail), coded(500, "Nextcloud returned an error (500); try again later.")),
        ((422, "json", detail), coded(422, refused)),
        ((422, "ocs", detail), coded(422, refused)),
        ((422, "text", detail), coded(422, refused)),
        ((422, "sabre", "SAFE \n " * 60), coded(422, PREFIX + ("SAFE " * 60).strip()[:200] + ".")),
    ]
    texts = []
    async with mcp_session(settings, fake, json_response=json_response) as call:
        for (status, shape, text), _ in calls:
            mode.update(status=status, shape=shape, detail=text)
            texts.append(await call("get_file_tree", {"path": "x"}))

    assert texts == [want for _, want in calls]
    assert DIAGNOSTIC in texts[0]
    if form != "cut":
        assert "end diagnostic" in texts[0]
    captured = capfd.readouterr()
    output = "\n".join([*texts, caplog.text, captured.out, captured.err])
    assert password not in output
    assert exposed not in output
    assert fake.requests
    assert {request.method for request in fake.requests} <= {"GET", "PROPFIND"}
    basic = "Basic " + b64(f"{LOGIN}:{password}")
    assert all(request.headers["authorization"] == basic for request in fake.requests)


def test_forms_are_distinct() -> None:
    """Each case tests its own string: no two forms of the test password coincide."""
    values = [FORMS[form](PASSWORD) for form in FORMS if form not in ("folded", "cut")]
    assert len(set(values)) == len(values)
    assert FORMS["folded"](SPACED) != SPACED
