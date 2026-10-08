"""Nextcloud HTTP client: one ``httpx.AsyncClient`` per server lifetime, lazily created."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx
from fastmcp.exceptions import ToolError

from . import __version__
from .errors import (
    AUTH_LATCHED_MESSAGE,
    THROTTLED_MESSAGE,
    GatewayDenied,
    NextcloudHTTPError,
    Operation,
    http_error_message,
    status_message,
)
from .paths import encode_path
from .settings import Settings
from .webdav import server_message

logger = logging.getLogger("nextcloud_mcp_server")

USER_AGENT = f"woow-nextcloud-mcp-server/{__version__}"
CONNECT_TIMEOUT = 10.0
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_ERROR_BODY = 64 * 1024

PolicyFactory = Callable[..., Any]
# Everything httpx raises for a failed exchange (StreamError and InvalidURL are not
# HTTPError subclasses).
_HTTPX_ERRORS = (httpx.HTTPError, httpx.StreamError, httpx.InvalidURL)


class BodyTooLarge(ToolError):
    """The response body exceeded the cap given to :meth:`NextcloudClient.send`.

    It is a :class:`ToolError`, so a caller that does not handle it still produces a
    clean tool error; tools that want a more specific message catch it.
    """

    def __init__(self, limit: int, label: str | None = None) -> None:
        target = f' for "{label}"' if label else ""
        super().__init__(
            f"Nextcloud's answer{target} is larger than {limit} bytes and was not processed."
        )
        self.limit = limit


_STATUS_CODE = re.compile(r"^BACKEND_HTTP_ERROR status=(\d{3})$")


def carried_status(exc: BaseException) -> int | None:
    """HTTP status carried by an exception (gateway transports raise for non-2xx).

    Duck-typed: an int ``status`` attribute, or a ``code`` string of the form
    ``BACKEND_HTTP_ERROR status=NNN`` on a wrapped failure.
    """
    status = getattr(exc, "status", None)
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        return status
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        match = _STATUS_CODE.match(code)
        if match:
            return int(match[1])
    return None


def _gateway_code(exc: BaseException) -> str | None:
    name = type(exc).__name__
    if name == "BackendDenied":
        return "BACKEND_DESTINATION_DENIED"
    if name == "BackendBusy":
        return "BACKEND_BUSY"
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.startswith("BACKEND_"):
        return code
    if isinstance(exc, (ValueError, httpx.HTTPError)) and str(exc) in (
        "BACKEND_DESTINATION_DENIED",
        "BACKEND_BUSY",
    ):
        return str(exc)
    return None


def failure_error(exc: BaseException, label: str) -> ToolError:
    """ToolError for a failed exchange. Never includes text from the exception."""
    code = _gateway_code(exc)
    if code == "BACKEND_DESTINATION_DENIED":
        return GatewayDenied(
            f'The gateway refused this request for "{label}" (destination or path not allowed).'
        )
    if code == "BACKEND_BUSY":
        return ToolError("Nextcloud is busy; try again later.")
    if code == "BACKEND_TIMEOUT":
        return ToolError("Could not reach Nextcloud (timed out).")
    if code == "BACKEND_UNAVAILABLE":
        return ToolError("Could not reach Nextcloud (connection failed).")
    if isinstance(exc, _HTTPX_ERRORS):
        return ToolError(http_error_message(exc))
    return ToolError(f"Nextcloud returned an error ({type(exc).__name__}); try again later.")


# Authentication latch (brute-force protection). After a 401 or 429 from Nextcloud no
# further request is sent with the same credentials: every failed Basic-auth login
# counts against the client IP and can get it throttled or banned. The latch is
# process-wide but keyed by (base URL, username, SHA-256 of the app password), so other
# clients in the same process with the same credentials share it, while a client built
# with different (e.g. corrected) credentials is not affected. A 401 stays latched until
# the process restarts; a 429 expires after Retry-After (capped) or a default period.
THROTTLE_DEFAULT_SECONDS = 5 * 60
THROTTLE_MAX_SECONDS = 15 * 60

LatchKey = tuple[str, str, str]

_clock: Callable[[], float] = time.monotonic  # replaced in tests


@dataclass(frozen=True)
class _Latch:
    status: int
    until: float | None  # monotonic deadline; None = until the process restarts


_latches: dict[LatchKey, _Latch] = {}


def latch_key(settings: Settings) -> LatchKey:
    """Credentials identity for the latch. Holds only a hash of the password."""
    parts = urlsplit(settings.base_url)
    base = f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}"
    digest = hashlib.sha256(settings.app_password.get_secret_value().encode()).hexdigest()
    return (base, settings.username, digest)


def _latch_message(status: int) -> str:
    return AUTH_LATCHED_MESSAGE if status == 401 else THROTTLED_MESSAGE


def _latched(key: LatchKey) -> NextcloudHTTPError | None:
    latch = _latches.get(key)
    if latch is None:
        return None
    if latch.until is not None and _clock() >= latch.until:
        del _latches[key]
        return None
    return NextcloudHTTPError(_latch_message(latch.status), latch.status)


def retry_after_seconds(value: str | None) -> float:
    """Seconds to wait after a 429: Retry-After (delta or HTTP date), capped; else default."""
    seconds: float | None = None
    if value:
        value = value.strip()
        if value.isdigit():
            seconds = float(value)
        else:
            try:
                moment = parsedate_to_datetime(value)
            except (TypeError, ValueError, IndexError):
                moment = None
            if moment is not None:
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=UTC)
                seconds = (moment - datetime.now(UTC)).total_seconds()
    if seconds is None:
        return float(THROTTLE_DEFAULT_SECONDS)
    return float(min(max(seconds, 1.0), THROTTLE_MAX_SECONDS))


def _set_latch(key: LatchKey, status: int, retry_after: str | None = None) -> NextcloudHTTPError:
    if status == 429:
        wait = retry_after_seconds(retry_after)
        _latches[key] = _Latch(status, _clock() + wait)
        logger.warning("Nextcloud answered 429; no requests with these credentials for %d s", wait)
    else:
        _latches[key] = _Latch(status, None)
        logger.warning(
            "Nextcloud answered %s; no requests with these credentials until restart", status
        )
    return NextcloudHTTPError(_latch_message(status), status)


def reset_auth_latch() -> None:
    """Clear every authentication latch (for tests; production clears 401 by restarting)."""
    _latches.clear()


class BackendPolicyError(Exception):
    """The gateway's ``backend_policy`` module exists but cannot be used."""


@dataclass
class Reply:
    """A fully read (and size-capped) backend response."""

    status: int
    headers: httpx.Headers
    body: bytes


def _redirect_target(request_url: str, location: str | None) -> str:
    if not location:
        return "an unknown address"
    try:
        target = urlsplit(urljoin(request_url, location))
        host = target.hostname or ""
        port = f":{target.port}" if target.port else ""
    except ValueError:
        return "an unknown address"
    return f"{target.scheme}://{host}{port}{target.path}"


def load_backend_policy() -> PolicyFactory | None:
    """Return ``backend_policy.async_client`` when a gateway ships that module.

    ``None`` when no module named ``backend_policy`` exists. A module that exists but
    fails to import, or has no callable ``async_client``, raises
    :class:`BackendPolicyError`: a gateway that ships a policy must never silently get
    an unrestricted client instead.
    """
    try:
        module = importlib.import_module("backend_policy")
    except ModuleNotFoundError as exc:
        if exc.name == "backend_policy":
            return None
        raise BackendPolicyError(
            f"backend_policy could not be imported ({type(exc).__name__}: missing module "
            f"{exc.name!r})"
        ) from None
    except Exception as exc:
        raise BackendPolicyError(
            f"backend_policy could not be imported ({type(exc).__name__})"
        ) from None
    factory = getattr(module, "async_client", None)
    if not callable(factory):
        raise BackendPolicyError("backend_policy has no callable async_client(*, base_url, ...)")
    return factory


_UNSET: Any = object()


class NextcloudClient:
    """Thin async wrapper around the Nextcloud WebDAV/CalDAV/OCS endpoints of one account."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        policy: PolicyFactory | None = _UNSET,
    ) -> None:
        """``policy`` defaults to :func:`load_backend_policy`, resolved here (at start-up)."""
        self.settings = settings
        self._transport = transport
        self.policy: PolicyFactory | None = load_backend_policy() if policy is _UNSET else policy
        self._verify = settings.tls_verify()
        self.latch_key = latch_key(settings)
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._user_id: str | None = None
        self._user_lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------------------

    def policy_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for ``backend_policy.async_client`` (besides ``base_url``).

        Only what the server must decide: credentials, timeouts and headers. The hook owns
        the transport, TLS verification, ``trust_env`` and ``follow_redirects`` (it sets
        them itself, so passing them would be a duplicate keyword argument).
        """
        settings = self.settings
        return {
            "auth": httpx.BasicAuth(settings.username, settings.app_password.get_secret_value()),
            "timeout": httpx.Timeout(settings.request_timeout, connect=CONNECT_TIMEOUT),
            "headers": {"User-Agent": USER_AGENT},
        }

    def client_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for the plain ``httpx.AsyncClient`` (no ``backend_policy``)."""
        kwargs = self.policy_kwargs()
        kwargs.update(follow_redirects=False, trust_env=False, verify=self._verify)
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return kwargs

    async def http(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it on first use (no request is made)."""
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                if self.policy is not None:
                    client = self.policy(base_url=self.settings.base_url, **self.policy_kwargs())
                    if inspect.isawaitable(client):
                        client = await client
                else:
                    client = httpx.AsyncClient(
                        base_url=self.settings.base_url, **self.client_kwargs()
                    )
                self._client = client
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    # -- requests ------------------------------------------------------------------

    async def send(
        self,
        method: str,
        url: str,
        *,
        label: str,
        op: Operation,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        max_body: int | None = None,
        ok: tuple[int, ...] = (200, 201, 204, 207),
    ) -> Reply:
        """Send one request and return the size-capped body.

        Raises :class:`ToolError` for transport/protocol failures and redirects,
        :class:`NextcloudHTTPError` for statuses outside ``ok`` and :class:`BodyTooLarge`
        (also a ToolError) when the body exceeds ``max_body`` bytes (default
        :data:`MAX_XML_BYTES`).
        """
        latched = _latched(self.latch_key)
        if latched is not None:
            raise latched
        try:
            # Building the client (a gateway hook may refuse the base URL) and the request
            # fail through the same mapping as the exchange itself.
            client = await self.http()
            request = client.build_request(method, url, headers=headers, content=content)
        except Exception as exc:
            raise failure_error(exc, label) from None
        try:
            response = await client.send(request, stream=True)
        except Exception as exc:
            status = carried_status(exc)
            if status is None:
                logger.warning("%s request failed: %s", method, type(exc).__name__)
                raise failure_error(exc, label) from None
            # A gateway transport (backend_policy) raises instead of returning non-2xx
            # answers; treat it exactly like that answer without headers or body.
            response = httpx.Response(status, request=request)
        try:
            logger.debug("%s -> %s", method, response.status_code)
            if 300 <= response.status_code < 400:
                target = _redirect_target(str(request.url), response.headers.get("location"))
                raise ToolError(
                    f"Nextcloud answered with a redirect to {target}; "
                    "set NEXTCLOUD_MCP_BASE_URL to the final address."
                )
            if response.status_code not in ok:
                if response.status_code in (401, 429):
                    raise _set_latch(
                        self.latch_key, response.status_code, response.headers.get("retry-after")
                    )
                body = await self._read_capped(response, MAX_ERROR_BODY, tolerate=True)
                detail = server_message(body) if response.status_code < 500 else None
                raise NextcloudHTTPError(
                    status_message(response.status_code, label, op, detail=detail),
                    response.status_code,
                )
            limit = MAX_XML_BYTES if max_body is None else max_body
            try:
                body = await self._read_capped(response, limit)
            except BodyTooLarge:
                raise BodyTooLarge(limit, label) from None
            return Reply(status=response.status_code, headers=response.headers, body=body)
        except ToolError:
            raise
        except Exception as exc:
            logger.warning("%s response failed: %s", method, type(exc).__name__)
            raise failure_error(exc, label) from None
        finally:
            await response.aclose()

    @staticmethod
    async def _read_capped(
        response: httpx.Response, limit: int, *, tolerate: bool = False
    ) -> bytes:
        length = response.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > limit and not tolerate:
            raise BodyTooLarge(limit)
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > limit:
                if tolerate:
                    break
                raise BodyTooLarge(limit)
            chunks.append(chunk)
        return b"".join(chunks)

    # -- account -------------------------------------------------------------------

    async def user_id(self) -> str:
        """The Nextcloud user id (may differ from the login name), resolved once."""
        if self._user_id is not None:
            return self._user_id
        async with self._user_lock:
            if self._user_id is None:
                self._user_id = await self._fetch_user_id()
        return self._user_id

    async def probe(self) -> dict[str, Any]:
        """Cheap health check for gateways (not an MCP tool).

        Sends one authenticated OCS ``cloud/user`` request (no file or calendar data) and
        returns ``{"ok": True, "user_id": <id>}``; on failure raises the same
        :class:`ToolError` a tool would. It shares the user-id cache and the
        authentication latch: after a 401/429 it fails without contacting Nextcloud.
        """
        user_id = await self._fetch_user_id()
        if self._user_id is None:
            self._user_id = user_id
        return {"ok": True, "user_id": user_id}

    async def _fetch_user_id(self) -> str:
        url = f"{self.settings.base_url}/ocs/v2.php/cloud/user?format=json"
        try:
            reply = await self.send(
                "GET",
                url,
                label="account",
                op="read",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                max_body=1024 * 1024,
                ok=(200,),
            )
        except NextcloudHTTPError as exc:
            if exc.status in (401, 429):
                raise
            raise ToolError(
                f"Could not read the Nextcloud account ({exc.status}); check "
                "NEXTCLOUD_MCP_BASE_URL points at the Nextcloud root."
            ) from None
        except BodyTooLarge:
            raise ToolError("Nextcloud sent an unexpectedly large account answer.") from None
        try:
            data = json.loads(reply.body)
            user_id = data["ocs"]["data"]["id"]
        except (ValueError, KeyError, TypeError):
            user_id = None
        if not isinstance(user_id, str) or not user_id:
            raise ToolError(
                "The address in NEXTCLOUD_MCP_BASE_URL did not answer like a Nextcloud "
                "server; check that it is the Nextcloud root URL."
            )
        return user_id

    # -- URLs ----------------------------------------------------------------------

    async def files_home(self) -> str:
        user = quote(await self.user_id(), safe="")
        return f"{self.settings.base_url}/remote.php/dav/files/{user}/"

    async def calendars_home(self) -> str:
        user = quote(await self.user_id(), safe="")
        return f"{self.settings.base_url}/remote.php/dav/calendars/{user}/"

    async def file_url(self, normalized: str) -> str:
        return await self.files_home() + encode_path(normalized)
