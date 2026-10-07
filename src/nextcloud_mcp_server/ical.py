"""A small, tolerant reader for the parts of iCalendar (RFC 5545) that ``list_tasks`` needs.

Only reading is supported: line unfolding (§3.1), content lines with parameters
(§3.1, §3.2), TEXT unescaping (§3.3.11), DATE / DATE-TIME values (§3.3.4, §3.3.5)
and INTEGER values. Broken lines or values are skipped instead of failing the
whole object, because calendars are written by many different clients.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_NAME = re.compile(r"[A-Za-z0-9-]+")
_DATE = re.compile(r"(\d{4})(\d{2})(\d{2})")
_DATE_TIME = re.compile(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)")


@dataclass(frozen=True)
class Property:
    name: str
    params: dict[str, str]
    value: str


def unfold(text: str) -> list[str]:
    """Split into logical lines, joining folded continuation lines (CRLF/LF + space or tab)."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t"):
            if lines:
                lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split_outside_quotes(text: str, separator: str, maxsplit: int = -1) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    for char in text:
        if char == '"':
            quoted = not quoted
        if char == separator and not quoted and (maxsplit < 0 or len(parts) < maxsplit):
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def parse_line(line: str) -> Property | None:
    """Parse ``NAME;PARAM=value:VALUE``; returns ``None`` for lines that cannot be read."""
    quoted = False
    colon = -1
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            colon = index
            break
    if colon < 0:
        return None
    head, value = line[:colon], line[colon + 1 :]
    pieces = _split_outside_quotes(head, ";")
    name = pieces[0].strip()
    if not _NAME.fullmatch(name):
        return None
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        key, sep, raw = piece.partition("=")
        if not sep or not _NAME.fullmatch(key.strip()):
            return None
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            raw = raw[1:-1]
        params[key.strip().upper()] = raw
    return Property(name=name.upper(), params=params, value=value)


def unescape_text(value: str) -> str:
    """Undo TEXT escaping: ``\\n``/``\\N`` newline, ``\\\\``, ``\\;``, ``\\,``."""
    out: list[str] = []
    chars = iter(value)
    for char in chars:
        if char != "\\":
            out.append(char)
            continue
        following = next(chars, "")
        if following in ("n", "N"):
            out.append("\n")
        elif following in ("\\", ";", ",", '"', ":"):
            out.append(following)
        else:
            out.append("\\" + following)
    return "".join(out)


def parse_date_value(prop: Property) -> date | datetime | None:
    """DATE or DATE-TIME (UTC ``Z``, ``TZID`` or floating); ``None`` when unreadable."""
    value = prop.value.strip()
    try:
        if prop.params.get("VALUE", "").upper() == "DATE" or _DATE.fullmatch(value):
            match = _DATE.fullmatch(value)
            if not match:
                return None
            return date(int(match[1]), int(match[2]), int(match[3]))
        match = _DATE_TIME.fullmatch(value)
        if not match:
            return None
        moment = datetime(*(int(match[i]) for i in range(1, 7)))
    except ValueError:
        return None
    if match[7]:
        return moment.replace(tzinfo=UTC)
    tzid = prop.params.get("TZID")
    if tzid:
        try:
            return moment.replace(tzinfo=ZoneInfo(tzid.lstrip("/")))
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return moment  # unknown zone name (e.g. a Windows name): keep it floating
    return moment


def parse_int(prop: Property | None) -> int | None:
    if prop is None:
        return None
    try:
        return int(prop.value.strip())
    except ValueError:
        return None


def components(text: str, wanted: str) -> list[dict[str, Property]]:
    """Properties (first occurrence of each name) of every ``wanted`` component.

    Nested sub-components (for example a VALARM inside a VTODO) do not contribute
    their properties. Components that are never closed are dropped.
    """
    wanted = wanted.upper()
    found: list[dict[str, Property]] = []
    stack: list[str] = []
    current: dict[str, Property] | None = None
    current_depth = -1
    for line in unfold(text):
        prop = parse_line(line)
        if prop is None:
            continue
        if prop.name == "BEGIN":
            stack.append(prop.value.strip().upper())
            if stack[-1] == wanted and current is None:
                current = {}
                current_depth = len(stack)
            continue
        if prop.name == "END":
            name = prop.value.strip().upper()
            if name in stack:
                while stack:
                    closed = stack.pop()
                    if current is not None and len(stack) < current_depth:
                        if closed == wanted and name == wanted:
                            found.append(current)
                        current = None
                        current_depth = -1
                    if closed == name:
                        break
            continue
        if current is not None and len(stack) == current_depth:
            current.setdefault(prop.name, prop)
    return found
