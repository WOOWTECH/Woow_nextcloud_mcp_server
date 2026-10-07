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
| `NEXTCLOUD_MCP_DISABLED_TOOLS` | 空白 | 以逗號分隔、永不註冊的工具名稱。 |
| `NEXTCLOUD_MCP_REQUEST_TIMEOUT` | `30` | 每個後端請求的讀取逾時秒數（連線逾時固定 10 秒）。 |
| `NEXTCLOUD_MCP_MAX_TEXT_BYTES` | `1048576` | 文字工具可回傳的最大檔案，以及可寫入的最大文字量（UTF-8 位元組）。 |
| `NEXTCLOUD_MCP_MAX_UPLOAD_BYTES` | `10485760` | `upload_file` 解碼後的最大大小。 |
| `NEXTCLOUD_MCP_TREE_MAX_ENTRIES` | `500` | `get_file_tree` 最多回傳的項目數。 |
| `NEXTCLOUD_MCP_VERIFY_TLS` | `true` | 是否驗證 TLS 憑證。 |

必填設定缺少或無效時，伺服器會以狀態碼 2 結束，並輸出一行指出是哪個變數的訊息
（絕不輸出其值）。

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
FASTMCP_SHOW_SERVER_BANNER=false FASTMCP_CHECK_FOR_UPDATES=off \
  nextcloud-mcp-server --transport http --host 127.0.0.1 --port 3000 --path /mcp
```

伺服器只綁定指定的位址，本身沒有任何驗證機制，請保持在 `127.0.0.1` 並放在閘道後面。

**`backend_policy` 掛鉤。** 若可以匯入名為 `backend_policy` 的 Python 模組，且其中有可呼叫的
`async_client(*, base_url, **kwargs)`，伺服器就用它建立唯一的 `httpx.AsyncClient`
（傳入的關鍵字參數與伺服器自己建立時相同：Basic 驗證、`follow_redirects=False`、
`trust_env=False`、逾時、TLS 驗證與 `User-Agent`）。閘道用它來固定 DNS 並禁止重新導向。
沒有這個模組時就使用一般的 `httpx.AsyncClient`。模組存在但匯入失敗時會直接報錯，
不會默默略過。

## 行為說明

* **ETag** 回傳時不含引號也不含 `W/` 前綴；傳回伺服器時加不加引號都可以。寫入時送出
  `If-Match: "<etag>"`（建立新檔則送 `If-None-Match: *`）。發生衝突時會回報目前的 etag，
  模型應重新讀取檔案，而不是盲目重試。
* **文字檔**必須是合法 UTF-8、不含 NUL 位元組，且不超過 `MAX_TEXT_BYTES`。UTF-8 的
  BOM 會從 `content` 移除，但仍計入 `bytes`。
* **刪除**的檔案在啟用「已刪除的檔案」App 時會移到 Nextcloud 垃圾桶。
* **重新導向**一律不跟隨：請把 `NEXTCLOUD_MCP_BASE_URL` 設成最終網址。
* **錯誤**是簡短的英文訊息（MCP 中 `isError: true`），絕不包含密碼、`Authorization`
  標頭或冗長的伺服器回應內容。
* **待辦事項**：`include_completed=false` 會隱藏 `STATUS:COMPLETED`、`STATUS:CANCELLED`，
  以及有 `COMPLETED` 日期但沒有狀態的項目。日期採 ISO 8601；只有日期的值維持
  `YYYY-MM-DD`；UTC 時間以 `Z` 結尾。

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
