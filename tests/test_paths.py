from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from nextcloud_mcp_server.paths import (
    MAX_PATH_LENGTH,
    caller_etag,
    display_path,
    encode_path,
    href_to_path,
    normalize_etag,
    normalize_path,
    quote_etag,
)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("", ""),
        ("/", ""),
        ("Docs", "Docs"),
        ("/Docs", "Docs"),
        ("Docs/", "Docs"),
        ("/Docs/a #1.md", "Docs/a #1.md"),
        ("what?.txt", "what?.txt"),
        ("100%.txt", "100%.txt"),
        ("a+b&c;d.txt", "a+b&c;d.txt"),
        ("報告/會議 紀錄.md", "報告/會議 紀錄.md"),
        ("  spaced  ", "  spaced  "),
        ("...", "..."),
        (".hidden", ".hidden"),
    ],
)
def test_normalize_accepts(raw: str, normalized: str) -> None:
    assert normalize_path(raw) == normalized


@pytest.mark.parametrize(
    "raw",
    [
        "a//b",
        "//a",
        "a//",
        "..",
        "../etc/passwd",
        "a/../b",
        "./a",
        "a/./b",
        "a\x00b",
        "a\nb",
        "a\tb",
        "a\x7fb",
        "a\\b",
        "x" * (MAX_PATH_LENGTH + 1),
    ],
)
def test_normalize_rejects(raw: str) -> None:
    with pytest.raises(ToolError):
        normalize_path(raw)


def test_normalize_rejects_non_string() -> None:
    with pytest.raises(ToolError):
        normalize_path(5)  # type: ignore[arg-type]


def test_max_length_is_inclusive() -> None:
    assert normalize_path("x" * MAX_PATH_LENGTH) == "x" * MAX_PATH_LENGTH


@pytest.mark.parametrize(
    ("normalized", "encoded"),
    [
        ("", ""),
        ("Docs/a #1.md", "Docs/a%20%231.md"),
        ("what?.txt", "what%3F.txt"),
        ("100%.txt", "100%25.txt"),
        ("a+b&c;d=e.txt", "a%2Bb%26c%3Bd%3De.txt"),
        ("報告.md", "%E5%A0%B1%E5%91%8A.md"),
        ("a:b@c~d", "a%3Ab%40c~d"),
    ],
)
def test_encode_path(normalized: str, encoded: str) -> None:
    assert encode_path(normalized) == encoded


def test_display_path() -> None:
    assert display_path("") == "/"
    assert display_path("a/b") == "a/b"


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("/nc/remote.php/dav/files/alice%20smith/", ""),
        ("/nc/remote.php/dav/files/alice%20smith", ""),
        ("/nc/remote.php/dav/files/alice%20smith/Docs/", "Docs"),
        ("/nc/remote.php/dav/files/alice%20smith/Docs/a%20%231.md", "Docs/a #1.md"),
        ("https://cloud.example.com/nc/remote.php/dav/files/alice%20smith/x%3F.txt", "x?.txt"),
        ("/nc/remote.php/dav/files/alice smith/raw name.txt", "raw name.txt"),
        ("/nc/remote.php/dav/files/bob/x.txt", None),
    ],
)
def test_href_to_path(href: str, expected: str | None) -> None:
    assert href_to_path(href, "/nc/remote.php/dav/files/alice%20smith/") == expected


def test_href_to_path_home_without_slash() -> None:
    assert href_to_path("/d/files/u/a", "/d/files/u") == "a"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ('"abc"', "abc"),
        ("abc", "abc"),
        ('W/"abc"', "abc"),
        (' "abc" ', "abc"),
        ('""', None),
        ("", None),
    ],
)
def test_normalize_etag(raw: str | None, expected: str | None) -> None:
    assert normalize_etag(raw) == expected


def test_caller_etag() -> None:
    assert caller_etag('"abc"') == "abc"
    assert caller_etag('W/"abc"') == "abc"
    for bad in ("", '""', "  "):
        with pytest.raises(ToolError, match="must not be empty"):
            caller_etag(bad)
    for bad in ('a"b', "a\nb", "x" * 300):
        with pytest.raises(ToolError, match="not a valid etag"):
            caller_etag(bad)


def test_quote_etag() -> None:
    assert quote_etag("abc") == '"abc"'
