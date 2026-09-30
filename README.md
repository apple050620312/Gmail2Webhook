# Gmail2Webhook

使用 Gmail API 讀取指定寄件人的郵件，將信件主旨、帳號、寄件人、收件人、日期及內文送到 Discord webhook embed。支援多 Gmail 帳號，每個帳號可配置多組寄件人與 webhook，同一封信也可轉送多個 webhook。

## 安裝

需要 Python 3.11 以上，在專案目錄執行（PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item config.example.json config.json
```

## Google 與 Discord 設定

1. 在 Google Cloud Console 建立專案，啟用 **Gmail API**。
2. 設定 Google Auth Platform 的同意畫面。若應用程式為 External / Testing，將每個 Gmail 帳號加入 Test users。
3. 建立 **Desktop app** 類型的 OAuth client，下載 JSON 至 `secrets/client_secret.json`。同一個 OAuth client 可授權多帳號，但各帳號必須有獨立 token 檔。
4. 在 Discord 頻道的「整合 → Webhooks」建立 webhook 並複製 URL。
5. 編輯 `config.json`，填入 Gmail 地址與寄件人地址，刪除不需要的示範帳號與路由。

Webhook 建議使用環境變數：

```powershell
$env:DISCORD_ORDERS_WEBHOOK = 'https://discord.com/api/webhooks/123/your-token'
$env:DISCORD_ALERTS_WEBHOOK = 'https://discord.com/api/webhooks/456/your-token'
$env:DISCORD_WORK_WEBHOOK = 'https://discord.com/api/webhooks/789/your-token'
```

也可將某路由的 `webhook_env` 改為 `webhook_url` 並填入 URL，兩者只能選一個。環境變數需由執行服務的環境提供；程式不會自動讀取 `.env`。

設定欄位：

| 欄位 | 用途 |
| --- | --- |
| `poll_interval_seconds` | 每次完整同步後等待秒數，預設 60 |
| `state_db` | SQLite 發送紀錄，預設 `data/state.sqlite3` |
| `accounts[].id` | 穩定且唯一的帳號識別碼 |
| `accounts[].email` | 要授權的 Gmail 地址；程式會核對實際授權帳號 |
| `credentials_file` | Desktop OAuth client JSON |
| `token_file` | 該帳號專屬的 OAuth token 檔 |
| `routes[].id` | 帳號內唯一且穩定的路由識別碼 |
| `routes[].senders` | 一個以上完整 email 地址，不支援萬用字元 |
| `routes[].webhook_env` / `webhook_url` | Discord webhook 的環境變數名稱或 URL |

所有相對檔案路徑以設定檔所在資料夾為基準。`config.json`、`secrets/`、`data/` 已加入 `.gitignore`，請保護 OAuth token、client JSON、webhook URL 與狀態資料庫。

## 授權及啟動

```powershell
# 開啟瀏覽器，逐一授權所有帳號（僅要求 gmail.readonly）
.\.venv\Scripts\gmail2webhook.exe authorize

# 或只授權某個帳號
.\.venv\Scripts\gmail2webhook.exe authorize --account personal

# 同步一次；有任何失敗會回傳 exit code 1
.\.venv\Scripts\gmail2webhook.exe run --once

# 持續執行，Ctrl+C 停止
.\.venv\Scripts\gmail2webhook.exe run

# 指定另一份設定檔；--config 放在子命令之前
.\.venv\Scripts\gmail2webhook.exe --config config.json run
```

也可使用 `python -m gmail2webhook`。排程器可定期執行 `run --once`；請設定工作目錄或使用設定檔的絕對路徑。

首次同步會發送 **所有符合寄件人的既有郵件**，包含已讀、封存、垃圾郵件及垃圾桶，不限收件匣，也不會標記已讀或修改 Gmail。程式會取得 Gmail 搜尋的所有分頁，再核對 `From` 的完整地址（不區分大小寫），避免搜尋結果誤配。這是依郵件 From 篩選，不是驗證寄件人真實身分。

後續每輪重新列出符合條件的郵件，跳過該帳號與路由已送達的信件。完整掃描保證新增寄件人時仍可同步歷史信件，但大量信件會增加 Gmail API 用量；同步時間加上等待時間才是實際週期。帳號或路由失敗不會中止其他帳號、路由的同步。暫時性 Gmail API 錯誤與 Discord 429 / 5xx 會重試；失敗的信件下次同步再嘗試。

長內文依 Discord embed 限制分段，每成功送達一段就儲存進度，重新啟動會接續發送。HTML 轉為文字，multipart 優先使用純文字，支援文字內文以 Gmail attachment ID 儲存的格式。檔案附件不會上傳，embed 顯示的是文字內文；沒有可讀文字時使用 Gmail snippet。寄件人、收件人及日期等欄位過長會截短，內文會完整分段。郵件中的 mention 不會 ping Discord 使用者。

保留帳號 ID、路由 ID 及 `state_db` 才能延續去重紀錄。新增路由 ID 會補送歷史郵件；只更換既有路由的 webhook URL 不會重送已送過的信件。刪除狀態資料庫會造成歷史郵件重新發送。

建議單一實例執行。SQLite 鎖可防止同一資料庫的並行 worker 同時送同一段，但 Discord 沒有此用途的冪等鍵：若 Discord 已收件、程式在儲存進度前崩潰，或網路回應遺失，重試仍可能重複。這是至少一次發送模型，不能保證 exactly-once。

Google External / Testing 的 refresh token 通常會在 7 天後失效（Gmail scope 不屬於基本登入 scope），屆時重新執行 `authorize`。正式長期使用需適當設定發布狀態與 Google 要求的驗證流程。若登入帳號錯誤，程式會拒絕保存該 token。錯誤紀錄只輸出類型與識別碼，避免洩漏 webhook/token；檢查配置、API 啟用狀態、OAuth 授權與網路即可排查。

## 驗證

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

測試以 mock Gmail API 與 Discord HTTP 回應驗證多帳號／多路由、完整分頁、精確寄件人、持久化去重、分段續送、限流重試、失敗隔離、MIME 解析與 embed 大小，不會讀取真實信箱或發送訊息。
