# Provenance

This document records how the code in this repository came to be, so that its MIT
licence can be relied on.

## Specification

[SPEC.md](SPEC.md) was written by WOOWTECH (WOOW). It describes only observable
behaviour (protocol requests, inputs, outputs and errors) and was written without
reproducing code from any other Nextcloud MCP implementation.

## Implementation

The implementation (the `nextcloud_mcp_server` package, the tests, the scripts, the CI
workflow and the documentation) was written from SPEC.md by an AI coding agent (Claude,
by Anthropic) working for WOOWTECH, in October 2026. The agent worked under a clean-room
rule and **did not have access to, open, search, copy, paraphrase or port** code from:

- the Nextcloud Context Agent (`nextcloud/context_agent`) or any WOOW overlay of it,
- `cbcoutinho/nextcloud-mcp-server`,
- any other Nextcloud MCP project,

all of which are licensed under the AGPL (or were treated as off-limits regardless of
licence).

The only inputs used were:

- SPEC.md;
- public protocol documentation: RFC 4918 (WebDAV), RFC 4791 (CalDAV), RFC 5545
  (iCalendar), RFC 4648 (base64), RFC 9110 (HTTP semantics, conditional requests) and the
  Nextcloud developer documentation for WebDAV and the OCS API;
- the public documentation and the installed source code of this project's own
  permissively licensed dependencies (FastMCP, the MCP Python SDK, httpx,
  pydantic-settings, defusedxml), read only to use their APIs correctly.

The in-memory Nextcloud stand-in used by the unit tests (`tests/fake_nextcloud.py`) was
written from the same public RFCs.

## Third-party code

No third-party source code is vendored. Runtime dependencies are installed from PyPI and
are listed with their licences in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); CI
fails if any of them is GPL, LGPL, AGPL or of unknown licence
(`scripts/check_licenses.py`).
