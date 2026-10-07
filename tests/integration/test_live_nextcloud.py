"""Integration tests against a real Nextcloud.

Skipped unless NEXTCLOUD_MCP_IT_BASE_URL, NEXTCLOUD_MCP_IT_USERNAME and
NEXTCLOUD_MCP_IT_APP_PASSWORD are set. Use a dedicated test account. The tests only
touch a folder ``woow-mcp-it-<random>`` and a calendar ``woow-mcp-it-<random>`` that
they create themselves and delete again at the end (deleted items go to the trash bin).
Optional: NEXTCLOUD_MCP_IT_VERIFY_TLS=false for a LAN server with a private CA.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import quote

import httpx
import pytest
from fastmcp import Client

from nextcloud_mcp_server.server import create_server
from nextcloud_mcp_server.settings import Settings

IT_VARS = ("BASE_URL", "USERNAME", "APP_PASSWORD")
pytestmark = pytest.mark.skipif(
    not all(os.environ.get(f"NEXTCLOUD_MCP_IT_{name}") for name in IT_VARS),
    reason="set NEXTCLOUD_MCP_IT_BASE_URL/_USERNAME/_APP_PASSWORD to run integration tests",
)

# True when a gateway's backend_policy module is importable (e.g. via PYTHONPATH).
GATEWAY = importlib.util.find_spec("backend_policy") is not None

SPECIAL_NAMES = [
    "plain.md",
    "hash #1.md",
    "question?.txt",
    "100% done.txt",
    "with space & plus+semi;colon.txt",
    "中文 檔案.md",
]


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "base_url": os.environ["NEXTCLOUD_MCP_IT_BASE_URL"],
        "username": os.environ["NEXTCLOUD_MCP_IT_USERNAME"],
        "app_password": os.environ["NEXTCLOUD_MCP_IT_APP_PASSWORD"],
        "verify_tls": os.environ.get("NEXTCLOUD_MCP_IT_VERIFY_TLS", "true").lower()
        not in ("0", "false", "no"),
        "readonly": False,
        "allow_delete": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@dataclass
class Live:
    client: Client
    http: httpx.AsyncClient
    folder: str
    files_home: str
    calendars_home: str

    async def call(self, tool: str, arguments: dict) -> dict:
        result = await self.client.call_tool_mcp(tool, arguments)
        assert result.isError is False, (tool, result.content)
        return result.structuredContent

    async def fails(self, tool: str, arguments: dict) -> str:
        result = await self.client.call_tool_mcp(tool, arguments)
        assert result.isError is True, (tool, result.structuredContent)
        return result.content[0].text


@asynccontextmanager
async def live() -> AsyncIterator[Live]:
    settings = _settings()
    auth = httpx.BasicAuth(settings.username, settings.app_password.get_secret_value())
    async with httpx.AsyncClient(
        auth=auth, verify=settings.verify_tls, timeout=60, follow_redirects=False
    ) as http:
        answer = await http.get(
            f"{settings.base_url}/ocs/v2.php/cloud/user?format=json",
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        )
        answer.raise_for_status()
        user = quote(answer.json()["ocs"]["data"]["id"], safe="")
        files_home = f"{settings.base_url}/remote.php/dav/files/{user}/"
        calendars_home = f"{settings.base_url}/remote.php/dav/calendars/{user}/"
        folder = f"woow-mcp-it-{secrets.token_hex(4)}"
        made = await http.request("MKCOL", files_home + folder)
        assert made.status_code == 201, made.status_code
        try:
            async with Client(create_server(settings)) as client:
                yield Live(client, http, folder, files_home, calendars_home)
        finally:
            await http.request("DELETE", files_home + folder)


async def test_file_lifecycle_with_special_names() -> None:
    async with live() as nc:
        for name in SPECIAL_NAMES:
            path = f"{nc.folder}/{name}"
            if GATEWAY and "%" in name:
                # A gateway backend_policy may refuse paths containing '%'.
                result = await nc.client.call_tool_mcp(
                    "create_text_file", {"path": path, "content": "one\n"}
                )
                if result.isError:
                    assert result.content[0].text == (
                        f'The gateway refused this request for "{path}" '
                        "(destination or path not allowed)."
                    )
                    continue
                created = result.structuredContent
            else:
                created = await nc.call("create_text_file", {"path": path, "content": "one\n"})
            assert created["status"] == "created" and created["etag"]
            assert "already exists" in await nc.fails(
                "create_text_file", {"path": path, "content": "again"}
            )

            read = await nc.call("read_text_file", {"path": "/" + path})
            assert read["content"] == "one\n"
            assert read["path"] == path
            assert read["etag"] == created["etag"]
            text = await nc.client.call_tool_mcp("get_file_content", {"path": path})
            assert text.content[0].text == "one\n"

            updated = await nc.call(
                "update_text_file",
                {"path": path, "content": "two\n", "expected_etag": read["etag"]},
            )
            assert updated["previous_etag"] == read["etag"]
            assert updated["etag"] != read["etag"]

            stale = await nc.fails(
                "update_text_file",
                {"path": path, "content": "three\n", "expected_etag": read["etag"]},
            )
            assert "changed since it was read" in stale
            assert updated["etag"] in stale
            assert (await nc.call("read_text_file", {"path": path}))["content"] == "two\n"

            stale_delete = await nc.fails(
                "delete_file_checked", {"path": path, "expected_etag": read["etag"]}
            )
            assert "changed since it was read" in stale_delete

            deleted = await nc.call(
                "delete_file_checked", {"path": path, "expected_etag": updated["etag"]}
            )
            assert deleted["status"] == "deleted"
            assert "does not exist" in await nc.fails("read_text_file", {"path": path})


async def test_upload_create_replace_and_binary_refusal() -> None:
    async with live() as nc:
        path = f"{nc.folder}/blob #1 中.bin"
        payload = bytes(range(256)) * 4
        created = await nc.call(
            "upload_file", {"path": path, "content_base64": base64.b64encode(payload).decode()}
        )
        assert created == {
            "status": "created",
            "path": path,
            "etag": created["etag"],
            "bytes": 1024,
        }
        assert "already exists" in await nc.fails(
            "upload_file", {"path": path, "content_base64": "AAEC"}
        )
        replaced = await nc.call(
            "upload_file",
            {"path": path, "content_base64": "AAEC", "expected_etag": created["etag"]},
        )
        assert replaced["status"] == "replaced" and replaced["bytes"] == 3
        stale = await nc.fails(
            "upload_file",
            {"path": path, "content_base64": "AAEC", "expected_etag": created["etag"]},
        )
        assert "changed since it was read" in stale
        assert "not a UTF-8 text file" in await nc.fails("read_text_file", {"path": path})
        await nc.call("delete_file_checked", {"path": path, "expected_etag": replaced["etag"]})


async def test_tree_and_folder_refusals() -> None:
    async with live() as nc:
        sub = f"{nc.folder}/Sub #1"
        made = await nc.http.request("MKCOL", nc.files_home + quote(nc.folder) + "/Sub%20%231")
        assert made.status_code == 201
        await nc.call("create_text_file", {"path": f"{sub}/deep?.md", "content": "d"})
        await nc.call("create_text_file", {"path": f"{nc.folder}/top.txt", "content": "t"})

        tree = await nc.call("get_file_tree", {"path": nc.folder, "depth": 2})
        assert [(e["path"], e["type"]) for e in tree["entries"]] == [
            (sub, "folder"),
            (f"{sub}/deep?.md", "file"),
            (f"{nc.folder}/top.txt", "file"),
        ]
        top = tree["entries"][2]
        assert top["size"] == 1 and top["etag"] and top["modified"].endswith("Z")

        single = await nc.call("get_file_tree", {"path": f"{nc.folder}/top.txt"})
        assert [e["name"] for e in single["entries"]] == ["top.txt"]

        assert "is a folder" in await nc.fails("read_text_file", {"path": sub})
        folder_etag = tree["entries"][0]["etag"]
        assert "is a folder" in await nc.fails(
            "delete_file_checked", {"path": sub, "expected_etag": folder_etag}
        )
        assert "parent folder" in await nc.fails(
            "create_text_file", {"path": f"{nc.folder}/missing/x.txt", "content": "x"}
        )


MKCALENDAR = """<?xml version="1.0" encoding="utf-8"?>
<c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
 <d:set><d:prop>
  <d:displayname>{name}</d:displayname>
  <c:supported-calendar-component-set><c:comp name="VTODO"/></c:supported-calendar-component-set>
 </d:prop></d:set>
</c:mkcalendar>"""


def _todo(uid: str, *lines: str) -> str:
    body = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//WOOW//integration tests//EN",
            "BEGIN:VTODO",
            f"UID:{uid}",
            "DTSTAMP:20261007T000000Z",
            *lines,
            "END:VTODO",
            "END:VCALENDAR",
            "",
        ]
    )
    return body


async def test_calendars_and_tasks() -> None:
    async with live() as nc:
        cal_id = nc.folder
        cal_url = f"{nc.calendars_home}{cal_id}/"
        made = await nc.http.request(
            "MKCALENDAR",
            cal_url,
            content=MKCALENDAR.format(name=cal_id).encode(),
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )
        assert made.status_code == 201, made.status_code
        try:
            todos = {
                "open-later": _todo("open-later", "SUMMARY:Later", "DUE;VALUE=DATE:20991231"),
                "open-soon": _todo("open-soon", "SUMMARY:Soon", "DUE:20990101T080000Z"),
                "done": _todo(
                    "done", "SUMMARY:Done", "STATUS:COMPLETED", "COMPLETED:20261001T000000Z"
                ),
                "no-due": _todo("no-due", "SUMMARY:Whenever 中文", "DESCRIPTION:" + "x" * 800),
            }
            for uid, ics in todos.items():
                put = await nc.http.put(
                    f"{cal_url}{uid}.ics",
                    content=ics.encode(),
                    headers={"Content-Type": "text/calendar; charset=utf-8"},
                )
                assert put.status_code in (201, 204), put.status_code

            calendars = (await nc.call("list_calendars", {}))["calendars"]
            mine = [c for c in calendars if c["id"] == cal_id]
            assert mine and "VTODO" in mine[0]["components"] and mine[0]["writable"] is True

            tasks = (await nc.call("list_tasks", {"calendar": cal_id}))["tasks"]
            assert [t["uid"] for t in tasks] == ["open-soon", "open-later", "no-due"]
            assert tasks[1]["due"] == "2099-12-31"
            assert tasks[0]["due"] == "2099-01-01T08:00:00Z"
            assert len(tasks[2]["description"]) == 500

            everything = await nc.call(
                "list_tasks", {"calendar": cal_id, "include_completed": True, "limit": 3}
            )
            assert everything["truncated"] is True
            assert len(everything["tasks"]) == 3

            all_calendars = (await nc.call("list_tasks", {"limit": 500}))["tasks"]
            assert {"open-soon", "open-later", "no-due"} <= {t["uid"] for t in all_calendars}
            assert "Unknown calendar" in await nc.fails(
                "list_tasks", {"calendar": cal_id + "-missing"}
            )
        finally:
            await nc.http.request("DELETE", cal_url)


async def test_read_only_server_has_no_write_tools() -> None:
    async with Client(create_server(_settings(readonly=True))) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names == {
        "get_file_tree",
        "get_file_content",
        "read_text_file",
        "list_calendars",
        "list_tasks",
    }


async def test_wrong_password_is_reported() -> None:
    settings = _settings(app_password="definitely-wrong-" + secrets.token_hex(8))
    async with Client(create_server(settings)) as client:
        result = await client.call_tool_mcp("get_file_tree", {})
    assert result.isError is True
    assert result.content[0].text.startswith("Nextcloud rejected the username or app password.")
