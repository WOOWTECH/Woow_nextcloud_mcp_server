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

## 2026-10-30：0.1.6 憑證遮罩（PaaS 與 HA 對齊）

0.1.6 把 2026-10-29 的遮罩集中成 `errors.mask_credentials`（與 `errors.credential_forms`），
作為 PaaS `woow-mcp-server` 與 HA add-on（`WOOWTECH/Woow_ha_mcp_addons`）共用的同一個函式。
比對時讀取的只有 WOOWTECH 自有的程式碼與文件：本 repo 的 MIT 原始碼（含 159410a）；HA add-on
vendored 的本套件副本（v0.1.5 加上 HA 自己的修補，來源記錄在該 repo 的
`docs/provenance/runtime-sources.json`）、其 `docs/provenance/nextcloud-error-secrets-20261029.md`、
`tests/test_nextcloud_error_secrets.py` 與管理核心的 diagnostics／redact；PaaS 的
`apps/nextcloud/tests/test_error_secrets.py`。另外只用了公開文件：RFC 3986、RFC 4648、
RFC 8259，以及 PHP 手冊對 `json_encode` 預設把 `/` 寫成 `\/` 的說明。未開啟、搜尋或改寫任何
上列 AGPL 的 Nextcloud MCP 程式碼。

比對結果：HA 的遮罩形式是 159410a 的子集（HA 的 JSON 跳脫只有 ASCII 輸出），兩邊的聯集就是
159410a 的形式，搬成單一函式後行為不變（與 159410a 的 `server_message` 做 20 萬組差分測試
無差異）。比對時發現兩邊共同的限制：由左而右、不重疊的單次替換在兩個出現位置重疊時（週期性
的密碼、base64 結尾的 `=` 也是密碼的第一個字元）會留下第二個的後半段，因此改為找出所有出現
位置（含重疊）、合併後再替換，與暴力參考實作做隨機比對無差異。另依 RFC 8259 補上 JSON 字串中
`/` 寫成 `\/` 的形式：Nextcloud 以 PHP 撰寫，含 `/` 的密碼與標準 base64（例如以 JSON 印出的
`Authorization: Basic` 值）原本會原樣留下。HA 的 `backend_policy` 必要、`woow_backend_probe`、
5xx 也轉出 Nextcloud 原文、OCS／JSON／純文字擷取與 1000 字上限屬於 HA 自己的包裝與 owner
政策，不在本 repo。

`tests/test_error_secrets_mcp.py` 以本 repo 的 server、client 與 in-memory 假 Nextcloud 重現
兩邊套件共同的 7 種形式（SSE／JSON 回應 × 一般／code mode）並涵蓋其餘形式；HA 的閘道與 PaaS 的
launcher 不在本 repo，兩邊仍各自以真子行程驗收 vendored 結果。
