from __future__ import annotations

import httpx
import pytest
from fastmcp.exceptions import ToolError

from fake_nextcloud import BASE_URL, FakeNextcloud, vtodo
from nextcloud_mcp_server.tools import NextcloudTools

CAL_HOME = f"{BASE_URL}/remote.php/dav/calendars/alice%20smith/"


@pytest.fixture
def calendars(fake: FakeNextcloud) -> FakeNextcloud:
    work = fake.add_calendar("work #1", "Work", ["VEVENT", "VTODO"], color="#FF0000")
    work.objects["a.ics"] = vtodo("a", "Write report", "DUE;VALUE=DATE:20261012", "PRIORITY:1")
    work.objects["b.ics"] = vtodo(
        "b", "Old thing", "STATUS:COMPLETED", "COMPLETED:20261001T080000Z"
    )
    work.objects["c.ics"] = vtodo("c", "Call Bob", "DUE:20261010T090000Z", "STATUS:IN-PROCESS")
    work.objects["broken.ics"] = "garbage"
    personal = fake.add_calendar("personal", "Personal", ["VEVENT"], writable=False, color=None)
    personal.objects["e.ics"] = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e\r\nEND:VEVENT\r\n"
    chores = fake.add_calendar("chores", "Chores", ["VTODO"])
    chores.objects["d.ics"] = vtodo("d", "Dishes")
    chores.objects["x.ics"] = vtodo("x", "Dropped", "STATUS:CANCELLED")
    return fake


async def test_list_calendars(tools: NextcloudTools, calendars: FakeNextcloud) -> None:
    result = await tools.list_calendars()
    assert result == {
        "calendars": [
            {
                "id": "chores",
                "name": "Chores",
                "components": ["VTODO"],
                "color": "#0082c9",
                "writable": True,
            },
            {
                "id": "personal",
                "name": "Personal",
                "components": ["VEVENT"],
                "color": None,
                "writable": False,
            },
            {
                "id": "work #1",
                "name": "Work",
                "components": ["VEVENT", "VTODO"],
                "color": "#FF0000",
                "writable": True,
            },
        ]
    }
    request = next(r for r in calendars.requests if "/calendars/" in str(r.url))
    assert request.method == "PROPFIND"
    assert str(request.url) == CAL_HOME
    assert request.headers["Depth"] == "1"
    body = calendars.bodies[calendars.requests.index(request)]
    for prop in (
        b"<d:displayname/>",
        b"<d:resourcetype/>",
        b"<c:supported-calendar-component-set/>",
        b"<a:calendar-color/>",
        b"<d:current-user-privilege-set/>",
        b"<cs:getctag/>",
    ):
        assert prop in body
    assert b'xmlns:a="http://apple.com/ns/ical/"' in body


async def test_list_tasks_all_open(tools: NextcloudTools, calendars: FakeNextcloud) -> None:
    result = await tools.list_tasks()
    assert result["truncated"] is False
    assert [(t["calendar"], t["summary"]) for t in result["tasks"]] == [
        ("work #1", "Call Bob"),
        ("work #1", "Write report"),
        ("chores", "Dishes"),
    ]
    first = result["tasks"][0]
    assert first == {
        "calendar": "work #1",
        "uid": "c",
        "summary": "Call Bob",
        "status": "IN-PROCESS",
        "due": "2026-10-10T09:00:00Z",
        "start": None,
        "completed": None,
        "percent_complete": None,
        "priority": None,
        "description": None,
    }
    reports = [r for r in calendars.requests if r.method == "REPORT"]
    assert [str(r.url) for r in reports] == [CAL_HOME + "chores/", CAL_HOME + "work%20%231/"]
    for report in reports:
        assert report.headers["Depth"] == "1"
        body = calendars.bodies[calendars.requests.index(report)]
        assert b"<c:calendar-query" in body
        assert b'<c:comp-filter name="VTODO"/>' in body
        assert b"<c:calendar-data/>" in body


async def test_list_tasks_completed_and_limit(tools: NextcloudTools, calendars) -> None:
    result = await tools.list_tasks(include_completed=True)
    summaries = [t["summary"] for t in result["tasks"]]
    assert summaries[:3] == ["Call Bob", "Write report", "Dishes"]
    assert set(summaries[3:]) == {"Old thing", "Dropped"}

    limited = await tools.list_tasks(include_completed=True, limit=2)
    assert [t["summary"] for t in limited["tasks"]] == ["Call Bob", "Write report"]
    assert limited["truncated"] is True


async def test_list_tasks_one_calendar(tools: NextcloudTools, calendars: FakeNextcloud) -> None:
    result = await tools.list_tasks(calendar="chores", include_completed=True)
    assert [t["uid"] for t in result["tasks"]] == ["d", "x"]
    assert [r.method for r in calendars.requests if r.method == "REPORT"] == ["REPORT"]


async def test_list_tasks_unknown_or_wrong_calendar(tools: NextcloudTools, calendars) -> None:
    with pytest.raises(ToolError, match=r'^Unknown calendar "nope"; call list_calendars'):
        await tools.list_tasks(calendar="nope")
    with pytest.raises(ToolError, match=r'Calendar "personal" does not hold tasks'):
        await tools.list_tasks(calendar="personal")


async def test_list_tasks_skips_unreadable_calendar(tools: NextcloudTools, calendars) -> None:
    def override(request: httpx.Request) -> httpx.Response | None:
        if request.method == "REPORT" and b"chores" in request.url.raw_path:
            return httpx.Response(403)
        return None

    calendars.override = override
    result = await tools.list_tasks()
    assert [t["summary"] for t in result["tasks"]] == ["Call Bob", "Write report"]
    with pytest.raises(ToolError, match=r'^Permission denied for "chores"\.$'):
        await tools.list_tasks(calendar="chores")


async def test_list_tasks_server_error(tools: NextcloudTools, calendars) -> None:
    calendars.override = lambda r: httpx.Response(503) if r.method == "REPORT" else None
    with pytest.raises(ToolError, match=r"Nextcloud returned an error \(503\)"):
        await tools.list_tasks()


async def test_list_tasks_too_large(tools: NextcloudTools, calendars, monkeypatch) -> None:
    import nextcloud_mcp_server.client as client_module

    original = tools.nc.send

    async def send(method, url, **kwargs):
        if method == "REPORT":
            monkeypatch.setattr(client_module, "MAX_XML_BYTES", 10)
        return await original(method, url, **kwargs)

    monkeypatch.setattr(tools.nc, "send", send)
    with pytest.raises(ToolError, match='tasks of calendar "chores" are too large'):
        await tools.list_tasks()


async def test_list_tasks_empty_calendar_data(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    fake.add_calendar("t", "T", ["VTODO"])
    xml = (
        b'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        b"<d:response><d:href>/x/1.ics</d:href><d:propstat><d:prop><c:calendar-data/>"
        b"</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        b"<d:response><d:href>/x/2.ics</d:href><d:propstat><d:prop><d:getetag>1</d:getetag>"
        b"</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        b"</d:multistatus>"
    )
    fake.override = lambda r: httpx.Response(207, content=xml) if r.method == "REPORT" else None
    assert await tools.list_tasks() == {
        "tasks": [],
        "truncated": False,
        "skipped_large_objects": 0,
    }


async def test_calendar_home_missing(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    fake.override = lambda r: httpx.Response(404) if "/calendars/" in str(r.url) else None
    with pytest.raises(ToolError, match="no calendar home"):
        await tools.list_calendars()


async def test_large_calendar_objects_are_skipped(
    tools: NextcloudTools, fake: FakeNextcloud, monkeypatch
) -> None:
    import nextcloud_mcp_server.caldav as caldav_module
    import nextcloud_mcp_server.tools as tools_module

    monkeypatch.setattr(caldav_module, "MAX_OBJECT_CHARS", 400)
    monkeypatch.setattr(tools_module, "OFFLOAD_BYTES", 10)  # also exercise the thread path
    cal = fake.add_calendar("t", "T", ["VTODO"])
    cal.objects["small.ics"] = vtodo("s", "Small")
    cal.objects["big.ics"] = vtodo("b", "Big", "DESCRIPTION:" + "x" * 500)
    result = await tools.list_tasks()
    assert [t["uid"] for t in result["tasks"]] == ["s"]
    assert result["skipped_large_objects"] == 1


async def test_override_only_objects_are_skipped(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    cal = fake.add_calendar("t", "T", ["VTODO"])
    cal.objects["o.ics"] = vtodo("o", "Only an override", "RECURRENCE-ID:20261010T000000Z")
    cal.objects["r.ics"] = vtodo("r", "Series", "RRULE:FREQ=DAILY", "DUE:20261001T090000Z")
    result = await tools.list_tasks()
    assert [(t["uid"], t["due"]) for t in result["tasks"]] == [("r", "2026-10-01T09:00:00Z")]
