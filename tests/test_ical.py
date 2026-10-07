from __future__ import annotations

import time
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from nextcloud_mcp_server.ical import (
    WINDOWS_ZONES,
    Property,
    components,
    parse_date_value,
    parse_int,
    parse_line,
    unescape_text,
    unfold,
)


def test_unfold() -> None:
    text = "SUMMARY:long\r\n  line\r\n\tcontinued\r\nUID:1\n\nX:y\r"
    assert unfold(text) == ["SUMMARY:long linecontinued", "UID:1", "X:y"]
    assert unfold(" orphan continuation\r\nA:b") == ["A:b"]


def test_parse_line_with_params_and_quotes() -> None:
    prop = parse_line('ATTENDEE;CN="Doe; John";ROLE=REQ-PARTICIPANT:mailto:j@x.org')
    assert prop == Property(
        name="ATTENDEE",
        params={"CN": "Doe; John", "ROLE": "REQ-PARTICIPANT"},
        value="mailto:j@x.org",
    )
    assert parse_line('X-A;P="a:b":v') == Property("X-A", {"P": "a:b"}, "v")
    assert parse_line("summary:x").name == "SUMMARY"


@pytest.mark.parametrize("line", ["no colon here", ":value", "BAD NAME:x", "X;=1:y", "X;P:y"])
def test_parse_line_rejects(line: str) -> None:
    assert parse_line(line) is None


def test_unescape_text() -> None:
    assert unescape_text(r"a\nb\Nc\\d\;e\,f") == "a\nb\nc\\d;e,f"
    assert unescape_text("trailing\\") == "trailing\\"
    assert unescape_text(r"keep\x") == r"keep\x"


@pytest.mark.parametrize(
    ("prop", "expected"),
    [
        (Property("DUE", {"VALUE": "DATE"}, "20261010"), date(2026, 10, 10)),
        (Property("DUE", {}, "20261010"), date(2026, 10, 10)),
        (Property("DUE", {}, "20261010T080000Z"), datetime(2026, 10, 10, 8, tzinfo=UTC)),
        (Property("DUE", {}, "20261010T080000"), datetime(2026, 10, 10, 8)),
        (
            Property("DUE", {"TZID": "Asia/Taipei"}, "20261010T080000"),
            datetime(2026, 10, 10, 8, tzinfo=ZoneInfo("Asia/Taipei")),
        ),
        (
            Property("DUE", {"TZID": "W. Europe Standard Time"}, "20261010T080000"),
            datetime(2026, 10, 10, 8, tzinfo=ZoneInfo("Europe/Berlin")),
        ),
        (
            Property("DUE", {"TZID": "Taipei Standard Time"}, "20261010T080000"),
            datetime(2026, 10, 10, 8, tzinfo=ZoneInfo("Asia/Taipei")),
        ),
        (
            Property("DUE", {"TZID": "/mozilla.org/20050126_1/Europe/Berlin"}, "20261010T080000"),
            datetime(2026, 10, 10, 8, tzinfo=ZoneInfo("Europe/Berlin")),
        ),
        (
            Property("DUE", {"TZID": "/Europe/Berlin"}, "20261010T080000"),
            datetime(2026, 10, 10, 8, tzinfo=ZoneInfo("Europe/Berlin")),
        ),
        (
            Property("DUE", {"TZID": "(UTC+08:00) Taipei"}, "20261010T080000"),
            datetime(2026, 10, 10, 8),
        ),
        (
            Property("DUE", {"TZID": "../../etc/passwd"}, "20261010T080000"),
            datetime(2026, 10, 10, 8),
        ),
        (Property("DUE", {"VALUE": "DATE"}, "2026-10-10"), None),
        (Property("DUE", {}, "20261310"), None),
        (Property("DUE", {}, "20261010T250000"), None),
        (Property("DUE", {}, "tomorrow"), None),
    ],
)
def test_parse_date_value(prop: Property, expected: object) -> None:
    assert parse_date_value(prop) == expected


def test_parse_int() -> None:
    assert parse_int(None) is None
    assert parse_int(Property("PRIORITY", {}, " 5 ")) == 5
    assert parse_int(Property("PRIORITY", {}, "high")) is None


def test_components_nesting_and_broken_structure() -> None:
    text = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "BEGIN:VTODO",
            "UID:one",
            "SUMMARY:first",
            "SUMMARY:second summary is ignored",
            "BEGIN:VALARM",
            "SUMMARY:alarm text",
            "TRIGGER:-PT15M",
            "END:VALARM",
            "DUE:20261010",
            "END:VTODO",
            "BEGIN:VEVENT",
            "UID:event",
            "END:VEVENT",
            "BEGIN:VTODO",
            "UID:unterminated",
            "END:VCALENDAR",
            "END:NOTOPEN",
        ]
    )
    found = components(text, "vtodo")
    assert len(found) == 1
    todo = found[0]
    assert todo["UID"].value == "one"
    assert todo["SUMMARY"].value == "first"
    assert "TRIGGER" not in todo
    assert todo["DUE"].value == "20261010"


def test_windows_zone_table_is_valid() -> None:
    for name in WINDOWS_ZONES.values():
        ZoneInfo(name)


def test_unfold_is_linear() -> None:
    # 2 MB of one property folded every 2 characters took ~40 s with quadratic joining.
    text = "DESCRIPTION:" + "\r\n x" * 700_000 + "\r\nUID:1\r\n"
    started = time.perf_counter()
    lines = unfold(text)
    assert time.perf_counter() - started < 2
    assert lines == ["DESCRIPTION:" + "x" * 700_000, "UID:1"]
