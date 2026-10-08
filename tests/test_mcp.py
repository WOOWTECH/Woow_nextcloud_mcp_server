"""MCP-level tests: the FastMCP server in-process, driven through an MCP client."""

from __future__ import annotations

import base64
import logging

import pytest
from fastmcp import Client

from fake_nextcloud import FakeNextcloud
from nextcloud_mcp_server import __version__
from nextcloud_mcp_server.client import NextcloudClient
from nextcloud_mcp_server.server import INSTRUCTIONS, SERVER_NAME, create_server
from nextcloud_mcp_server.tools import ALL_TOOLS, READ_TOOLS, WRITE_TOOLS

COMBINATORS = ("oneOf", "anyOf", "allOf", "not", "$ref")


async def _tool_names(settings, fake) -> list[str]:
    async with Client(create_server(settings, transport=fake.transport)) as client:
        return [tool.name for tool in await client.list_tools()]


async def test_tools_list_schemas(settings, fake: FakeNextcloud) -> None:
    server = create_server(settings, transport=fake.transport)
    async with Client(server) as client:
        tools = await client.list_tools()
    assert [t.name for t in tools] == list(ALL_TOOLS)
    for tool in tools:
        schema = tool.inputSchema
        assert schema["type"] == "object", tool.name
        assert schema["additionalProperties"] is False, tool.name
        for key in COMBINATORS:
            assert key not in schema, (tool.name, key)
        assert "$defs" not in schema
        for name, prop in schema["properties"].items():
            assert prop.get("description"), (tool.name, name)
        assert tool.description and len(tool.description) > 80
        assert tool.annotations is not None
        if tool.name in READ_TOOLS:
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False
        else:
            assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is (
            tool.name in ("update_text_file", "upload_file", "delete_file_checked")
        )
        assert tool.annotations.idempotentHint is True
        if tool.name == "get_file_content":
            assert tool.outputSchema is None
        else:
            assert tool.outputSchema is not None
            assert tool.outputSchema["type"] == "object"

    by_name = {t.name: t.inputSchema for t in tools}
    assert by_name["list_calendars"]["properties"] == {}
    assert by_name["get_file_tree"]["properties"]["depth"]["minimum"] == 1
    assert by_name["get_file_tree"]["properties"]["depth"]["maximum"] == 3
    assert by_name["list_tasks"]["properties"]["limit"]["maximum"] == 500
    assert set(by_name["update_text_file"]["required"]) == {"path", "content", "expected_etag"}
    assert set(by_name["delete_file_checked"]["required"]) == {"path", "expected_etag"}
    assert set(by_name["upload_file"]["required"]) == {"path", "content_base64"}
    assert by_name["update_text_file"]["properties"]["expected_etag"]["minLength"] == 1


async def test_server_info_and_instructions(settings, fake: FakeNextcloud) -> None:
    async with Client(create_server(settings, transport=fake.transport)) as client:
        info = client.initialize_result
    assert info.serverInfo.name == SERVER_NAME
    assert info.serverInfo.version == __version__
    assert info.instructions == INSTRUCTIONS
    lines = INSTRUCTIONS.splitlines()
    assert len(lines) <= 12
    text = INSTRUCTIONS.lower()
    for phrase in (
        "administrator",
        "relative",
        "etag",
        "never retry blindly",
        "trash bin",
        "binary",
    ):
        assert phrase in text


async def test_no_request_at_start_up(settings, fake: FakeNextcloud) -> None:
    async with Client(create_server(settings, transport=fake.transport)) as client:
        await client.list_tools()
    assert fake.requests == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, list(READ_TOOLS)),
        ({"allow_delete": True}, list(READ_TOOLS)),
        ({"readonly": False}, list(READ_TOOLS + WRITE_TOOLS)),
        ({"readonly": False, "allow_delete": True}, list(ALL_TOOLS)),
        (
            {"readonly": False, "allow_delete": True, "disabled_tools": "upload_file,list_tasks"},
            [n for n in ALL_TOOLS if n not in ("upload_file", "list_tasks")],
        ),
        ({"disabled_tools": ",".join(READ_TOOLS)}, []),
    ],
)
async def test_gating(make_settings, fake: FakeNextcloud, overrides, expected) -> None:
    assert await _tool_names(make_settings(**overrides), fake) == expected


def test_unknown_disabled_tool_is_a_settings_error(make_settings) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="unknown tool name"):
        make_settings(disabled_tools="nope,get_file_tree")


async def test_backend_policy_logged_at_start(
    settings, fake: FakeNextcloud, monkeypatch, caplog
) -> None:
    import sys
    import types

    module = types.ModuleType("backend_policy")
    module.async_client = lambda **kw: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend_policy", module)
    caplog.set_level(logging.INFO, logger="nextcloud_mcp_server")
    create_server(settings, transport=fake.transport)
    assert [r.getMessage() for r in caplog.records].count(
        "backend_policy.async_client will build the HTTP client"
    ) == 1


async def test_startup_warnings(make_settings, fake: FakeNextcloud, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="nextcloud_mcp_server")
    create_server(make_settings(), transport=fake.transport)
    assert caplog.records == []
    create_server(
        make_settings(verify_tls=False, base_url="http://192.168.1.2/nc"),
        transport=fake.transport,
    )
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "VERIFY_TLS=false" in messages
    assert "http://" in messages


async def test_gated_tool_cannot_be_called(make_settings, fake: FakeNextcloud) -> None:
    async with Client(create_server(make_settings(), transport=fake.transport)) as client:
        result = await client.call_tool_mcp("create_text_file", {"path": "x.txt", "content": "x"})
    assert result.isError is True
    assert "x.txt" not in fake.files


async def test_call_tools_end_to_end(settings, fake: FakeNextcloud) -> None:
    async with Client(create_server(settings, transport=fake.transport)) as client:
        tree = await client.call_tool_mcp("get_file_tree", {"path": "Docs", "depth": 2})
        assert tree.isError is False
        assert tree.structuredContent["path"] == "Docs"
        assert [e["name"] for e in tree.structuredContent["entries"]][:2] == ["Sub", "deep.txt"]

        text = await client.call_tool_mcp("get_file_content", {"path": "Docs/readme.md"})
        assert text.isError is False
        assert text.structuredContent is None
        assert text.content[0].text == "# Hello\n"

        read = await client.call_tool_mcp("read_text_file", {"path": "Docs/readme.md"})
        etag = read.structuredContent["etag"]

        stale = await client.call_tool_mcp(
            "update_text_file", {"path": "Docs/readme.md", "content": "v2", "expected_etag": "x"}
        )
        assert stale.isError is True
        assert f"current etag {etag}" in stale.content[0].text

        updated = await client.call_tool_mcp(
            "update_text_file",
            {"path": "Docs/readme.md", "content": "v2", "expected_etag": f'"{etag}"'},
        )
        assert updated.isError is False
        assert updated.structuredContent["status"] == "updated"
        new_etag = updated.structuredContent["etag"]

        upload = await client.call_tool_mcp(
            "upload_file",
            {"path": "bin.dat", "content_base64": base64.b64encode(b"\x00\x01").decode()},
        )
        assert upload.structuredContent["status"] == "created"

        created = await client.call_tool_mcp(
            "create_text_file", {"path": "Docs/new.txt", "content": "n"}
        )
        assert created.structuredContent["status"] == "created"

        deleted = await client.call_tool_mcp(
            "delete_file_checked", {"path": "Docs/readme.md", "expected_etag": new_etag}
        )
        assert deleted.structuredContent["status"] == "deleted"

        gone = await client.call_tool_mcp("read_text_file", {"path": "Docs/readme.md"})
        assert gone.isError is True
        assert gone.content[0].text == '"Docs/readme.md" does not exist.'

        calendars = await client.call_tool_mcp("list_calendars", {})
        assert calendars.structuredContent == {"calendars": []}
        tasks = await client.call_tool_mcp("list_tasks", {})
        assert tasks.structuredContent == {
            "tasks": [],
            "truncated": False,
            "skipped_large_objects": 0,
        }


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list_calendars", {"unexpected": 1}),
        ("get_file_tree", {"depth": 4}),
        ("get_file_tree", {"depth": 0}),
        ("list_tasks", {"limit": 501}),
        ("update_text_file", {"path": "a", "content": "b", "expected_etag": ""}),
        ("read_text_file", {}),
        ("get_file_tree", {"path": "a//b"}),
        ("read_text_file", {"path": "../secret"}),
    ],
)
async def test_invalid_arguments_are_errors(settings, fake, tool: str, arguments: dict) -> None:
    async with Client(create_server(settings, transport=fake.transport)) as client:
        result = await client.call_tool_mcp(tool, arguments)
    assert result.isError is True
    assert not [r for r in fake.requests if "/remote.php/" in str(r.url)]


async def test_unexpected_errors_are_masked(settings, fake, monkeypatch) -> None:
    async def boom(self):
        raise RuntimeError("internal detail with secret-ish text")

    monkeypatch.setattr(NextcloudClient, "user_id", boom)
    async with Client(create_server(settings, transport=fake.transport)) as client:
        result = await client.call_tool_mcp("get_file_tree", {})
    assert result.isError is True
    assert "internal detail" not in result.content[0].text


async def test_lifespan_closes_http_client(settings, fake, monkeypatch) -> None:
    closed: list[bool] = []
    original = NextcloudClient.aclose

    async def aclose(self):
        closed.append(True)
        await original(self)

    monkeypatch.setattr(NextcloudClient, "aclose", aclose)
    async with Client(create_server(settings, transport=fake.transport)) as client:
        await client.call_tool_mcp("get_file_tree", {})
        assert closed == []
    assert closed == [True]


async def test_tool_calls_through_a_realistic_backend_policy(
    make_settings, fake: FakeNextcloud, monkeypatch, caplog
) -> None:
    import sys
    import types

    from test_client import realistic_policy

    calls: list[dict] = []
    module = types.ModuleType("backend_policy")
    module.async_client = realistic_policy(fake.transport, calls)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend_policy", module)
    caplog.set_level(logging.WARNING, logger="nextcloud_mcp_server")

    settings = make_settings(readonly=False)
    async with Client(create_server(settings)) as client:
        tree = await client.call_tool_mcp("get_file_tree", {"path": "Docs"})
        assert tree.isError is False, tree.content
        read = await client.call_tool_mcp("read_text_file", {"path": "Docs/readme.md"})
        assert read.structuredContent["content"] == "# Hello\n"
        created = await client.call_tool_mcp(
            "create_text_file", {"path": "Docs/via-hook.txt", "content": "ok"}
        )
        assert created.structuredContent["status"] == "created"
    assert len(calls) == 1
    assert not {"follow_redirects", "trust_env", "transport", "verify"} & set(calls[0])
    assert caplog.records == []  # default TLS settings: no warning


async def test_tls_settings_ignored_under_policy_warns(
    make_settings, fake: FakeNextcloud, monkeypatch, caplog
) -> None:
    import sys
    import types

    from test_client import realistic_policy

    module = types.ModuleType("backend_policy")
    module.async_client = realistic_policy(fake.transport, [])  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend_policy", module)
    caplog.set_level(logging.WARNING, logger="nextcloud_mcp_server")
    create_server(make_settings(verify_tls=False))
    messages = [r.getMessage() for r in caplog.records]
    assert any("ignored: backend_policy owns TLS verification" in m for m in messages)


async def test_note_is_optional_in_output_schemas(settings, fake: FakeNextcloud) -> None:
    async with Client(create_server(settings, transport=fake.transport)) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in ("update_text_file", "upload_file"):
        schema = tools[name].outputSchema
        assert "note" in schema["properties"]
        assert "note" not in schema["required"]
