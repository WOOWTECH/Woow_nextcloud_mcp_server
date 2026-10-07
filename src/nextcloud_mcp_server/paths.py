"""User path normalisation, URL encoding and ETag helpers."""

from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit

from fastmcp.exceptions import ToolError

MAX_PATH_LENGTH = 1024


def _has_control(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def normalize_path(path: str) -> str:
    """Normalise a user path (relative to the files home).

    Returns the un-encoded path without a leading slash; ``""`` is the home folder.
    Raises :class:`ToolError` for paths that are ambiguous or unsafe.
    """
    if not isinstance(path, str):
        raise ToolError("path must be a string.")
    if len(path) > MAX_PATH_LENGTH:
        raise ToolError(f"path is longer than {MAX_PATH_LENGTH} characters.")
    if _has_control(path):
        raise ToolError("path must not contain control characters.")
    if "\\" in path:
        raise ToolError("path must use '/' as separator, not backslashes.")
    if path in ("", "/"):
        return ""
    segments = path.split("/")
    if segments and segments[0] == "":
        segments = segments[1:]
    if segments and segments[-1] == "":
        segments = segments[:-1]
    for segment in segments:
        if segment == "":
            raise ToolError(f'path "{path}" contains an empty segment ("//").')
        if segment in (".", ".."):
            raise ToolError(f'path "{path}" must not contain "." or ".." segments.')
    return "/".join(segments)


def encode_path(normalized: str) -> str:
    """Percent-encode every segment of a normalised path; nothing is left unescaped."""
    if normalized == "":
        return ""
    return "/".join(quote(segment, safe="") for segment in normalized.split("/"))


def display_path(normalized: str) -> str:
    """Path as shown in messages: the home folder is shown as ``/``."""
    return normalized if normalized else "/"


def href_to_path(href: str, home_path: str) -> str | None:
    """Turn a ``<d:href>`` into a user path, or ``None`` when it is outside the home.

    ``home_path`` is the (encoded) URL path of the files home, ending with ``/``.
    """
    href_path = unquote(urlsplit(href).path)
    home = unquote(home_path)
    if not home.endswith("/"):
        home += "/"
    if href_path.rstrip("/") == home.rstrip("/"):
        return ""
    if not href_path.startswith(home):
        return None
    return href_path[len(home) :].strip("/")


def normalize_etag(etag: str | None) -> str | None:
    """Return an ETag without ``W/`` prefix and surrounding quotes (``None`` stays ``None``)."""
    if etag is None:
        return None
    value = etag.strip()
    if value.startswith(("W/", "w/")):
        value = value[2:].strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return value or None


def caller_etag(etag: str | None, argument: str = "expected_etag") -> str:
    """Validate an ETag given by the caller and return it normalised."""
    value = normalize_etag(etag)
    if value is None:
        raise ToolError(f"{argument} must not be empty; read the file first to get its etag.")
    if '"' in value or _has_control(value) or len(value) > 256:
        raise ToolError(
            f"{argument} is not a valid etag; copy it exactly as a read tool returned it."
        )
    return value


def quote_etag(etag: str) -> str:
    """Format an ETag for ``If-Match``."""
    return f'"{etag}"'
