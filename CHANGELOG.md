# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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
