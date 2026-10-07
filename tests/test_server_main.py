from __future__ import annotations

import os
import subprocess
import sys

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport

from fake_nextcloud import PASSWORD
from nextcloud_mcp_server import server as server_module
from nextcloud_mcp_server.server import build_parser, main


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXTCLOUD_MCP_BASE_URL", "https://cloud.example.com")
    monkeypatch.setenv("NEXTCLOUD_MCP_USERNAME", "alice")
    monkeypatch.setenv("NEXTCLOUD_MCP_APP_PASSWORD", PASSWORD)


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_run(self: FastMCP, transport=None, **kwargs) -> None:
        calls.append({"transport": transport, **kwargs})

    monkeypatch.setattr(FastMCP, "run", fake_run)
    return calls


def test_missing_settings_exit_code(capsys: pytest.CaptureFixture[str], runs) -> None:
    assert main([]) == 2
    err = capsys.readouterr().err
    assert err.startswith("nextcloud-mcp-server: configuration error: ")
    assert err.count("\n") == 1
    assert runs == []


def test_stdio_default(monkeypatch: pytest.MonkeyPatch, runs) -> None:
    _set_env(monkeypatch)
    assert main([]) == 0
    assert runs == [{"transport": "stdio"}]


def test_http(monkeypatch: pytest.MonkeyPatch, runs) -> None:
    _set_env(monkeypatch)
    assert main(["--transport", "http", "--host", "127.0.0.1", "--port", "3001"]) == 0
    assert runs == [{"transport": "http", "host": "127.0.0.1", "port": 3001, "path": "/mcp"}]


def test_sse_and_custom_path(monkeypatch: pytest.MonkeyPatch, runs) -> None:
    _set_env(monkeypatch)
    main(["--transport", "sse"])
    main(["--transport", "http", "--path", "custom"])
    assert runs[0]["path"] == "/sse"
    assert runs[1]["path"] == "/custom"
    assert runs[1]["host"] == "127.0.0.1"
    assert runs[1]["port"] == 3000


@pytest.mark.parametrize("port", ["0", "70000", "abc"])
def test_bad_port(port: str) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--port", port])


def test_bad_transport() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--transport", "websocket"])


def _child_env(**extra: str) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("NEXTCLOUD_MCP_")
    }
    env.update(
        FASTMCP_SHOW_SERVER_BANNER="false",
        FASTMCP_CHECK_FOR_UPDATES="off",
        **extra,
    )
    return env


def test_module_entry_point_fails_cleanly(tmp_path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "nextcloud_mcp_server.server"],
        env=_child_env(NEXTCLOUD_MCP_APP_PASSWORD=PASSWORD),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    lines = proc.stderr.strip().splitlines()
    assert len(lines) == 1
    assert "NEXTCLOUD_MCP_BASE_URL is required" in lines[0]
    assert PASSWORD not in proc.stderr


async def test_stdio_child_process_lists_tools_without_backend(tmp_path) -> None:
    # The base URL points to a closed local port: start-up must not contact it.
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "nextcloud_mcp_server.server"],
        env=_child_env(
            NEXTCLOUD_MCP_BASE_URL="http://127.0.0.1:9",
            NEXTCLOUD_MCP_USERNAME="alice",
            NEXTCLOUD_MCP_APP_PASSWORD=PASSWORD,
        ),
        cwd=str(tmp_path),
        keep_alive=False,
    )
    async with Client(transport) as client:
        names = [tool.name for tool in await client.list_tools()]
        assert names == [
            "get_file_tree",
            "get_file_content",
            "read_text_file",
            "list_calendars",
            "list_tasks",
        ]
        result = await client.call_tool_mcp("list_calendars", {})
        assert result.isError is True
        assert result.content[0].text.startswith("Could not reach Nextcloud (")
        assert PASSWORD not in str(result)


def test_server_module_exports() -> None:
    assert server_module.SERVER_NAME == "Woow Nextcloud"
