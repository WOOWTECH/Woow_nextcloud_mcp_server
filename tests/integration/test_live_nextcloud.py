"""Integration tests against a real Nextcloud.

Skipped unless NEXTCLOUD_MCP_IT_BASE_URL, NEXTCLOUD_MCP_IT_USERNAME and
NEXTCLOUD_MCP_IT_APP_PASSWORD are set. Use a dedicated test account. The tests only
touch a folder ``woow-mcp-it-<random>`` that they create and delete again (deleted items
go to the trash bin), and a calendar ``woow-mcp-it`` that is created once per account
and reused: Nextcloud rate-limits calendar creation (about 10 per user and hour by
default), so each run only adds and removes its own ``woow-mcp-it-<random>-*`` objects.
If the calendar does not exist yet and creating it is rate-limited, that test is skipped.
Optional: NEXTCLOUD_MCP_IT_VERIFY_TLS=false for a LAN server with a private CA.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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


WRITE_TOOLS = {"create_text_file", "update_text_file", "upload_file", "delete_file_checked"}

# Nextcloud's ETags have about one-second resolution for writes to the same file: a
# conditional write less than ~1 s after the previous write may keep the old ETag, or the
# old ETag may still match. Tests of stale-etag refusal therefore wait this long after
# the last write to the file before the conditional write (verified on Nextcloud 35).
ETAG_RESOLUTION = 1.1


@dataclass
class Live:
    client: Client
    http: httpx.AsyncClient
    folder: str
    files_home: str
    calendars_home: str
    last_write: dict[str, float] = field(default_factory=dict)

    async def call(self, tool: str, arguments: dict) -> dict:
        result = await self.client.call_tool_mcp(tool, arguments)
        assert result.isError is False, (tool, result.content)
        if tool in WRITE_TOOLS:
            self.last_write[arguments["path"]] = time.monotonic()
        return result.structuredContent

    async def settle(self, path: str) -> None:
        """Wait until ETAG_RESOLUTION has passed since the last write to ``path``."""
        last = self.last_write.get(path)
        if last is not None:
            remaining = ETAG_RESOLUTION - (time.monotonic() - last)
            if remaining > 0:
                await asyncio.sleep(remaining)

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
                        "BACKEND_DESTINATION_DENIED: "  # code mode is on under the hook
                        f'The gateway refused this request for "{path}" '
                        "(destination or path not allowed)."
                    )
                    continue
                created = result.structuredContent
                nc.last_write[path] = time.monotonic()
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

            await nc.settle(path)
            updated = await nc.call(
                "update_text_file",
                {"path": path, "content": "two\n", "expected_etag": read["etag"]},
            )
            assert updated["previous_etag"] == read["etag"]
            assert updated["etag"] != read["etag"]

            await nc.settle(path)
            stale = await nc.fails(
                "update_text_file",
                {"path": path, "content": "three\n", "expected_etag": read["etag"]},
            )
            assert "changed since it was read" in stale
            assert updated["etag"] in stale
            assert (await nc.call("read_text_file", {"path": path}))["content"] == "two\n"

            await nc.settle(path)
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
        await nc.settle(path)
        replaced = await nc.call(
            "upload_file",
            {"path": path, "content_base64": "AAEC", "expected_etag": created["etag"]},
        )
        assert replaced["status"] == "replaced" and replaced["bytes"] == 3
        await nc.settle(path)
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


# One calendar per test account is reused across runs: Nextcloud rate-limits calendar
# creation per user (app config ``dav`` ``rateLimitCalendarCreation`` /
# ``rateLimitPeriodCalendarCreation``, by default about 10 per hour), so creating a new
# calendar on every run soon answers 429. Each run adds its own uniquely named objects
# and deletes exactly those; the calendar itself is never deleted.
IT_CALENDAR = "woow-mcp-it"

MKCALENDAR = """<?xml version="1.0" encoding="utf-8"?>
<c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
 <d:set><d:prop>
  <d:displayname>{name}</d:displayname>
  <c:supported-calendar-component-set>
   <c:comp name="VTODO"/><c:comp name="VEVENT"/>
  </c:supported-calendar-component-set>
 </d:prop></d:set>
</c:mkcalendar>"""

PROPFIND_COMPONENTS = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    b"<d:prop><c:supported-calendar-component-set/></d:prop></d:propfind>"
)


def _ics(component: str, uid: str, *lines: str) -> str:
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//WOOW//integration tests//EN",
            f"BEGIN:{component}",
            f"UID:{uid}",
            "DTSTAMP:20261007T000000Z",
            *lines,
            f"END:{component}",
            "END:VCALENDAR",
            "",
        ]
    )


def _todo(uid: str, *lines: str) -> str:
    return _ics("VTODO", uid, *lines)


async def _shared_calendar(nc: Live) -> tuple[str, bool]:
    """URL of the reused test calendar (created once) and whether it accepts VEVENT."""
    cal_url = f"{nc.calendars_home}{IT_CALENDAR}/"
    found = await nc.http.request(
        "PROPFIND",
        cal_url,
        content=PROPFIND_COMPONENTS,
        headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
    )
    if found.status_code == 404:
        made = await nc.http.request(
            "MKCALENDAR",
            cal_url,
            content=MKCALENDAR.format(name=IT_CALENDAR).encode(),
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )
        if made.status_code == 429:
            pytest.skip(
                "Nextcloud rate-limits calendar creation for this account (dav "
                "rateLimitCalendarCreation); create the calendar 'woow-mcp-it' once or "
                "try again later"
            )
        assert made.status_code == 201, made.status_code
        return cal_url, True
    assert found.status_code == 207, found.status_code
    return cal_url, b'name="VEVENT"' in found.content or b"name='VEVENT'" in found.content


async def test_calendars_and_tasks() -> None:
    async with live() as nc:
        cal_id = IT_CALENDAR
        cal_url, events_allowed = await _shared_calendar(nc)
        run = nc.folder  # woow-mcp-it-<random>: unique per run
        uid = {name: f"{run}-{name}" for name in ("open-later", "open-soon", "done", "no-due")}
        objects = {
            uid["open-later"]: _todo(uid["open-later"], "SUMMARY:Later", "DUE;VALUE=DATE:20991231"),
            uid["open-soon"]: _todo(uid["open-soon"], "SUMMARY:Soon", "DUE:20990101T080000Z"),
            uid["done"]: _todo(
                uid["done"], "SUMMARY:Done", "STATUS:COMPLETED", "COMPLETED:20261001T000000Z"
            ),
            uid["no-due"]: _todo(
                uid["no-due"], "SUMMARY:Whenever 中文", "DESCRIPTION:" + "x" * 800
            ),
        }
        if events_allowed:
            objects[f"{run}-event"] = _ics(
                "VEVENT", f"{run}-event", "SUMMARY:Not a task", "DTSTART:20991231T090000Z"
            )
        created: list[str] = []
        try:
            for name, ics in objects.items():
                put = await nc.http.put(
                    f"{cal_url}{name}.ics",
                    content=ics.encode(),
                    headers={
                        "Content-Type": "text/calendar; charset=utf-8",
                        "If-None-Match": "*",
                    },
                )
                assert put.status_code in (201, 204), put.status_code
                created.append(name)

            calendars = (await nc.call("list_calendars", {}))["calendars"]
            mine = [c for c in calendars if c["id"] == cal_id]
            assert mine and "VTODO" in mine[0]["components"] and mine[0]["writable"] is True

            # The calendar may hold objects of other (earlier, crashed) runs: look at ours.
            tasks = (await nc.call("list_tasks", {"calendar": cal_id, "limit": 500}))["tasks"]
            ours = [t for t in tasks if (t["uid"] or "").startswith(f"{run}-")]
            assert [t["uid"] for t in ours] == [uid["open-soon"], uid["open-later"], uid["no-due"]]
            assert ours[1]["due"] == "2099-12-31"
            assert ours[0]["due"] == "2099-01-01T08:00:00Z"
            assert len(ours[2]["description"]) == 500
            assert f"{run}-event" not in {t["uid"] for t in tasks}  # events are not tasks

            everything = await nc.call(
                "list_tasks", {"calendar": cal_id, "include_completed": True, "limit": 3}
            )
            assert everything["truncated"] is True
            assert len(everything["tasks"]) == 3
            completed = await nc.call(
                "list_tasks", {"calendar": cal_id, "include_completed": True, "limit": 500}
            )
            assert uid["done"] in {t["uid"] for t in completed["tasks"]}

            all_calendars = (await nc.call("list_tasks", {"limit": 500}))["tasks"]
            assert {uid["open-soon"], uid["open-later"], uid["no-due"]} <= {
                t["uid"] for t in all_calendars
            }
            assert "Unknown calendar" in await nc.fails(
                "list_tasks", {"calendar": cal_id + "-missing"}
            )
        finally:
            # Delete exactly the objects this run created; never the calendar.
            for name in created:
                await nc.http.request("DELETE", f"{cal_url}{name}.ics")


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
    expected = "Nextcloud rejected the username or app password."
    if GATEWAY:  # code mode is on automatically under the gateway hook
        expected = "BACKEND_HTTP_ERROR status=401: " + expected
    assert result.content[0].text.startswith(expected)
