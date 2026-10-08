# Woow Nextcloud MCP Server — behaviour specification

Status: v0.1.0 target. This document is the only design input for the implementation.
It describes observable behaviour (protocol requests, inputs, outputs, errors) and was
written without reproducing code from any other Nextcloud MCP implementation. The
implementation must be written from this specification and the public Nextcloud /
WebDAV / CalDAV / MCP documentation only (see "Clean-room rule" at the end).

## 1. Purpose and shape

A small, standalone MCP server that lets an MCP client (Claude Code, Claude Desktop,
claude.ai via a WOOW gateway) work with **one Nextcloud account**, authenticated with
that account's **app password** (HTTP Basic). It is packaged the same way as WOOW's
other MCP children (EMQX, LiteLLM): a Python package started as a child process by a
WOOW admin gateway, which owns the public endpoint, the bearer/URL token, the admin UI
and the per-tool policy. The server itself has no admin UI, no OAuth and no database.

* Language: Python ≥ 3.11 (CI tests 3.11 and 3.13). Package `nextcloud_mcp_server`,
  distribution `woow-nextcloud-mcp-server`, console script `nextcloud-mcp-server`,
  also runnable as `python -m nextcloud_mcp_server.server`.
* MCP framework: `fastmcp==3.4.5` (with `mcp==1.28.1`), HTTP: `httpx==0.28.1`,
  settings: `pydantic-settings==2.15.0`. XML: `defusedxml` (never parse server XML
  with plain `xml.etree` on untrusted input without it). iCalendar: `icalendar`
  (BSD) or a small own parser. Every runtime dependency must have a permissive
  licence (MIT, BSD, Apache-2.0, PSF, MPL-2.0, ISC). No GPL/LGPL/AGPL anywhere in
  the runtime closure — CI checks this.
* Licence of this repository: MIT, copyright WOOWTECH.

## 2. Process contract

```
nextcloud-mcp-server [--transport stdio|http|sse] [--host 127.0.0.1] [--port 3000] [--path /mcp]
```

* Default transport `stdio` (desktop clients). WOOW gateways start it with
  `--transport http --host 127.0.0.1 --port 3000 --path /mcp` (Streamable HTTP).
* Must not bind to anything but the given host. Must not print secrets in the banner,
  logs, errors or tool results. Respect `FASTMCP_CHECK_FOR_UPDATES=off` and
  `FASTMCP_SHOW_SERVER_BANNER=false` (gateways set them).
* Exit non-zero with a one-line message (no secret) when required settings are
  missing or invalid at start-up. Do **not** contact Nextcloud at start-up (gateways
  start the child before the backend may be reachable); connect lazily.

## 3. Settings (environment, prefix `NEXTCLOUD_MCP_`)

| Variable | Default | Meaning |
|---|---|---|
| `BASE_URL` | — (required) | Nextcloud root URL, e.g. `https://cloud.example.com` or `https://example.com/nextcloud`. `http://` allowed (LAN), trailing slash ignored. No query/fragment/userinfo. |
| `USERNAME` | — (required) | Login name of the account. |
| `APP_PASSWORD` | — (required) | App password (Settings → Security). Treated as a secret everywhere (`SecretStr`). |
| `READONLY` | `true` | When true, only read tools are registered. |
| `ALLOW_DELETE` | `false` | `delete_file_checked` is registered only when `READONLY=false` **and** this is true. |
| `DISABLED_TOOLS` | `""` | Comma-separated tool names never registered. |
| `REQUEST_TIMEOUT` | `30` | Seconds for a single backend request (read timeout). Connect timeout 10 s. |
| `MAX_TEXT_BYTES` | `1048576` | Largest file `read_text_file`/`get_file_content` will return, and largest text body `create_text_file`/`update_text_file` accept (UTF-8 bytes). |
| `MAX_UPLOAD_BYTES` | `10485760` | Largest decoded payload for `upload_file`. |
| `TREE_MAX_ENTRIES` | `500` | Upper bound on entries returned by `get_file_tree`. |
| `VERIFY_TLS` | `true` | TLS certificate verification. |

Also read from an optional `.env` file in the working directory (like the EMQX child),
but environment wins. Unknown variables are ignored.

## 4. HTTP client

* One `httpx.AsyncClient` per server lifetime (lifespan), Basic auth, `follow_redirects=False`,
  `trust_env=False`, a `User-Agent` of `woow-nextcloud-mcp-server/<version>`.
* **Gateway hook:** if a module named `backend_policy` is importable and exposes
  `async_client(*, base_url, **kwargs)`, build the client with it (WOOW's HA gateway
  ships this module to pin DNS and forbid redirects); otherwise build a plain
  `httpx.AsyncClient`. Same keyword arguments either way.
* A 3xx answer is an error: `Nextcloud answered with a redirect to <Location>; set NEXTCLOUD_MCP_BASE_URL to the final address.` (include only scheme+host+path of Location).
* Never log request headers or bodies.

## 5. Account and path resolution

* **User id.** The WebDAV/CalDAV home uses the Nextcloud *user id*, which can differ
  from the login name (e-mail login, LDAP). Resolve it once, lazily, with
  `GET {base}/ocs/v2.php/cloud/user?format=json` and headers `OCS-APIRequest: true`,
  `Accept: application/json`; use `ocs.data.id`. Cache it for the server lifetime.
  On 401: error `Nextcloud rejected the username or app password.`
* **Files home:** `{base}/remote.php/dav/files/{enc(user_id)}/`.
* **Calendars home:** `{base}/remote.php/dav/calendars/{enc(user_id)}/`.
* **User paths** (tool inputs) are relative to the files home. Normalisation:
  - Accept with or without a leading `/`; `""` and `"/"` mean the home folder.
  - Split on `/`; drop empty segments produced by a leading/trailing slash only;
    reject an empty segment in the middle (`a//b`), `.` and `..` segments, NUL and
    other C0 control characters, and backslashes. Max 1024 characters total.
  - Each segment is percent-encoded with **nothing** left unescaped
    (`urllib.parse.quote(segment, safe="")`), so `#`, `?`, `%`, space, `+`, `&`,
    `;` and non-ASCII are always encoded. The returned/echoed path is the
    normalised, *un-encoded* form without a leading slash (e.g. `Docs/a #1.md`).
  - Paths coming back from the server (`<d:href>`) are percent-decoded and the
    files-home prefix is removed to produce user paths.
* Folder operations are out of scope for writes in v0.1 (no mkdir/move/copy).

## 6. Error model

Every failure is raised as `fastmcp.exceptions.ToolError` with a short English
message that tells the model what happened and what to do; the MCP result then has
`isError: true`. Never put credentials, the `Authorization` header or raw server
bodies longer than 200 chars into a message. Mapping:

| Backend status | Message (examples; `<p>` = user path) |
|---|---|
| 401 | `Nextcloud rejected the username or app password.` |
| 403 | `Permission denied for "<p>".` |
| 404 | `"<p>" does not exist.` |
| 405 on PUT/MKCOL | `"<p>" is a folder or cannot be written.` |
| 409 on PUT | `The parent folder of "<p>" does not exist.` |
| 412 (create) | `"<p>" already exists; read it first and use update_text_file with its etag.` |
| 412 (update/delete/upload-replace) | `"<p>" changed since it was read (current etag <e or unknown>); read it again and retry.` |
| 423 | `"<p>" is locked (it may be open in Nextcloud Office or locked by another user); try again later or ask the owner to unlock it.` |
| 413 / 507 | `Not enough storage or file too large for "<p>".` |
| 5xx | `Nextcloud returned an error (<status>); try again later.` |
| network/timeout | `Could not reach Nextcloud (<short reason>).` |

Validation failures (bad path, oversize, not UTF-8, binary file to a text tool,
wrong base64) are ToolErrors too, raised before any request.

ETags: always return them **without** surrounding quotes and without a `W/` prefix;
accept them from the caller with or without quotes; send them quoted
(`If-Match: "abc"`). If the server returns no ETag after a write, do a `PROPFIND`
depth 0 for `getetag` to report it.

## 7. Tools

Tool names are fixed (WOOW gateways and existing client configs use them). Every
tool has a precise JSON schema (no `oneOf`/`anyOf`/`allOf` at the top level, every
property documented, `additionalProperties: false`), and annotations:
`readOnlyHint` for read tools, `destructiveHint` for `delete_file_checked`,
`idempotentHint` where true. Results are JSON objects (structured content) unless
stated. Descriptions are written for an LLM: state what the tool does, the limits,
and which tool to call next.

### Read tools (always registered unless in `DISABLED_TOOLS`)

1. **`get_file_tree`** `{path: str = "", depth: int = 1 (1..3)}`
   `PROPFIND` (Depth 1, repeated per sub-folder up to `depth`) for `resourcetype`,
   `getcontentlength`, `getlastmodified`, `getetag`, `getcontenttype`.
   Returns `{path, entries: [{path, name, type: "file"|"folder", size, modified (ISO 8601 UTC), etag, content_type}], truncated: bool}`
   sorted folders first then by name; the queried folder itself is not an entry.
   Stops at `TREE_MAX_ENTRIES` and sets `truncated`. Path that is a file → return
   that single file as the only entry.

2. **`get_file_content`** `{path: str}` — returns the file's text as a plain string
   result (for clients that just want the text). Same limits and text detection as
   `read_text_file`.

3. **`read_text_file`** `{path: str}` — `GET`. Returns
   `{path, etag, bytes, content_type, content}`. Refuse folders, files larger than
   `MAX_TEXT_BYTES` (check `Content-Length`/`PROPFIND` first and stream with a cap),
   and content that is not valid UTF-8 or contains NUL (`"<p>" is not a UTF-8 text file; use get_file_tree to see its type.`). A UTF-8 BOM is stripped from `content` but counted in `bytes`.

4. **`list_calendars`** `{}` — `PROPFIND` Depth 1 on the calendars home for
   `displayname`, `resourcetype`, `supported-calendar-component-set`,
   `calendar-color` (Apple ns), `current-user-privilege-set`, `getctag`.
   Returns `{calendars: [{id, name, components: ["VEVENT","VTODO",...], color, writable: bool}]}`
   where `id` is the last href segment (decoded). Skip non-calendar collections
   (`inbox`, `outbox`, `trashbin`, subscriptions without calendar resourcetype).

5. **`list_tasks`** `{calendar: str | null = null, include_completed: bool = false, limit: int = 100 (1..500)}`
   For each calendar whose components include `VTODO` (or only the given `id`), a
   CalDAV `REPORT` `calendar-query` filtered to `VTODO` (and, unless
   `include_completed`, excluding `STATUS:COMPLETED`/`CANCELLED` client-side).
   Returns `{tasks: [{calendar, uid, summary, status, due, start, completed, percent_complete, priority, description}], truncated}`;
   `description` truncated to 500 chars; dates ISO 8601 (date-only stays `YYYY-MM-DD`).
   Sort: open first, then due ascending (no due last), then summary. Unknown
   `calendar` id → ToolError listing nothing secret.

### Write tools (registered only when `READONLY=false`)

6. **`create_text_file`** `{path: str, content: str}` — `PUT` with
   `If-None-Match: *`, `Content-Type: text/plain; charset=utf-8` (or
   `text/markdown` for `.md`). Never overwrites. Returns
   `{status: "created", path, etag, bytes}`. 412 → "already exists" message.

7. **`update_text_file`** `{path: str, content: str, expected_etag: str}` —
   `PUT` with `If-Match: "<etag>"`. Returns
   `{status: "updated", path, previous_etag, etag, bytes}`. 412 → "changed since it
   was read", include the current etag from a `PROPFIND` if obtainable.
   `expected_etag` is required and non-empty.

8. **`upload_file`** `{path: str, content_base64: str, expected_etag: str | null = null}` —
   decode (strict base64, size ≤ `MAX_UPLOAD_BYTES`), `PUT` the bytes with a
   content type guessed from the extension (`application/octet-stream` fallback).
   Without `expected_etag`: `If-None-Match: *` (create only). With it: `If-Match`
   (replace only that version). Returns `{status: "created"|"replaced", path, etag, bytes}`.

### Delete tool (registered only when `READONLY=false` and `ALLOW_DELETE=true`)

9. **`delete_file_checked`** `{path: str, expected_etag: str}` — first `PROPFIND`
   depth 0: refuse folders (`"<p>" is a folder; this tool deletes single files only.`).
   Then `DELETE` with `If-Match: "<etag>"`. Returns
   `{status: "deleted", path, etag: <deleted etag>, note: "Moved to the Nextcloud trash bin when the Deleted files app is enabled."}`.

## 8. Server instructions

FastMCP `instructions` (shown to the model), in English, ≤ 12 lines: the account it
acts as is fixed by the administrator; paths are relative to that account's home;
always read a file (to get its etag) before updating or deleting it; never retry a
412 blindly; deletes go to the trash bin; large/binary files are refused by text
tools.

## 9. Tests

* Unit tests with `httpx.MockTransport` (no network): path normalisation including
  `#`, `?`, `%`, spaces, unicode, `..`, `a//b`, control chars; every tool's request
  method/URL/headers/body; every error mapping row; ETag quoting; PROPFIND/REPORT
  XML parsing (namespaced, multistatus with 404 propstat); size caps; BOM; base64;
  gating (`READONLY`, `ALLOW_DELETE`, `DISABLED_TOOLS`); the `backend_policy` hook;
  redirect refusal; no secret in any exception text or log record.
* MCP-level tests: start the FastMCP server in-process and call `tools/list` and
  `tools/call` through an MCP client, checking schemas have no top-level
  combinators and `additionalProperties: false`.
* Integration tests (`tests/integration`, skipped unless `NEXTCLOUD_MCP_IT_BASE_URL`,
  `..._USERNAME`, `..._APP_PASSWORD` are set): full create → read → update → stale
  update refused → upload (create, replace) → delete with stale etag refused →
  delete → read refused; names with `#`, `?`, `%`, space and CJK; tree; calendars
  and tasks against fixtures created by the test (and removed afterwards). They
  only touch a folder named `woow-mcp-it-<random>` that they create and remove.
* `ruff check`, `ruff format --check`, `pytest -q` with coverage ≥ 85 %, and a
  licence check of the runtime dependency closure (deny GPL/LGPL/AGPL/unknown).

## 10. Repository contents

`LICENSE` (MIT), `README.md` (English) and `README.zh-TW.md` (Traditional Chinese):
what it is, settings table, how to create an app password and a dedicated account,
Claude Code / Claude Desktop config examples (stdio), gateway usage; `CHANGELOG.md`;
`SPEC.md` (this file); `PROVENANCE.md` (clean-room statement);
`THIRD_PARTY_NOTICES.md` (runtime deps and licences); `.github/workflows/ci.yml`
(lint, tests on 3.11/3.13, licence check); `pyproject.toml` (hatchling) and
`uv.lock`.

## 11. Authentication latch (added in 0.1.2, keyed in 0.1.3)

* After a `401` or `429` answer (or a gateway exception carrying that status), no further
  request is sent with the same credentials; tools and the probe fail at once with the
  same ToolError (401: "Nextcloud rejected the username or app password. Fix the
  credentials and restart the server; no further login attempts will be made until
  then."; 429: the throttling message).
* The latch is process-wide and keyed by (normalised base URL, username, SHA-256 of the
  app password). The password is never stored or logged, nor is the key. Clients with
  different credentials are not affected.
* 401: latched until the process restarts. 429: expires after `Retry-After` (delta
  seconds or HTTP date, at least 1 s, at most 15 min) or after 5 min without it.
* `NextcloudClient.probe()` (one OCS `cloud/user` request, returns
  `{"ok": true, "user_id": ...}`) shares the latch and is not an MCP tool.

## 12. Known limitations

* **ETag resolution (observed on Nextcloud 35.0.1).** Nextcloud's ETags have about
  one-second resolution for writes to the same file. Two writes to one file within about
  a second may not be told apart: the second write may leave the ETag unchanged, or a
  conditional write with the ETag from before the first write may still be accepted.
  `If-Match` therefore protects against edits made by others more than ~1 s apart, not
  against back-to-back writes within the same second. When `update_text_file` or a
  replacing `upload_file` returns an `etag` equal to the `expected_etag` it was given,
  the result additionally contains
  `note: "Nextcloud did not change the etag; wait a second before the next conditional write to this file."`
  Integration tests wait at least 1.1 s after a write before a conditional write that
  tests stale-etag refusal.

## Clean-room rule

The implementer must not open, copy, paraphrase or port code from: the Nextcloud
Context Agent (`nextcloud/context_agent`), any WOOW overlay of it
(`apps/nextcloud/context-agent-patch/**` in `woow-mcp-server`), or
`cbcoutinho/nextcloud-mcp-server`, all of which are AGPL. Public protocol
documentation (RFC 4918 WebDAV, RFC 4791 CalDAV, RFC 5545 iCalendar, Nextcloud
developer docs for WebDAV/OCS) and permissively licensed libraries are fine.
