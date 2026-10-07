"""Mapping of backend failures to short, model-friendly :class:`ToolError` messages."""

from __future__ import annotations

import ssl
from typing import Literal

import httpx
from fastmcp.exceptions import ToolError

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


class NextcloudHTTPError(ToolError):
    """A non-success HTTP status from Nextcloud, already translated into a message."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def stale_message(label: str, current_etag: str | None) -> str:
    return (
        f'"{label}" changed since it was read (current etag {current_etag or "unknown"}); '
        "read it again and retry."
    )


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
    if status == 403:
        return f'Permission denied for "{label}".'
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
