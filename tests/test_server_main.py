from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time

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
    assert runs == [
        {
            "transport": "http",
            "host": "127.0.0.1",
            "port": 3001,
            "path": "/mcp",
            "host_origin_protection": True,
            "allowed_hosts": ["127.0.0.1"],
        }
    ]


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


# ----------------------------------------------------------------- start-up refusals (exit 2)


def test_unknown_disabled_tool_exits(monkeypatch: pytest.MonkeyPatch, capsys, runs) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("NEXTCLOUD_MCP_DISABLED_TOOLS", "upload_fiel")
    assert main([]) == 2
    err = capsys.readouterr().err
    assert "NEXTCLOUD_MCP_DISABLED_TOOLS" in err
    assert "upload_fiel" in err
    assert runs == []


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("x = 1\n", "no callable async_client"),
        ("raise ImportError('nope')\n", "could not be imported (ImportError)"),
    ],
)
def test_unusable_backend_policy_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, runs, source: str, fragment: str
) -> None:
    _set_env(monkeypatch)
    (tmp_path / "backend_policy.py").write_text(source)
    monkeypatch.syspath_prepend(str(tmp_path))
    assert main([]) == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert fragment in err
    assert PASSWORD not in err
    assert runs == []


def test_quiet_fastmcp_defaults(monkeypatch: pytest.MonkeyPatch, runs) -> None:
    import fastmcp

    _set_env(monkeypatch)
    monkeypatch.delenv("FASTMCP_CHECK_FOR_UPDATES", raising=False)
    monkeypatch.delenv("FASTMCP_SHOW_SERVER_BANNER", raising=False)
    monkeypatch.setattr(fastmcp.settings, "check_for_updates", "stable")
    monkeypatch.setattr(fastmcp.settings, "show_server_banner", True)
    main([])
    assert os.environ["FASTMCP_CHECK_FOR_UPDATES"] == "off"
    assert os.environ["FASTMCP_SHOW_SERVER_BANNER"] == "false"
    assert fastmcp.settings.check_for_updates == "off"
    assert fastmcp.settings.show_server_banner is False


def test_explicit_fastmcp_env_is_respected(monkeypatch: pytest.MonkeyPatch, runs) -> None:
    import fastmcp

    _set_env(monkeypatch)
    monkeypatch.setenv("FASTMCP_SHOW_SERVER_BANNER", "true")
    monkeypatch.setattr(fastmcp.settings, "show_server_banner", True)
    main([])
    assert os.environ["FASTMCP_SHOW_SERVER_BANNER"] == "true"
    assert fastmcp.settings.show_server_banner is True


def test_http_guard_hosts(make_settings) -> None:
    from nextcloud_mcp_server.server import http_guard

    settings = make_settings(allowed_hosts="mcp.example.com")
    assert http_guard(settings, "127.0.0.1") == {
        "host_origin_protection": True,
        "allowed_hosts": ["mcp.example.com", "127.0.0.1"],
    }
    assert http_guard(settings, "0.0.0.0")["allowed_hosts"] == ["mcp.example.com"]  # noqa: S104
    (guard,) = http_guard(settings, "127.0.0.1", "sse")["middleware"]
    assert guard.kwargs == {
        "allowed_hosts": ["mcp.example.com", "127.0.0.1"],
        "mode": "strict",
    }


# ----------------------------------------------------------------- real HTTP / SSE servers

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def _running(tmp_path, transport: str, **extra_env: str):
    import httpx

    port = _free_port()
    proc = subprocess.Popen(  # noqa: S603 - fixed interpreter and module
        [
            sys.executable,
            "-m",
            "nextcloud_mcp_server.server",
            "--transport",
            transport,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=_child_env(
            NEXTCLOUD_MCP_BASE_URL="http://127.0.0.1:9",
            NEXTCLOUD_MCP_USERNAME="alice",
            NEXTCLOUD_MCP_APP_PASSWORD=PASSWORD,
            **extra_env,
        ),
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                httpx.get(f"http://127.0.0.1:{port}/", timeout=1)
                break
            except httpx.TransportError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError(
                        proc.stderr.read().decode() if proc.stderr else ""
                    ) from None
                time.sleep(0.1)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if proc.stderr:
            stderr = proc.stderr.read().decode()
            assert PASSWORD not in stderr
            proc.stderr.close()


def test_http_transport_host_and_origin_protection(tmp_path) -> None:
    import httpx

    with _running(tmp_path, "http", NEXTCLOUD_MCP_ALLOWED_HOSTS="mcp.example.com") as port:
        url = f"http://127.0.0.1:{port}/mcp"

        def post(**headers: str) -> int:
            return httpx.post(url, json=INITIALIZE, headers={**MCP_HEADERS, **headers}).status_code

        # what a WOOW gateway sends: Host rewritten to the child's address, no Origin
        assert post(Host=f"127.0.0.1:{port}") == 200
        assert post(Host=f"localhost:{port}") == 200
        assert post(Host="mcp.example.com") == 200  # NEXTCLOUD_MCP_ALLOWED_HOSTS
        # DNS rebinding: a foreign Host is refused before reaching MCP
        assert post(Host="evil.example") == 421
        assert post(Host=f"evil.example:{port}") == 421
        # a browser page from another origin is refused
        assert post(Host=f"127.0.0.1:{port}", Origin="http://evil.example") == 403
        assert post(Host=f"127.0.0.1:{port}", Origin=f"http://127.0.0.1:{port}") == 200


def test_sse_transport_host_protection(tmp_path) -> None:
    import httpx

    with _running(tmp_path, "sse") as port:
        url = f"http://127.0.0.1:{port}/sse"
        assert httpx.get(url, headers={"Host": "evil.example"}, timeout=5).status_code == 421
        with httpx.stream("GET", url, headers={"Host": f"127.0.0.1:{port}"}, timeout=5) as answer:
            assert answer.status_code == 200
        messages = f"http://127.0.0.1:{port}/messages/?session_id=0"
        assert httpx.post(messages, headers={"Host": "evil.example"}, timeout=5).status_code == 421
