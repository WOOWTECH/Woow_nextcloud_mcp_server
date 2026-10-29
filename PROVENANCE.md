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

## 2026-10-29：Sabre 錯誤細節的已知憑證遮罩

PaaS `woow-mcp-server` 的 Nextcloud 真 launcher／子行程／gateway 搭配自有 HTTP
假後端與假憑證，重現 422 Sabre `<s:message>` 洩漏原文、Basic、URL、JSON、base64，
以及空白整理與 200 字截斷後的部分密碼。這是實際工具回應路徑，並非僅 helper 的假設。

本次實作只依本 repo 現有 MIT 原始碼、上述自有測試及 Python 標準函式庫，未讀取或
搬移任何 AGPL 的 Nextcloud MCP 程式碼。遮罩目標限已設定 app password 及
`username:password` 的原文與常見編碼；解析 XML 後先遮罩，再沿用空白整理及 200 字上限。
500 固定短文案、錯誤碼、正常檔案內容及版本均維持原契約。

`tests/test_error_secrets.py` 驗證來源端行為；PaaS 相依修補另以真正 launcher／子行程、
JSON／SSE gateway 回應及子行程 log 驗收，再從本 repo 已提交的 HEAD 原封 vendor。
