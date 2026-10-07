"""Every row of the error table (SPEC §6), both as pure mapping and through the tools."""

from __future__ import annotations

import base64

import httpx
import pytest
from fastmcp.exceptions import ToolError

from fake_nextcloud import FakeNextcloud
from nextcloud_mcp_server.errors import stale_message, status_message
from nextcloud_mcp_server.tools import NextcloudTools

P = "Docs/a #1.md"


@pytest.mark.parametrize(
    ("status", "op", "expected"),
    [
        (401, "read", "Nextcloud rejected the username or app password."),
        (403, "read", f'Permission denied for "{P}".'),
        (404, "read", f'"{P}" does not exist.'),
        (405, "create", f'"{P}" is a folder or cannot be written.'),
        (405, "update", f'"{P}" is a folder or cannot be written.'),
        (405, "upload_create", f'"{P}" is a folder or cannot be written.'),
        (409, "create", f'The parent folder of "{P}" does not exist.'),
        (404, "create", f'The parent folder of "{P}" does not exist.'),
        (404, "upload_create", f'The parent folder of "{P}" does not exist.'),
        (409, "upload_replace", f'The parent folder of "{P}" does not exist.'),
        (
            412,
            "create",
            f'"{P}" already exists; read it first and use update_text_file with its etag.',
        ),
        (
            412,
            "update",
            f'"{P}" changed since it was read (current etag unknown); read it again and retry.',
        ),
        (
            412,
            "delete",
            f'"{P}" changed since it was read (current etag unknown); read it again and retry.',
        ),
        (
            412,
            "upload_replace",
            f'"{P}" changed since it was read (current etag unknown); read it again and retry.',
        ),
        (
            423,
            "update",
            f'"{P}" is locked (it may be open in Nextcloud Office or locked by another user); '
            "try again later or ask the owner to unlock it.",
        ),
        (413, "upload_create", f'Not enough storage or file too large for "{P}".'),
        (507, "create", f'Not enough storage or file too large for "{P}".'),
        (500, "read", "Nextcloud returned an error (500); try again later."),
        (502, "list", "Nextcloud returned an error (502); try again later."),
        (503, "calendar", "Nextcloud returned an error (503); try again later."),
        (405, "read", f'Nextcloud refused the request for "{P}" (405).'),
        (409, "delete", f'Nextcloud refused the request for "{P}" (409).'),
        (400, "read", f'Nextcloud refused the request for "{P}" (400).'),
    ],
)
def test_status_message(status: int, op: str, expected: str) -> None:
    assert status_message(status, P, op) == expected  # type: ignore[arg-type]


def test_status_message_detail_and_etag() -> None:
    assert status_message(400, P, "read", detail="Bad") == (
        f'Nextcloud refused the request for "{P}" (400): Bad.'
    )
    assert "current etag abc" in status_message(412, P, "update", current_etag="abc")
    assert stale_message(P, "abc") == (
        f'"{P}" changed since it was read (current etag abc); read it again and retry.'
    )
    assert "pass it as expected_etag" in status_message(412, P, "upload_create")


@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        (401, "Nextcloud rejected the username or app password."),
        (403, f'Permission denied for "{P}".'),
        (404, f'"{P}" does not exist.'),
        (423, f'"{P}" is locked'),
        (500, "Nextcloud returned an error (500); try again later."),
        (507, f'Not enough storage or file too large for "{P}".'),
    ],
)
@pytest.mark.parametrize("tool", ["read", "tree", "create", "update", "upload", "delete"])
async def test_tool_level_mapping(
    tools: NextcloudTools, fake: FakeNextcloud, status: int, fragment: str, tool: str
) -> None:
    fake.add_file(P, b"x")
    fake.override = lambda r: httpx.Response(status, text="<html>" + "z" * 5000 + "</html>")
    calls = {
        "read": lambda: tools.read_text_file(P),
        "tree": lambda: tools.get_file_tree(P),
        "create": lambda: tools.create_text_file(P, "x"),
        "update": lambda: tools.update_text_file(P, "x", "e"),
        "upload": lambda: tools.upload_file(P, base64.b64encode(b"x").decode(), "e"),
        "delete": lambda: tools.delete_file_checked(P, "e"),
    }
    with pytest.raises(ToolError) as info:
        await calls[tool]()
    message = str(info.value)
    if tool == "tree" and status == 404:
        assert message == f'"{P}" does not exist.'
    elif tool == "create" and status == 404:
        assert message == f'The parent folder of "{P}" does not exist.'
    else:
        assert fragment in message
    assert "zzzz" not in message  # never echo raw server bodies
    assert len(message) < 300
