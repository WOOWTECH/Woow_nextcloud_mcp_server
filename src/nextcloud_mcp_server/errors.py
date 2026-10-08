"""Mapping of backend failures to short, model-friendly :class:`ToolError` messages."""

from __future__ import annotations

import re
import ssl
from typing import Literal

import httpx
from fastmcp.exceptions import ToolError

from .paths import display_etag

Operation = Literal[
    "read",
    "list",
    "create",
    "update",
    "upload_create",
    "upload_replace",
    "delete",
    "calendar",
]

AUTH_MESSAGE = "Nextcloud rejected the username or app password."
AUTH_LATCHED_MESSAGE = (
    AUTH_MESSAGE + " Fix the credentials and restart the server; no further login attempts "
    "will be made until then."
)
THROTTLED_MESSAGE = (
    "Nextcloud is throttling requests from this server (too many failed logins); ask the "
    "Nextcloud administrator to reset brute-force protection for this IP, then restart "
    "the server."
)


# Public error codes ("code mode", see SPEC): prefixed to messages of errors caused by a
# backend answer or a transport problem when code mode is on.
INVALID_RESPONSE = "BACKEND_INVALID_RESPONSE"
TIMEOUT = "BACKEND_TIMEOUT"
UNAVAILABLE = "BACKEND_UNAVAILABLE"
DESTINATION_DENIED = "BACKEND_DESTINATION_DENIED"
BUSY = "BACKEND_BUSY"
ETAG_MISMATCH = "ETAG_MISMATCH"

_PUBLIC_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}(?: status=[1-5]\d\d)?$")


def http_code(status: int) -> str:
    return f"BACKEND_HTTP_ERROR status={int(status)}"


def is_public_code(value: object) -> bool:
    return isinstance(value, str) and bool(_PUBLIC_CODE.match(value))


def with_code(exc: ToolError, code: str) -> ToolError:
    """Attach a public code to a ToolError (shown only in code mode)."""
    exc.code = code  # type: ignore[attr-defined]
    return exc


def coded(message: str, code: str) -> ToolError:
    return with_code(ToolError(message), code)


def render_error(exc: ToolError, code_mode: bool) -> ToolError:
    """The error as the MCP client sees it: ``CODE: text`` in code mode, else unchanged."""
    code = getattr(exc, "code", None)
    if not code_mode or not is_public_code(code):
        return exc
    rendered = ToolError(f"{code}: {exc}")
    rendered.code = code  # type: ignore[attr-defined]
    return rendered


class GatewayDenied(ToolError):
    """The gateway's backend_policy refused the destination or path."""

    code = DESTINATION_DENIED


class NextcloudHTTPError(ToolError):
    """A non-success HTTP status from Nextcloud, already translated into a message."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status
        self.code = http_code(status)


def stale_message(label: str, current_etag: str | None) -> str:
    shown = display_etag(current_etag) or "unknown"
    return f'"{label}" changed since it was read (current etag {shown}); read it again and retry.'


def status_message(
    status: int,
    label: str,
    op: Operation,
    *,
    current_etag: str | None = None,
    detail: str | None = None,
) -> str:
    """Translate a backend HTTP status into the message of the error table (SPEC §6)."""
    if status == 401:
        return AUTH_MESSAGE
    if status == 429:
        return THROTTLED_MESSAGE
    if status == 403:
        return f'Permission denied for "{label}".'
    if status == 404 and op in ("create", "upload_create"):
        # Nextcloud (verified on 35) answers 404, not 409, when the parent folder is missing.
        return f'The parent folder of "{label}" does not exist.'
    if status == 404:
        return f'"{label}" does not exist.'
    if status == 405 and op in ("create", "update", "upload_create", "upload_replace"):
        return f'"{label}" is a folder or cannot be written.'
    if status == 409 and op in ("create", "update", "upload_create", "upload_replace"):
        return f'The parent folder of "{label}" does not exist.'
    if status == 412:
        if op == "create":
            return (
                f'"{label}" already exists; read it first and use update_text_file with its etag.'
            )
        if op == "upload_create":
            return (
                f'"{label}" already exists; get its etag (get_file_tree) and pass it as '
                "expected_etag to replace it."
            )
        return stale_message(label, current_etag)
    if status == 423:
        return (
            f'"{label}" is locked (it may be open in Nextcloud Office or locked by another '
            "user); try again later or ask the owner to unlock it."
        )
    if status in (413, 507):
        return f'Not enough storage or file too large for "{label}".'
    if status >= 500:
        return f"Nextcloud returned an error ({status}); try again later."
    suffix = f": {detail}" if detail else ""
    return f'Nextcloud refused the request for "{label}" ({status}){suffix}.'


def network_message(exc: httpx.TransportError) -> str:
    """Short reason for a transport failure (no URL, no headers)."""
    if isinstance(exc, httpx.TimeoutException):
        reason = "timed out"
    elif isinstance(exc, httpx.ConnectError):
        cause = exc.__cause__ or exc.__context__
        text = str(exc)
        if isinstance(cause, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text:
            reason = "TLS certificate verification failed"
        elif isinstance(cause, ssl.SSLError) or "SSL" in text:
            reason = "TLS error"
        else:
            reason = "connection failed"
    elif isinstance(exc, httpx.RemoteProtocolError):
        reason = "the connection was closed unexpectedly"
    else:
        reason = f"network error {type(exc).__name__}"
    return f"Could not reach Nextcloud ({reason})."


def failure_code(exc: BaseException) -> str:
    """Public code for an exception that is not an HTTP status answer."""
    if isinstance(exc, httpx.TimeoutException | TimeoutError):
        return TIMEOUT
    if isinstance(exc, httpx.TransportError):
        return UNAVAILABLE
    if isinstance(exc, httpx.HTTPError | httpx.StreamError | UnicodeError | ValueError):
        return INVALID_RESPONSE
    return UNAVAILABLE


def http_error_message(exc: Exception) -> str:
    """Message for any httpx failure: transport problems or an unreadable answer."""
    if isinstance(exc, httpx.TransportError):
        return network_message(exc)
    return f"Nextcloud returned an error ({type(exc).__name__}); try again later."
