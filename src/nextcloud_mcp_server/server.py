"""FastMCP server factory and command line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastmcp import FastMCP

from . import __version__
from .client import NextcloudClient
from .settings import Settings, SettingsError, load_settings
from .tools import register_tools

SERVER_NAME = "Woow Nextcloud"

INSTRUCTIONS = "\n".join(
    (
        "This server works with ONE Nextcloud account chosen by the administrator; you cannot switch accounts.",  # noqa: E501
        'Paths are relative to that account\'s home folder ("" or "/" is the home); use \'/\' as separator.',  # noqa: E501
        "Start with get_file_tree to see folders and files, then read_text_file to read a text file.",  # noqa: E501
        "Always read a file first: update_text_file, upload_file (replace) and delete_file_checked need its etag.",  # noqa: E501
        "If a write fails because the file changed (etag mismatch, HTTP 412), never retry blindly: read it again, merge, then retry.",  # noqa: E501
        "create_text_file and upload_file without expected_etag never overwrite an existing file.",
        "Deleted files go to the Nextcloud trash bin (when the Deleted files app is enabled).",
        "Text tools refuse folders, binary (non UTF-8) files and files over the configured size limit.",  # noqa: E501
        "list_calendars shows calendar ids; list_tasks reads tasks (VTODO) from them.",
        "Some tools may be absent because the administrator made the server read-only or disabled them.",  # noqa: E501
    )
)


def create_server(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastMCP:
    """Build the MCP server for ``settings``. No request is sent to Nextcloud here."""
    nc = NextcloudClient(settings, transport=transport)

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {"nextcloud": nc}
        finally:
            await nc.aclose()

    server: FastMCP = FastMCP(
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
        mask_error_details=True,
        on_duplicate="error",
    )
    register_tools(server, nc, settings)
    return server


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nextcloud-mcp-server",
        description="MCP server for one Nextcloud account (settings: NEXTCLOUD_MCP_* env).",
    )
    parser.add_argument("--transport", choices=("stdio", "http", "sse"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (http/sse)")
    parser.add_argument("--port", type=_port, default=3000, help="port (http/sse)")
    parser.add_argument(
        "--path", default=None, help="endpoint path (http: /mcp, sse: /sse by default)"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"nextcloud-mcp-server: configuration error: {exc}", file=sys.stderr)
        return 2
    server = create_server(settings)
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        path = args.path or ("/mcp" if args.transport == "http" else "/sse")
        if not path.startswith("/"):
            path = "/" + path
        server.run(transport=args.transport, host=args.host, port=args.port, path=path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
