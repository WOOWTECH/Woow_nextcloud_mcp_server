# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## 0.1.5 - 2026-10-08

### Fixed

- Five malformed answers escaped as non-ToolError exceptions (masked by FastMCP as
  "Error calling tool", without a code). Now, at the point of failure:
  - an XML declaration with an unknown encoding (`x-bogus`, `rot13`) is invalid XML;
  - an href that cannot be parsed (`http://[bad/x`) is skipped like an href outside the
    home; the rest of the listing is returned;
  - an unrepresentable `getlastmodified` (year 9999 with a negative offset) gives
    `modified: null`;
  - the account lookup rejects absurdly nested JSON and a user id that is not valid
    UTF-8, contains surrogates or control characters, or exceeds 255 characters.
- Belt: any other unexpected exception in a tool or in `probe()` becomes
  `Nextcloud sent an answer that could not be processed.` (prefixed with
  `BACKEND_INVALID_RESPONSE: ` in code mode); only its type is logged. Cancellation still
  propagates.

## 0.1.4 - 2026-10-08

### Added

- Code mode: with `NEXTCLOUD_MCP_ERROR_CODES` (`auto` = on when the `backend_policy` hook
  is in use, or `true`/`false`), backend and transport errors start with a public code:
  `BACKEND_HTTP_ERROR status=N`, `BACKEND_INVALID_RESPONSE`, `BACKEND_TIMEOUT`,
  `BACKEND_UNAVAILABLE`, or the hook's own `public_backend_error(exc)` code;
  `ETAG_MISMATCH` for `delete_file_checked`'s local etag check. Input validation errors
  carry no code; without code mode messages are unchanged. `probe()` uses the same codes.
- `list_tasks` without `calendar` skips calendars the gateway refuses (like 403/404) and
  reports the count in the optional `skipped_calendars`.

### Fixed

- iCalendar parsing tracks open components with a counter (an END for a name that is not
  open is O(1)); objects nested deeper than 32 levels are skipped and counted in
  `skipped_large_objects`. A 40,000-level object no longer takes seconds.
- Etags echoed in messages must be RFC 9110 `etagc` ASCII (<= 256 characters), else
  `unknown`.
- An exception while closing a response no longer replaces the result or the original
  error.
- Tests: the fake server can keep the ETag on a replace, exercising the "did not change
  the etag" note.

## 0.1.3 - 2026-10-08

### Changed

- The authentication latch is keyed by credentials: (normalised base URL, username,
  SHA-256 of the app password). It stays process-wide, so clients with the same
  credentials share it and a long-lived process never retries a known-bad login, but a
  client built with different credentials (for example after the operator saves the
  corrected app password in an admin shell that keeps running) is no longer blocked.
  Only the password hash is kept; the key is never logged.
- A 429 latch expires after `Retry-After` (seconds or HTTP date, capped at 15 minutes),
  or after 5 minutes when the server sent none. A 401 latch still lasts until restart.

### Tests

- The calendar integration test reuses one calendar `woow-mcp-it` per account (created
  only when missing) and adds/deletes only its own uniquely named VTODO/VEVENT objects:
  Nextcloud rate-limits calendar creation per user (about 10 per hour by default), which
  made repeated runs fail with 429. If creating the calendar is rate-limited the test is
  skipped with that reason.

## 0.1.2 - 2026-10-08

### Added

- Authentication latch (brute-force protection): after the first `401` or `429` from
  Nextcloud, every tool call and probe fails at once with the same message and nothing
  more is sent until the process restarts. The 401 message now says to fix the
  credentials and restart; the 429 message explains the throttling (no BASE_URL hint).
- `NextcloudClient.probe()` (also reachable as `server.nextcloud_client.probe()`): one
  OCS `cloud/user` request returning `{"ok": True, "user_id": ...}` for gateway health
  checks. It is not an MCP tool.
- README: brute-force protection notes (stop before revoking the app password,
  `occ security:bruteforce:reset <ip>`, whitelisting the gateway IP).

### Fixed

- Errors raised while building the HTTP client (for example a gateway hook refusing the
  base URL) go through the normal error mapping for tools and the probe.
- `get_file_tree` with depth 2-3 skips a sub-folder the gateway refuses (e.g. a name
  with `%`), sets `truncated=true` and lists the rest, instead of failing completely.
- Tests: the fake server answers 428 to an unconditional PUT over an existing file, and
  a test proves `upload_file` without `expected_etag` never overwrites.
- Integration tests wait 1.1 s after a write before a conditional write that tests
  stale-etag refusal: Nextcloud's ETags have about one-second resolution for writes to the
  same file, which made the stale-upload test flaky.

### Known limitations

- Two writes to the same file within about one second may not be told apart by
  Nextcloud (the ETag may stay the same, or the old ETag may still match), so `If-Match`
  protects against edits by others more than ~1 s apart. `update_text_file` and a
  replacing `upload_file` now add a `note` to the result when the returned etag equals
  the `expected_etag` sent. Documented in README (en/zh-TW) and SPEC.md ("Known limitations").

## 0.1.1 - 2026-10-08

### Fixed

- `backend_policy.async_client` is now called with only `base_url`, `auth`, `timeout` and
  `headers`. 0.1.0 also passed `follow_redirects`, `trust_env`, `verify` (and a test
  transport), which a real gateway hook sets itself, so the first tool call failed with
  `TypeError: got multiple values for keyword argument`. The hook owns the transport,
  TLS verification, `trust_env` and `follow_redirects`.
- `NEXTCLOUD_MCP_VERIFY_TLS` / `NEXTCLOUD_MCP_CA_BUNDLE` are documented as ignored under
  the hook, and a start-up warning is logged when they are set while the hook is active.
- Gateway transports raise instead of returning non-2xx answers. An exception with an
  integer `status` (or a `code` of the form `BACKEND_HTTP_ERROR status=N`) is now treated
  exactly like an answer with that status and no headers or body, so the full error table
  applies again (412 "already exists"/"changed since it was read" with the follow-up
  PROPFIND, 404 parent folder on create, 423 locked, 3xx redirect). Before, every such
  failure read "Nextcloud returned an error (BackendHTTPError)".
- A gateway denial (`BackendDenied` / `BACKEND_DESTINATION_DENIED`, e.g. for paths
  containing `%`) reads `The gateway refused this request for "<p>" (destination or path
  not allowed).`; `BACKEND_BUSY` reads `Nextcloud is busy; try again later.` Gateway
  exception text is never included in messages.

## 0.1.0 - 2026-10-07

### Added

- First release, implemented from [SPEC.md](SPEC.md).
- Read tools: `get_file_tree`, `get_file_content`, `read_text_file`, `list_calendars`,
  `list_tasks`.
- Write tools (only with `NEXTCLOUD_MCP_READONLY=false`): `create_text_file`,
  `update_text_file`, `upload_file`; all guarded by `If-None-Match: *` / `If-Match`.
- Delete tool (only with `READONLY=false` and `ALLOW_DELETE=true`): `delete_file_checked`,
  single files only, etag checked, moved to the trash bin.
- `NEXTCLOUD_MCP_DISABLED_TOOLS` to hide individual tools.
- Settings from `NEXTCLOUD_MCP_*` environment variables and an optional `.env` file;
  one-line start-up errors that never contain secret values.
- Lazy connection: the Nextcloud user id is resolved on first use (OCS `cloud/user`).
- One `httpx.AsyncClient` per server lifetime, redirects refused, optional
  `backend_policy.async_client` hook for gateways.
- Transports: stdio (default), Streamable HTTP and SSE.
- Small built-in iCalendar (RFC 5545) reader for tasks; XML parsed with `defusedxml`.
- Unit, MCP-level and (opt-in) integration tests; licence check of the runtime
  dependency closure; GitHub Actions CI on Python 3.11 and 3.13.

### Changed before release (review hardening)

- `http`/`sse` transports check `Host` and `Origin` (DNS-rebinding protection): foreign
  hosts get 421, foreign browser origins 403. New `NEXTCLOUD_MCP_ALLOWED_HOSTS` for
  proxies that forward the public host name. FastMCP 3.4.5 does not guard its SSE app, so
  the same middleware is added explicitly.
- `backend_policy` is resolved at start-up; a module that fails to import or lacks a
  callable `async_client` stops the server (exit 2) instead of falling back silently.
- Unknown names in `NEXTCLOUD_MCP_DISABLED_TOOLS` stop the server (exit 2).
- New `NEXTCLOUD_MCP_CA_BUNDLE`; start-up warnings for `VERIFY_TLS=false` and `http://`.
- The FastMCP banner and update check default to off.
- iCalendar unfolding is linear (it was quadratic); calendar objects over 1 MiB are skipped
  and counted in `skipped_large_objects`; large answers are parsed in a worker thread;
  the WebDAV/CalDAV answer cap is now 8 MiB.
- Server paths are re-validated like user paths (unsafe entries dropped, never followed);
  responses with a non-2xx status are ignored; a listing whose paths all lie outside the
  account's WebDAV folder reports a webroot mismatch.
- User paths reject C1 controls and bidirectional override/isolate characters; etags must
  be ASCII `etagc`.
- A 412 on update/replace/delete of a file that no longer exists says so.
- `update_text_file` and `upload_file` are marked `destructiveHint`.
- Every httpx error (also decoding/stream errors) becomes a tool error; oversized answers
  to writes are tool errors too.
- 404 on a create means a missing parent folder (what Nextcloud 35 answers).
- `tzdata` is an unconditional dependency; common Windows/Outlook `TZID` names and
  prefixed IANA paths are mapped. RRULE expansion is documented as not supported.
- Licence check: GPL-family classifiers or wording next to a permissive licence fail
  unless allow-listed by hand; CI checks THIRD_PARTY_NOTICES.md against `uv.lock`; CI
  actions pinned to commit SHAs without persisted credentials; hatchling pinned.
