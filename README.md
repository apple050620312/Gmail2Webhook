# Gmail2Webhook

簡單的 Python Gmail → Discord 轉送程式，適合部署在 **Pterodactyl Panel 的 Python egg**。使用 `main.py` 啟動、`requirements.txt` 安裝套件，直接編輯 `config.json` 設定多帳號、多寄件人與多 webhook。需要 Python 3.11 以上及對 Google API、Discord 的 HTTPS 出站連線。

## 1. 設定 Google 與 Discord

1. 在 Google Cloud Console 建立專案、啟用 **Gmail API**，設定 OAuth 同意畫面；External / Testing 模式需加入所有 Gmail 帳號為 Test users。
2. 建立 **Desktop app** 類型的 OAuth client，下載 JSON 為 `secrets/client_secret.json`。
3. 在 Discord 頻道「整合 → Webhooks」建立 webhook 並複製 URL。
4. 複製 `config.example.json` 為 `config.json`，填入實際 Gmail 地址、寄件人及 `webhook_url`，刪除不需要的示範帳號／路由。

每個帳號必須使用不同的 `token_file`，可共用 `credentials_file`。每個帳號可有多個 route；同一寄件人可對應多個 webhook。寄件人填完整 email 地址，不支援萬用字元。

| 設定 | 用途 |
| --- | --- |
| `poll_interval_seconds` | 每輪同步後等待秒數，預設 60 |
| `state_db` | 本機 SQLite 發送紀錄，預設 `data/state.sqlite3` |
| `accounts[].id` / `routes[].id` | 穩定的帳號／路由 ID，供去重使用 |
| `accounts[].email` | Gmail 帳號，會核對實際授權帳號 |
| `credentials_file` | Google OAuth client JSON 路徑 |
| `token_file` | 各帳號專屬的 OAuth token 路徑 |
| `routes[].senders` | 一個以上指定寄件人地址 |
| `routes[].webhook_url` | Discord webhook URL |

路徑以 `config.json` 所在目錄為基準。直接填寫 URL 即可，無須新增 Panel 環境變數。若管理員已配置環境變數，也可用 `webhook_env` 取代 `webhook_url`，兩者只能選一個；程式不讀取 `.env`。

## 2. 在自己的電腦完成 Gmail 授權

Pterodactyl 容器沒有桌面瀏覽器，Google Desktop OAuth 的 localhost 回呼應在自己的電腦完成，再上傳 token。**不要在 Panel 執行 `authorize`**，也不需要在容器公開 OAuth port。

在有 Python 與瀏覽器的電腦下載本專案，準備好 `config.json` 與 client JSON，於專案目錄執行：

```shell
python -m pip install -r requirements.txt
python main.py authorize
```

瀏覽器會逐一要求登入各 Gmail 帳號，只要求 `gmail.readonly` 權限。也可單獨授權：

```shell
python main.py authorize --account personal
```

授權成功後，`secrets/` 會產生設定的 token JSON。Gmail 帳號不符時不會保存 token。token 內含 refresh token，上傳後伺服器會自動更新 access token。

## 3. 部署到 Pterodactyl Panel

1. 選擇 **Python 3.11+ egg／映像**，映像與啟動命令的管理權限可能由主機商提供。
2. 透過 Panel 檔案管理員或 SFTP 將下列檔案放在伺服器根目錄（通常為 `/home/container`）：

   ```text
   main.py
   requirements.txt
   config.json
   gmail2webhook/             # 整個 Python 模組資料夾
   secrets/
     client_secret.json
     personal-token.json     # 已在自己的電腦授權
     work-token.json         # 依實際帳號數量上傳
   ```

3. 如果 Python egg 有套件安裝欄位，將 requirements 檔設為 `requirements.txt`，啟動檔設為 `main.py`。
4. 若可自訂完整啟動命令，可使用：

   ```sh
   python -m pip install --user -r requirements.txt && python -u main.py
   ```

   若 egg 已自動安裝 requirements，啟動命令只需：

   ```sh
   python -u main.py
   ```

5. 按 **Start**。程式會持續輪詢，日誌即時顯示於 Panel Console。`data/state.sqlite3` 會自動建立，SQLite 是 Python 內建模組，不需另外架設資料庫。

不同主機商的 Python egg 欄位名稱可能不同；啟動命令由管理員限制時，請讓主機商設定以上安裝／啟動命令。程式不需要額外對外監聽埠、systemd、Docker Compose、虛擬環境或 Python 套件打包安裝。

**保留 `secrets/` 及 `data/`**，重啟不會重送已記錄的郵件；重裝／搬移伺服器時一併備份。勿上傳本機 `.venv/`。OAuth JSON、token、webhook URL 與狀態資料庫應保密，已加入 `.gitignore`。

## 行為與其他命令

首次會同步 **指定寄件人的所有歷史郵件**，包含已讀、封存、垃圾郵件及垃圾桶。每個帳號會先取得所有分頁的待發送郵件，依 Gmail 收信時間（`internalDate`）**由舊到新**發送。後續輪詢也採相同順序，核對 `From` 完整地址（不區分大小寫），透過 SQLite 跳過已送達的帳號／路由／信件；不會修改信件或標記已讀。每輪完整掃描，大信箱同步較久且增加 API 用量，也會暫存待發送郵件於記憶體。

Discord embed 包含主旨、寄件人、收件人及內文，日期放在 footer，不顯示 Gmail ID 或帳號欄位。長內文自動分段，重啟後從未送達段落繼續。內文的連續空白／tab 合併為一個空白，移除行首尾空白，段落間最多保留一個空行。有 HTML 版本時優先使用；有獨立顯示文字的連結轉成 Discord Markdown `[文字](<網址>)`，純網址及以網址本身作為文字的連結直接保留原始 URL，不加 Markdown 或角括號。一般長度的連結不會跨 embed 拆開。HTML 轉成可讀文字，忽略 script／style，沒有文字時使用 Gmail snippet；檔案附件不會上傳。過長的主旨及 metadata 會截短。郵件內的 mention 不會 ping Discord 使用者。

Gmail 暫時性 API 錯誤、Discord 限流／伺服器錯誤會重試，單一帳號或路由錯誤不會中止其他帳號。正常重啟可去重；若 Discord 已收件但回應遺失或程式在記錄前中斷，仍可能重複發送。建議只啟動一個實例。

變更帳號／路由 ID 或刪除 `state_db` 會補送歷史郵件；只更換既有路由的 webhook URL 不會重送已完成的信件。

```shell
# 手動同步一次，有失敗時 exit code 為 1
python main.py run --once

# 顯式持續執行（與 python main.py 相同）
python main.py run

# 指定設定檔
python main.py --config another-config.json run

```

Google External / Testing 的 Gmail refresh token 通常 7 天後失效。重新在自己的電腦執行 `authorize`，停止 Panel 服務、上傳新的 token，再啟動。長期使用需適當設定 Google 應用程式發布狀態與要求的驗證流程。錯誤紀錄只顯示錯誤類型，避免洩漏憑證。
