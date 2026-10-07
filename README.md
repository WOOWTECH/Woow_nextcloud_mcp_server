# Woow Nextcloud MCP Server

English | [繁體中文](README.zh-TW.md)

A small, standalone [Model Context Protocol](https://modelcontextprotocol.io) server that
lets an MCP client (Claude Code, Claude Desktop, or claude.ai through a WOOW gateway) work
with **one Nextcloud account**: browse and read files, create and update text files,
upload files, delete single files, and read calendars and tasks.

* One account per process, authenticated with that account's **app password** (HTTP Basic).
* Read-only by default; write and delete tools must be switched on explicitly.
* Every change is guarded by ETags: nothing is overwritten or deleted unless it is still
  the version the model read.
* No admin UI, no OAuth, no database. It does not contact Nextcloud until the first tool
  call, so it can start before the backend is reachable.
* MIT licensed. Every runtime dependency is permissively licensed (checked in CI).

The behaviour is specified in [SPEC.md](SPEC.md).

## Tools

| Tool | Registered when | What it does |
|---|---|---|
| `get_file_tree` | always | List files and folders (1-3 levels, folders first, capped). |
| `get_file_content` | always | Text of one UTF-8 file as plain text. |
| `read_text_file` | always | Text of one UTF-8 file with `path`, `etag`, `bytes`, `content_type`. |
| `list_calendars` | always | Calendars with id, name, components, colour, writable. |
| `list_tasks` | always | Tasks (VTODO), open first, then by due date. |
| `create_text_file` | `READONLY=false` | Create a new text file; never overwrites. |
| `update_text_file` | `READONLY=false` | Replace a text file, only if `expected_etag` still matches. |
| `upload_file` | `READONLY=false` | Upload base64 bytes: create only, or replace a given etag. |
| `delete_file_checked` | `READONLY=false` and `ALLOW_DELETE=true` | Delete one file (not a folder) if the etag matches; it goes to the trash bin. |

Any tool can additionally be hidden with `NEXTCLOUD_MCP_DISABLED_TOOLS`. Folder creation,
moves and copies are out of scope in 0.1. Paths are relative to the account's home folder
(`""` or `/` is the home) and are never percent-encoded by the caller.

## Settings

Environment variables (an optional `.env` file in the working directory is read too; the
environment wins):

| Variable | Default | Meaning |
|---|---|---|
| `NEXTCLOUD_MCP_BASE_URL` | required | Nextcloud root URL, e.g. `https://cloud.example.com` or `https://example.com/nextcloud`. `http://` is allowed for LAN servers. No user info, query or fragment. |
| `NEXTCLOUD_MCP_USERNAME` | required | Login name of the account. |
| `NEXTCLOUD_MCP_APP_PASSWORD` | required | App password of the account (treated as a secret everywhere). |
| `NEXTCLOUD_MCP_READONLY` | `true` | When true, only the read tools are registered. |
| `NEXTCLOUD_MCP_ALLOW_DELETE` | `false` | Registers `delete_file_checked` (only when `READONLY=false`). |
| `NEXTCLOUD_MCP_DISABLED_TOOLS` | empty | Comma-separated tool names that are never registered. |
| `NEXTCLOUD_MCP_REQUEST_TIMEOUT` | `30` | Read timeout per backend request, seconds (connect timeout is 10 s). |
| `NEXTCLOUD_MCP_MAX_TEXT_BYTES` | `1048576` | Largest file the text tools return, and largest text they write. |
| `NEXTCLOUD_MCP_MAX_UPLOAD_BYTES` | `10485760` | Largest decoded `upload_file` payload. |
| `NEXTCLOUD_MCP_TREE_MAX_ENTRIES` | `500` | Maximum entries returned by `get_file_tree`. |
| `NEXTCLOUD_MCP_VERIFY_TLS` | `true` | TLS certificate verification. |

If a required setting is missing or invalid the server exits with status 2 and a one-line
message naming the variable (never its value).

## Create a dedicated account and an app password

We recommend a **dedicated Nextcloud user** for the assistant instead of your personal
account: an app password gives access to everything that user can see, and Nextcloud app
passwords cannot be limited to single folders.

1. As an administrator open *Users* (avatar menu → *Accounts*/*Users*) and create a user,
   e.g. `assistant`. Give it a quota if you like.
2. Share with that user only the folders and calendars it should work with (with
   read-only shares where writing is not needed).
3. Log in as that user, open *Personal settings → Security*, enter a name such as
   `MCP server` under *Devices & sessions* and press *Create new app password*.
4. Copy the password shown once; it is the `NEXTCLOUD_MCP_APP_PASSWORD`. You can revoke it
   in the same place at any time. App passwords also work when two-factor authentication
   is enabled.

`NEXTCLOUD_MCP_USERNAME` is the login name. If the login is an e-mail address or comes
from LDAP, the server looks up the real user id once (OCS `cloud/user`) and uses it for
WebDAV/CalDAV.

## Installation

The server needs Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/) from a clone
of this repository:

```sh
uv tool install .            # installs the `nextcloud-mcp-server` command
nextcloud-mcp-server --help
```

or run it without installing: `uvx --from /path/to/Woow_nextcloud_mcp_server nextcloud-mcp-server`.
It can also be started as `python -m nextcloud_mcp_server.server`.

```
nextcloud-mcp-server [--transport stdio|http|sse] [--host 127.0.0.1] [--port 3000] [--path /mcp]
```

The default transport is `stdio`.

## Claude Code

```sh
claude mcp add nextcloud \
  -e NEXTCLOUD_MCP_BASE_URL=https://cloud.example.com \
  -e NEXTCLOUD_MCP_USERNAME=assistant \
  -e NEXTCLOUD_MCP_APP_PASSWORD=xxxxx-xxxxx-xxxxx-xxxxx-xxxxx \
  -e NEXTCLOUD_MCP_READONLY=false \
  -- nextcloud-mcp-server
```

or, in a project's `.mcp.json`:

```json
{
  "mcpServers": {
    "nextcloud": {
      "command": "nextcloud-mcp-server",
      "env": {
        "NEXTCLOUD_MCP_BASE_URL": "https://cloud.example.com",
        "NEXTCLOUD_MCP_USERNAME": "assistant",
        "NEXTCLOUD_MCP_APP_PASSWORD": "xxxxx-xxxxx-xxxxx-xxxxx-xxxxx"
      }
    }
  }
}
```

Do not commit a file that contains the app password.

## Claude Desktop

Add the server to `claude_desktop_config.json` (*Settings → Developer → Edit config*):

```json
{
  "mcpServers": {
    "nextcloud": {
      "command": "nextcloud-mcp-server",
      "env": {
        "NEXTCLOUD_MCP_BASE_URL": "https://cloud.example.com",
        "NEXTCLOUD_MCP_USERNAME": "assistant",
        "NEXTCLOUD_MCP_APP_PASSWORD": "xxxxx-xxxxx-xxxxx-xxxxx-xxxxx",
        "NEXTCLOUD_MCP_READONLY": "false",
        "NEXTCLOUD_MCP_ALLOW_DELETE": "false"
      }
    }
  }
}
```

If the command is not on the `PATH` Claude Desktop sees, use its absolute path (for
example the output of `which nextcloud-mcp-server`).

## Behind a WOOW gateway

WOOW admin gateways start the server as a child process and own the public endpoint, the
bearer/URL token, the admin UI and the per-tool policy:

```sh
FASTMCP_SHOW_SERVER_BANNER=false FASTMCP_CHECK_FOR_UPDATES=off \
  nextcloud-mcp-server --transport http --host 127.0.0.1 --port 3000 --path /mcp
```

The server binds only to the given host and has no authentication of its own, so keep it
on `127.0.0.1` behind the gateway.

**`backend_policy` hook.** If a Python module named `backend_policy` is importable and has
a callable `async_client(*, base_url, **kwargs)`, the server builds its single
`httpx.AsyncClient` with it (it receives the same keyword arguments the server would use:
Basic auth, `follow_redirects=False`, `trust_env=False`, timeouts, TLS verification and
the `User-Agent`). Gateways use this to pin DNS and forbid redirects. Without the module a
plain `httpx.AsyncClient` is used. A module that exists but fails to import is reported as
an error rather than silently ignored.

## Behaviour notes

* **ETags** are returned without quotes or `W/` prefix and may be passed back with or
  without quotes. Writes send `If-Match: "<etag>"` (or `If-None-Match: *` for creation).
  A conflict answers with the current etag; the model should read the file again instead
  of retrying blindly.
* **Text files** must be valid UTF-8 without NUL bytes and not larger than
  `MAX_TEXT_BYTES`. A UTF-8 byte-order mark is removed from `content` but counted in
  `bytes`.
* **Deletes** go to the Nextcloud trash bin when the *Deleted files* app is enabled.
* **Redirects** are never followed: set `NEXTCLOUD_MCP_BASE_URL` to the final address.
* **Errors** are short English messages (`isError: true` in MCP) that never contain the
  password, the `Authorization` header or long server bodies.
* **Tasks**: `include_completed=false` hides tasks with `STATUS:COMPLETED` or
  `STATUS:CANCELLED`, and tasks that have a `COMPLETED` date but no status. Dates are ISO
  8601; date-only values stay `YYYY-MM-DD`; UTC times end in `Z`.

## Development

```sh
uv sync                                   # Python 3.11+; creates .venv
uv run ruff check . && uv run ruff format --check .
uv run pytest -q --cov                    # unit + MCP-level tests, coverage >= 85 %
uv run python scripts/check_licenses.py   # runtime licence check (queries PyPI for
                                          # packages of other platforms; --offline skips)
```

Unit tests use `httpx.MockTransport` and never touch the network. Integration tests in
`tests/integration` run against a real Nextcloud only when these are set (use a test
account; they create and remove a folder and a calendar named `woow-mcp-it-<random>`):

```sh
export NEXTCLOUD_MCP_IT_BASE_URL=https://cloud.example.com
export NEXTCLOUD_MCP_IT_USERNAME=mcp-test
export NEXTCLOUD_MCP_IT_APP_PASSWORD=...
# optional for a private CA: export NEXTCLOUD_MCP_IT_VERIFY_TLS=false
uv run pytest -q tests/integration
```

## Licence

MIT, Copyright (c) 2026 WOOWTECH. See [LICENSE](LICENSE),
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [PROVENANCE.md](PROVENANCE.md).
