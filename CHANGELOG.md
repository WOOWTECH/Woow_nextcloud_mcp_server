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
