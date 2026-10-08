# Woow Nextcloud MCP Server

[English](README.md) | 繁體中文

這是一個小型、獨立的 [Model Context Protocol](https://modelcontextprotocol.io)（MCP）伺服器，
讓 MCP 用戶端（Claude Code、Claude Desktop，或透過 WOOW 閘道的 claude.ai）操作
**一個 Nextcloud 帳號**：瀏覽與讀取檔案、建立與更新文字檔、上傳檔案、刪除單一檔案，
以及讀取行事曆與待辦事項。

* 每個行程只服務一個帳號，以該帳號的**應用程式密碼**（HTTP Basic）驗證。
* 預設唯讀；寫入與刪除工具必須明確開啟。
* 所有變更都以 ETag 把關：只有在檔案仍是模型讀到的那個版本時，才會覆寫或刪除。
* 沒有管理介面、沒有 OAuth、沒有資料庫。第一次呼叫工具之前不會連線到 Nextcloud，
  所以後端還連不上時也能先啟動。
* 採用 MIT 授權。所有執行期相依套件皆為寬鬆授權（CI 會檢查）。

行為規格請見 [SPEC.md](SPEC.md)。

## 工具

| 工具 | 註冊條件 | 功能 |
|---|---|---|
| `get_file_tree` | 一律 | 列出檔案與資料夾（1 到 3 層，資料夾在前，有數量上限）。 |
| `get_file_content` | 一律 | 以純文字回傳一個 UTF-8 文字檔的內容。 |
| `read_text_file` | 一律 | 回傳一個 UTF-8 文字檔的內容，並附上 `path`、`etag`、`bytes`、`content_type`。 |
| `list_calendars` | 一律 | 列出行事曆：id、名稱、元件類型、顏色、是否可寫入。 |
| `list_tasks` | 一律 | 列出待辦事項（VTODO），未完成在前，再依到期日排序。 |
| `create_text_file` | `READONLY=false` | 建立新的文字檔；絕不覆寫既有檔案。 |
| `update_text_file` | `READONLY=false` | 取代文字檔內容，前提是 `expected_etag` 仍相符。 |
| `upload_file` | `READONLY=false` | 上傳 base64 內容：只建立新檔，或取代指定 etag 的版本。 |
| `delete_file_checked` | `READONLY=false` 且 `ALLOW_DELETE=true` | etag 相符時刪除單一檔案（不能刪資料夾），檔案會移到垃圾桶。 |

`update_text_file`、`upload_file` 與 `delete_file_checked` 帶有 MCP 的 `destructiveHint`
（會取代或移除內容）；因為每次寫入都有 ETag 條件把關，所有工具都標記為 `idempotentHint`。

任何工具都可以再用 `NEXTCLOUD_MCP_DISABLED_TOOLS` 隱藏。0.1 版不支援建立資料夾、
搬移與複製。路徑一律相對於該帳號的家目錄（`""` 或 `/` 代表家目錄），呼叫端不需要
也不應該自行做百分比編碼。

## 設定

使用環境變數（也會讀取工作目錄中的 `.env` 檔，但環境變數優先）：

| 變數 | 預設值 | 說明 |
|---|---|---|
| `NEXTCLOUD_MCP_BASE_URL` | 必填 | Nextcloud 根網址，例如 `https://cloud.example.com` 或 `https://example.com/nextcloud`。區域網路可用 `http://`。不可包含帳密、查詢字串或片段。 |
| `NEXTCLOUD_MCP_USERNAME` | 必填 | 帳號的登入名稱。 |
| `NEXTCLOUD_MCP_APP_PASSWORD` | 必填 | 帳號的應用程式密碼（在任何地方都視為機密）。 |
| `NEXTCLOUD_MCP_READONLY` | `true` | 為 true 時只註冊讀取工具。 |
| `NEXTCLOUD_MCP_ALLOW_DELETE` | `false` | 註冊 `delete_file_checked`（僅在 `READONLY=false` 時）。 |
| `NEXTCLOUD_MCP_DISABLED_TOOLS` | 空白 | 以逗號分隔、永不註冊的工具名稱。名稱不存在時伺服器會停止（狀態碼 2），避免打錯字而讓工具仍然開著。 |
| `NEXTCLOUD_MCP_REQUEST_TIMEOUT` | `30` | 每個後端請求的讀取逾時秒數（連線逾時固定 10 秒）。 |
| `NEXTCLOUD_MCP_MAX_TEXT_BYTES` | `1048576` | 文字工具可回傳的最大檔案，以及可寫入的最大文字量（UTF-8 位元組）。 |
| `NEXTCLOUD_MCP_MAX_UPLOAD_BYTES` | `10485760` | `upload_file` 解碼後的最大大小。 |
| `NEXTCLOUD_MCP_TREE_MAX_ENTRIES` | `500` | `get_file_tree` 最多回傳的項目數。 |
| `NEXTCLOUD_MCP_VERIFY_TLS` | `true` | 是否驗證 TLS 憑證。設為 `false` 時啟動會記錄警告；建議改用 `CA_BUNDLE`。 |
| `NEXTCLOUD_MCP_CA_BUNDLE` | 空白 | PEM 檔路徑，內含要信任的 CA 憑證，適用於私有 CA 的伺服器。 |
| `NEXTCLOUD_MCP_ALLOWED_HOSTS` | 空白 | `http`／`sse` 傳輸額外接受的 `Host` 標頭值，以逗號分隔（可用 `*.example.com` 樣式）。詳見下文。 |

必填設定缺少或無效，或 `backend_policy`（見下文）無法使用時，伺服器會以狀態碼 2 結束，
並輸出一行指出是哪個變數的訊息（絕不輸出其值）。`http://` 的根網址或 `VERIFY_TLS=false`
可以使用，但啟動時會記錄警告。

## 建立專用帳號與應用程式密碼

建議為 AI 助理建立**專用的 Nextcloud 使用者**，不要用個人帳號：應用程式密碼可以存取
該使用者看得到的一切，而 Nextcloud 的應用程式密碼無法限制在特定資料夾。

1. 以管理員身分開啟「使用者」（頭像選單 →「帳號」/「使用者」），建立一個使用者，
   例如 `assistant`，需要的話可設定配額。
2. 只把它需要處理的資料夾與行事曆分享給這個使用者（不需要寫入的就用唯讀分享）。
3. 以該使用者登入，開啟「個人設定 → 安全性」，在「裝置與工作階段」輸入名稱
   （例如 `MCP server`），按「建立新的應用程式密碼」。
4. 複製只會顯示一次的密碼，這就是 `NEXTCLOUD_MCP_APP_PASSWORD`。之後可隨時在同一處
   撤銷。啟用兩步驟驗證的帳號也能使用應用程式密碼。

`NEXTCLOUD_MCP_USERNAME` 是登入名稱。若登入名稱是電子郵件或來自 LDAP，伺服器會先透過
OCS `cloud/user` 查一次真正的使用者 id，之後用它存取 WebDAV/CalDAV。

## 安裝

需要 Python 3.11 以上。在本專案的 clone 目錄中使用 [uv](https://docs.astral.sh/uv/)：

```sh
uv tool install .            # 安裝 `nextcloud-mcp-server` 指令
nextcloud-mcp-server --help
```

也可以不安裝直接執行：`uvx --from /path/to/Woow_nextcloud_mcp_server nextcloud-mcp-server`，
或以 `python -m nextcloud_mcp_server.server` 啟動。

```
nextcloud-mcp-server [--transport stdio|http|sse] [--host 127.0.0.1] [--port 3000] [--path /mcp]
```

預設傳輸方式為 `stdio`。

## Claude Code

```sh
claude mcp add nextcloud \
  -e NEXTCLOUD_MCP_BASE_URL=https://cloud.example.com \
  -e NEXTCLOUD_MCP_USERNAME=assistant \
  -e NEXTCLOUD_MCP_APP_PASSWORD=xxxxx-xxxxx-xxxxx-xxxxx-xxxxx \
  -e NEXTCLOUD_MCP_READONLY=false \
  -- nextcloud-mcp-server
```

或寫在專案的 `.mcp.json`：

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

請勿把含有應用程式密碼的檔案提交到版本控制。

## Claude Desktop

在 `claude_desktop_config.json`（「設定 → 開發者 → 編輯設定」）加入：

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

如果 Claude Desktop 的 `PATH` 找不到指令，請改用絕對路徑（例如
`which nextcloud-mcp-server` 的輸出）。

## 搭配 WOOW 閘道

WOOW 管理閘道會以子行程啟動本伺服器，並負責對外端點、bearer/網址權杖、管理介面與
每個工具的政策：

```sh
nextcloud-mcp-server --transport http --host 127.0.0.1 --port 3000 --path /mcp
```

除非明確設定 `FASTMCP_SHOW_SERVER_BANNER`／`FASTMCP_CHECK_FOR_UPDATES`，否則 FastMCP 的
啟動橫幅與版本更新檢查都是關閉的。

伺服器只綁定指定的位址，本身沒有任何驗證機制，請保持在 `127.0.0.1` 並放在閘道後面。

**Host 與 Origin 檢查（防 DNS rebinding）。** `http` 與 `sse` 傳輸在 `Host` 標頭不是
`localhost`、`127.0.0.1`、`::1`、綁定的位址或 `NEXTCLOUD_MCP_ALLOWED_HOSTS` 中的名稱時
（忽略連接埠），回應 `421 Misdirected Request`；瀏覽器送出的 `Origin` 既非同源也非
loopback 時回應 `403`。WOOW 閘道在轉送前會把 `Host` 改寫成 `127.0.0.1:<port>` 並移除
`Origin`，因此不需額外設定。若代理伺服器原封不動轉送對外的 `Host`，就必須列出該名稱，
例如 `NEXTCLOUD_MCP_ALLOWED_HOSTS=mcp.example.com`。

**`backend_policy` 掛鉤。** 若可以匯入名為 `backend_policy` 的 Python 模組，它必須提供可呼叫的
`async_client(*, base_url, **kwargs)`；伺服器會用它建立唯一的 `httpx.AsyncClient`，並在啟動時
記錄一行 info。伺服器只傳入 `base_url`、`auth`（HTTP Basic）、`timeout` 與 `headers`
（`User-Agent`）；傳輸層、TLS 驗證、`trust_env` 與 `follow_redirects` 由掛鉤自行設定
（閘道用它來固定 DNS 並禁止重新導向）。無論如何，伺服器都會拒絕重新導向的回應。使用掛鉤時
會忽略 `NEXTCLOUD_MCP_VERIFY_TLS` 與 `NEXTCLOUD_MCP_CA_BUNDLE`；若有設定，啟動時會記錄警告。
閘道的傳輸層可能以例外取代錯誤回應：帶有整數 `status` 屬性的例外會被當成該 HTTP 狀態碼處理；
閘道拒絕的請求（例如路徑含 `%`）會回報
`The gateway refused this request for "<p>" (destination or path not allowed).`。
掛鉤在啟動時就解析：沒有這個模組時使用一般的 `httpx.AsyncClient`；模組匯入失敗或沒有可呼叫的
`async_client` 時，伺服器以狀態碼 2 停止，閘道的政策絕不會被默默略過。

**健康檢查探測（不是 MCP 工具）。** `create_server()` 回傳的 FastMCP 伺服器附有 client；
`await server.nextcloud_client.probe()` 只送一次帶驗證的 OCS `cloud/user` 請求（不讀任何檔案或
行事曆資料），成功時回傳 `{"ok": True, "user_id": "<id>"}`，失敗時丟出與工具相同的
`ToolError`。成本很低，適合定期健康檢查；它與工具共用使用者 id 快取與下述的驗證鎖存，
也不會出現在 `tools/list`。

## 暴力破解防護

Nextcloud 的暴力破解防護會依來源 IP 累計每一次登入失敗，之後對該 IP 限速（HTTP 429）或
封鎖，連同一 IP 的網頁登入與其他用戶端也會受影響。為避免這種情況，伺服器收到第一個
`401` 或 `429` 後就會**鎖存**：之後每次工具呼叫與探測都立即以相同訊息失敗，不再連線到
Nextcloud，直到伺服器行程重新啟動（WOOW 閘道在連線設定變更時會重啟它）。

* 撤銷或更換應用程式密碼**之前**，請先停止 MCP 伺服器（或閘道 add-on），更新密碼後再啟動。
* 若 IP 已被限速，管理員可以用 `occ security:bruteforce:reset <ip>` 重設。
* 可考慮在「Brute-force settings」App（`bruteforcesettings`）把閘道的 IP 加入白名單。

## 行為說明

* **ETag** 回傳時不含引號也不含 `W/` 前綴；傳回伺服器時加不加引號都可以。寫入時送出
  `If-Match: "<etag>"`（建立新檔則送 `If-None-Match: *`）。只接受 ASCII 的 etag 字元
  （RFC 9110 `etagc`）。發生衝突時會回報目前的 etag（或說明檔案已不存在），模型應重新
  讀取檔案，而不是盲目重試。
* **ETag 解析度**：Nextcloud 對同一檔案的寫入，ETag 大約只有一秒的解析度。約一秒內對同一
  檔案的兩次寫入可能無法區分：ETag 可能不變，或舊的 ETag 仍被接受。因此 `If-Match` 能防護
  的是相隔一秒以上的他人修改。更新或取代後若回傳的 etag 與送出的相同，結果會附上 `note`，
  提醒對該檔案下一次條件式寫入前先等一秒。
* **文字檔**必須是合法 UTF-8、不含 NUL 位元組，且不超過 `MAX_TEXT_BYTES`。UTF-8 的
  BOM 會從 `content` 移除，但仍計入 `bytes`。
* **刪除**的檔案在啟用「已刪除的檔案」App 時會移到 Nextcloud 垃圾桶。
* **重新導向**一律不跟隨：請把 `NEXTCLOUD_MCP_BASE_URL` 設成最終網址。
* **樹狀列表**（`depth` 2–3）會略過已消失、無法讀取（403／404）或被閘道拒絕（例如名稱含
  `%` 的資料夾）的子資料夾；被拒絕時也會設 `truncated=true`，其餘部分照常列出。
* **錯誤**是簡短的英文訊息（MCP 中 `isError: true`），絕不包含密碼、`Authorization`
  標頭或冗長的伺服器回應內容。
* **路徑**含控制字元（C0、DEL、C1）、雙向文字覆寫／隔離字元、`.`／`..` 區段、空區段或
  反斜線時會被拒絕。伺服器回傳的路徑也經過同樣檢查，不合格的項目會被略過且絕不會再去
  存取。若清單中沒有任何路徑位於該帳號的 WebDAV 資料夾下（反向代理或 `overwritewebroot`
  設定不符），工具會明確說明，而不是回傳空清單。
* **大型回應**：WebDAV／CalDAV 回應上限為 8 MiB；較大的回應會在背景執行緒解析，
  讓伺服器保持回應。
* **待辦事項**：`include_completed=false` 會隱藏 `STATUS:COMPLETED`、`STATUS:CANCELLED`，
  以及有 `COMPLETED` 日期但沒有狀態的項目。日期採 ISO 8601；只有日期的值維持
  `YYYY-MM-DD`；UTC 時間以 `Z` 結尾。重複規則（`RRULE`）**不會展開**：重複的待辦事項
  只列出一次，到期／開始時間為第一次發生的時間；只含變更過的單次發生（有
  `RECURRENCE-ID` 但沒有主項目）的物件會被略過。超過 1 MiB 的行事曆物件會被略過，並計入
  `skipped_large_objects`。`TZID` 為 IANA 名稱、常見的 Windows／Outlook 名稱（例如
  `W. Europe Standard Time`、`Taipei Standard Time`）或帶前綴的 IANA 路徑時會正確套用；
  其他 `TZID` 則保留為浮動時間（不帶時差）。

## 開發

```sh
uv sync                                   # Python 3.11 以上；建立 .venv
uv run ruff check . && uv run ruff format --check .
uv run pytest -q --cov                    # 單元與 MCP 層級測試，覆蓋率需 >= 85 %
uv run python scripts/check_licenses.py   # 執行期授權檢查（其他平台的套件會查詢
                                          # PyPI；加 --offline 則不查）
```

單元測試使用 `httpx.MockTransport`，完全不連網路。`tests/integration` 中的整合測試只在
設定下列變數時才會對真正的 Nextcloud 執行（請使用測試帳號；測試會建立並移除名為
`woow-mcp-it-<亂數>` 的資料夾與行事曆）：

```sh
export NEXTCLOUD_MCP_IT_BASE_URL=https://cloud.example.com
export NEXTCLOUD_MCP_IT_USERNAME=mcp-test
export NEXTCLOUD_MCP_IT_APP_PASSWORD=...
# 私有 CA 可選擇加上：export NEXTCLOUD_MCP_IT_VERIFY_TLS=false
uv run pytest -q tests/integration
```

## 授權

MIT，Copyright (c) 2026 WOOWTECH。請見 [LICENSE](LICENSE)、
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 與 [PROVENANCE.md](PROVENANCE.md)。
