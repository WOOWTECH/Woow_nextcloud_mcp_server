from __future__ import annotations

from fake_nextcloud import vtodo
from nextcloud_mcp_server.caldav import (
    calendar_entry,
    href_last_segment,
    is_done,
    parse_tasks,
    sort_tasks,
)
from nextcloud_mcp_server.webdav import parse_multistatus

CALENDARS = b"""<?xml version="1.0"?>
<multistatus xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"
             xmlns:A="http://apple.com/ns/ical/">
 <response><href>/dav/calendars/u/</href>
  <propstat><prop><resourcetype><collection/></resourcetype></prop>
  <status>HTTP/1.1 200 OK</status></propstat></response>
 <response><href>/dav/calendars/u/work%20tasks/</href>
  <propstat><prop>
   <displayname>Work</displayname>
   <resourcetype><collection/><C:calendar/></resourcetype>
   <C:supported-calendar-component-set><C:comp name="vtodo"/><C:comp name="VEVENT"/>
   </C:supported-calendar-component-set>
   <A:calendar-color>#FF0000FF</A:calendar-color>
   <current-user-privilege-set><privilege><read/></privilege>
     <privilege><write-content/></privilege></current-user-privilege-set>
  </prop><status>HTTP/1.1 200 OK</status></propstat></response>
 <response><href>/dav/calendars/u/plain/</href>
  <propstat><prop>
   <resourcetype><collection/><C:calendar/></resourcetype>
   <current-user-privilege-set><privilege><read/></privilege></current-user-privilege-set>
  </prop><status>HTTP/1.1 200 OK</status></propstat></response>
 <response><href>/dav/calendars/u/owner/</href>
  <propstat><prop>
   <resourcetype><collection/><C:calendar/></resourcetype>
   <C:supported-calendar-component-set><C:comp/></C:supported-calendar-component-set>
   <current-user-privilege-set><privilege><all/></privilege></current-user-privilege-set>
  </prop><status>HTTP/1.1 200 OK</status></propstat></response>
</multistatus>"""


def test_calendar_entries() -> None:
    items = parse_multistatus(CALENDARS)
    entries = [calendar_entry(i) for i in items]
    assert entries[0] is None  # the calendar home itself
    assert entries[1] == {
        "id": "work tasks",
        "name": "Work",
        "components": ["VEVENT", "VTODO"],
        "color": "#FF0000FF",
        "writable": True,
    }
    # no component set: every component type is accepted (RFC 4791 5.2.3)
    assert entries[2] == {
        "id": "plain",
        "name": "plain",
        "components": ["VEVENT", "VJOURNAL", "VTODO"],
        "color": None,
        "writable": False,
    }
    assert entries[3]["components"] == []
    assert entries[3]["writable"] is True


def test_href_last_segment() -> None:
    assert href_last_segment("/x/calendars/u/a%20b/") == "a b"
    assert href_last_segment("https://h/x/calendars/u/c") == "c"


def test_parse_task_fields() -> None:
    ics = vtodo(
        "uid-1",
        "Buy milk\\, eggs",
        "STATUS:needs-action",
        "DUE;VALUE=DATE:20261010",
        "DTSTART;TZID=Europe/Berlin:20261009T100000",
        "COMPLETED:20261009T120000Z",
        "PERCENT-COMPLETE:50",
        "PRIORITY:1",
        "DESCRIPTION:line1\\nline2",
    )
    (task,) = parse_tasks("cal", ics)
    assert task["calendar"] == "cal"
    assert task["uid"] == "uid-1"
    assert task["summary"] == "Buy milk, eggs"
    assert task["status"] == "NEEDS-ACTION"
    assert task["due"] == "2026-10-10"
    assert task["start"] == "2026-10-09T10:00:00+02:00"
    assert task["completed"] == "2026-10-09T12:00:00Z"
    assert task["percent_complete"] == 50
    assert task["priority"] == 1
    assert task["description"] == "line1\nline2"


def test_parse_task_floating_and_missing_values() -> None:
    (task,) = parse_tasks("c", vtodo("u", "S", "DUE:20261010T080000", "PRIORITY:high"))
    assert task["due"] == "2026-10-10T08:00:00"
    assert task["priority"] is None
    assert task["status"] is None
    assert task["description"] is None


def test_description_is_truncated() -> None:
    (task,) = parse_tasks("c", vtodo("u", "S", "DESCRIPTION:" + "x" * 900))
    assert len(task["description"]) == 500
    assert task["description"].endswith("…")


def test_bad_ics_is_tolerated() -> None:
    assert parse_tasks("c", "this is not ical") == []
    (task,) = parse_tasks("c", vtodo("u", "S", "X-BAD;=:", "PRIORITY:high", "DUE:garbage"))
    assert task["summary"] == "S"
    assert task["priority"] is None
    assert task["due"] is None


def test_recurrence_overrides_are_skipped() -> None:
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n"
        "BEGIN:VTODO\r\nUID:r\r\nSUMMARY:master\r\nRRULE:FREQ=DAILY\r\nEND:VTODO\r\n"
        "BEGIN:VTODO\r\nUID:r\r\nSUMMARY:override\r\nRECURRENCE-ID:20261010T000000Z\r\n"
        "END:VTODO\r\nEND:VCALENDAR\r\n"
    )
    assert [t["summary"] for t in parse_tasks("c", ics)] == ["master"]


def test_done_and_sorting() -> None:
    def task(summary: str, status: str | None, due: float | None, completed: str | None = None):
        return {
            "summary": summary,
            "status": status,
            "completed": completed,
            "uid": summary,
            "_due_sort": due,
        }

    tasks = [
        task("z-no-due", None, None),
        task("done", "COMPLETED", 1.0),
        task("cancelled", "CANCELLED", 1.0),
        task("b-later", "NEEDS-ACTION", 20.0),
        task("a-later", "IN-PROCESS", 20.0),
        task("soon", None, 10.0),
        task("implicitly-done", None, 5.0, completed="2026-01-01"),
        task("A-no-due", None, None),
    ]
    assert is_done(tasks[1]) and is_done(tasks[2]) and is_done(tasks[6])
    assert not is_done(tasks[0])
    order = [t["summary"] for t in sort_tasks(tasks)]
    assert order == [
        "soon",
        "a-later",
        "b-later",
        "A-no-due",
        "z-no-due",
        "cancelled",
        "done",
        "implicitly-done",
    ]
