# 每日總經晨報 — 完整重建手冊（RUNBOOK）

這份文件記錄「從零重建整套系統」所需的每一個步驟，包含 **repo 之外**的設定
（LINE Bot、Claude Code 環境變數、routine 排程、GitHub repo 設定、群組 ID 取得）。
若未來環境毀損、要搬移帳號、或要重新建立，照這份做即可。

> 每天「執行時」做什麼，另見 `.claude/skills/daily-macro-report/SKILL.md`。
> 本手冊管的是「一次性基礎建置」。

---

## 0. 系統概觀（現行架構）

- **做什麼**：每個台灣工作日早上（台北 08:00），自動產出全球總經/市場晨報「卡片圖片」，
  推播到 Neil 的 3 個 LINE 業務群組。**只發圖片，不發文字。**
- **跑在哪**：**Claude Code Routine**（Anthropic 雲端），不是 GitHub Actions，不是本機。
  電腦關機、人不在都照跑。
- **大腦是誰**：**Claude 本人**（透過 WebSearch 查證行情、依合規規則撰寫文案）。
  **不使用 Gemini、不使用任何外部 LLM API key。**
- **圖片怎麼產**：HTML 排版 → 內建無頭 Chromium 截圖成 PNG（見第 5 節）。
  **不使用 Pillow**（Claude sandbox 裝不了）。
- **圖片怎麼給 LINE 看**：commit 進 **public** repo，用 `raw.githubusercontent.com`
  當圖床；LINE 伺服器自行去公開網址抓圖。

```
Claude Code Routine（工作日 08:00 台北）
  1. scripts/check_workday.py      判斷是否台灣工作日；非工作日→整個跳過
  2. Claude 用 WebSearch 抓最近收盤行情 + 當日財經新聞
  3. Claude 依合規規則寫三段文案，輸出 report.json + line_text.txt
  4. scripts/make_card_html.py     report.json → card.html
     scripts/shot_card.js          card.html → card.png + card_preview.png（Chromium 截圖）
  5. git commit + push 報告資產（raw URL 立即生效，當圖床）
  6. scripts/push_line.py          對 3 個群組推播「圖片卡片」（不帶 --text-file）
```

---

## 1. 需要的元件清單

| 元件 | 用途 |
|---|---|
| GitHub repo `NeilCCH/Daily-Macro-Report`（**public**） | 存程式、當圖床 |
| LINE Messaging API channel | 取得 access token、發送推播 |
| LINE 群組 ID × N | 推播目標 |
| Claude Code 環境（Environment） | 存放環境變數、網路政策 |
| Claude Code Routine | 排程每工作日 08:00 觸發 |
| repo 內的 `daily-macro-report` skill | 定義每天執行流程 |

---

## 2. Repo 檔案結構（現行有效的）

```
scripts/
  check_workday.py       工作日閘門，讀 data/taiwan_calendar_<year>.json
  make_card_html.py      report.json → card.html（現行產圖流程）
  shot_card.js           card.html → card.png / card_preview.png（Chromium 截圖）
  push_line.py           推播 LINE（預設只發圖片）
data/
  taiwan_calendar_<year>.json   台灣工作日/假日/補班日
.claude/skills/daily-macro-report/SKILL.md   每日執行流程定義
reports/<YYYY-MM-DD>/   每日產出（report.json / line_text.txt / card.html / card.png / card_preview.png）
docs/RUNBOOK.md         本文件
```

> **已停用的舊檔（僅供參考，勿使用）**：
> `scripts/generate_report.py`（Gemini 版產報，已棄用）、
> `scripts/make_card.py`（Pillow 版產圖，Claude sandbox 裝不了 Pillow，已棄用）。
> `.github/workflows/daily-report.yml`（Gemini + GitHub Actions 排程）已於 PR #1 移除。

---

## 3. 從零重建步驟（一次性）

### 3.1 GitHub repo 必須是 Public
圖床靠 `raw.githubusercontent.com`，LINE 伺服器要抓得到圖，**repo 必須 public**。
- repo → **Settings** → 最下方 **Danger Zone** → **Change visibility** → **Public**。
- 驗證：瀏覽器開 `https://raw.githubusercontent.com/NeilCCH/Daily-Macro-Report/<分支>/reports/<日期>/card.png`
  能看到圖 = OK。

### 3.2 建立 LINE Messaging API channel 並取得 token
1. 到 [LINE Developers Console](https://developers.line.biz/) 建立 Provider → **Messaging API** channel。
2. **Messaging API** 分頁 → **Channel access token (long-lived)** → **Issue** 一組，複製備用。
3. 允許 Bot 加入群組：[LINE Official Account Manager](https://manager.line.biz/) → 設定 →
   回應設定 → 開啟「**允許加入群組／Allow bot to join group chats**」。
4. 用手機加這個 Bot 好友，並把它邀請進每一個要發晨報的群組。

> ⚠️ token 是機密。**絕不要**寫進任何會被 commit 的檔案（repo 是 public）。
> 只放在第 3.4 節的 Claude 環境變數。若曾外流，到 Console **Reissue** 換新的。

### 3.3 取得群組 ID（`C` 開頭）
群組 ID 不是群組名稱，只能從 webhook 事件撈：
1. 開 https://webhook.site，複製它給你的專屬網址。
2. LINE Console → 你的 channel → **Messaging API** → **Webhook URL** 貼上該網址、存檔、
   開啟 **Use webhook**。
3. 在目標群組隨便發一句話 → 回 webhook.site 看收到的 JSON，找：
   ```json
   "source": { "type": "group", "groupId": "Cxxxxxxxx..." }
   ```
   那個 `Cxxxx...` 就是群組 ID。
4. 每個群組重複，收集全部 ID。
5. （撈完後 webhook 用不到了，可清掉 Webhook URL；本系統用主動推播 push，不需要 webhook。）

對照：`C`=群組(要這個) / `R`=多人聊天室 / `U`=個人。

### 3.3.1 群組名稱對照與開關（`data/line_groups.json`）

`LINE_GROUP_IDS` 只是一串不透明的 `Cxxxx...`，光看環境變數認不出是哪個群組。
`data/line_groups.json` 記錄每個群組 ID 對應的人類可讀名稱與是否啟用：

```json
{
  "Cxxxx1": {"name": "南區組訓", "enabled": false},
  "Cxxxx2": {"name": "南區 單位主管群組", "enabled": true}
}
```

- 群組名稱可用 LINE Messaging API 的 `GET /v2/bot/group/{groupId}/summary` 反查
  （帶 `Authorization: Bearer $LINE_CHANNEL_ACCESS_TOKEN`）。
- **要暫停對某個群組推播**：把該群組 `enabled` 改成 `false`，commit 併回預設分支即可生效，
  **不需要**去改 `LINE_GROUP_IDS` 環境變數（環境變數只對新 session 生效，而這個 json 是
  repo 內容，下一次 Routine 一啟動就會讀到最新版）。
- 若某個 ID 不在這份 json 裡，`push_line.py` 預設當作啟用（向下相容，不會漏推）。
- 這份 json 只是「顯示名稱 + 開關」，實際能不能推播、token 有沒有效，還是要看
  `LINE_GROUP_IDS` / `LINE_CHANNEL_ACCESS_TOKEN` 這兩個環境變數。

### 3.4 設定 Claude Code 環境變數（**不是** GitHub Secrets）
因為系統跑在 Claude Routine（Anthropic 雲端），token 要放在 Claude 環境，
GitHub Secrets 餵不到這裡。
1. [claude.ai/code](https://claude.ai/code) → 點環境名稱的**雲朵圖示 ☁️** → 在本 repo
   用的環境上點**齒輪 ⚙️**。
2. **Environment variables** 欄，用 `.env` 格式填（等號兩邊無空格、值不加引號、一行一個）：
   ```
   LINE_CHANNEL_ACCESS_TOKEN=你的channel access token
   LINE_GROUP_IDS=Cxxxx1,Cxxxx2,Cxxxx3
   ```
3. **Network access**：確認允許 `api.line.me`（實測預設 Trusted 已可連 `api.line.me`）。
4. 存檔。**環境變數只對「新啟動的 session」生效**，改完要開新 session 才讀得到。

### 3.5 建立 Routine 排程
1. [claude.ai/code](https://claude.ai/code) → 左側 **Routines** → 建立 routine。
2. 綁定：本 repo、上面設好的環境、預設分支（見 3.6）。
3. 排程：每個工作日早上（台北 08:00）。工作日的最終判斷由 `check_workday.py`
   在 routine 內把關，所以就算排「每天」也沒關係，非工作日會自動跳過。
4. Routine 的提示詞：叫用 `daily-macro-report` skill 執行完整流程。

### 3.6 分支
- Routine 每次從 repo 的**預設分支**重新啟動，所以所有正式流程與 skill 必須在
  **預設分支**上。
- 目前預設分支：`claude/wizardly-franklin-m2yb1h`。
- 開發改動走 PR 合併回預設分支（本專案 PR #1、#2 即如此）。

---

## 4. 每日執行流程
詳見 `.claude/skills/daily-macro-report/SKILL.md`（步驟 0～7 與合規規則）。重點：
- 步驟 0 工作日閘門為 `false` 立即結束。
- 數值全部來自當次 WebSearch，不可沿用舊值；查不到的那列省略。
- 三段文案（今日重點 / 業務切入點 / 貼心小語）嚴守合規規則（第 9 節）。
- 推播**只發圖片**（`push_line.py` 不帶 `--text-file`）。

---

## 5. 產圖技術細節（HTML + Chromium，免 Pillow / 免外網）
Claude sandbox 的出網被政策擋（`pip install` / `apt install` 會 403），所以不用 Pillow。
改用 base image 內建元件：

- **中文字型**：文泉驛正黑 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`（繁簡皆支援）。
  `make_card_html.py` 的 CSS 指定 `font-family:'WenQuanYi Zen Hei'`。
- **無頭瀏覽器**：預裝 Chromium + 全域 Playwright。執行 `shot_card.js` 前需設：
  ```bash
  export PATH=/opt/node22/bin:$PATH
  export NODE_PATH=/opt/node22/lib/node_modules   # 讓 node 找到全域 playwright
  # PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers 環境已預設，勿執行 playwright install
  ```
- 指令：
  ```bash
  python3 scripts/make_card_html.py reports/<日期>/report.json reports/<日期>/card.html
  node scripts/shot_card.js reports/<日期>/card.html reports/<日期>/card.png reports/<日期>/card_preview.png
  ```
- 產完用 Read 檢視 `card.png` 確認中文正確、紅漲綠跌無誤再繼續。

---

## 6. 手動驗證 / 測試指令

```bash
# 1) 確認新 session 讀得到環境變數
echo "token 長度=${#LINE_CHANNEL_ACCESS_TOKEN}; 群組數=$(echo "$LINE_GROUP_IDS" | tr ',' '\n' | grep -c .)"

# 2) 確認 LINE token 有效（非發送，GET bot info，預期 200）
curl -sS -o /dev/null -w "%{http_code}\n" https://api.line.me/v2/bot/info \
  -H "Authorization: Bearer ${LINE_CHANNEL_ACCESS_TOKEN}"

# 3) 只發到第一個群組做測試（用永久 commit SHA 當圖床 URL 最穩）
SHA="<某個含該日報告的 commit SHA>"
BASE="https://raw.githubusercontent.com/NeilCCH/Daily-Macro-Report/${SHA}/reports/<日期>"
FIRST=$(echo "$LINE_GROUP_IDS" | cut -d',' -f1)
LINE_GROUP_IDS="$FIRST" python scripts/push_line.py \
  --image-url "${BASE}/card.png" --preview-url "${BASE}/card_preview.png"
```
用手機看那個群組，確認圖片正常顯示（LINE 是延遲抓圖，push API 就算圖壞也回 OK，
所以圖片能否顯示只能人眼確認）。

---

## 7. 疑難排解

| 症狀 | 原因 / 解法 |
|---|---|
| session 內 `LINE_*` 讀不到（空） | 環境變數只對新 session 生效；改完設定要**開新 session**。 |
| LINE 群組收到但**圖片破圖** | repo 不是 public；到 Settings → Change visibility 改 Public。 |
| `push_line.py` 回 401 | token 失效或錯誤；到 LINE Console Reissue，更新環境變數。 |
| `push_line.py` 回 403（api.line.me） | 網路政策未放行 `api.line.me`；到環境 Network access 放行。 |
| 卡片中文變成方框（tofu） | 字型路徑不對；確認 `wqy-zenhei.ttc` 存在，或改用其他 CJK 字型。 |
| `node: Cannot find module 'playwright'` | 沒設 `NODE_PATH=/opt/node22/lib/node_modules`。 |
| `pip install pillow` 失敗（403） | 預期行為；本系統不用 Pillow，改用 HTML+Chromium（第 5 節）。 |

---

## 8. 這個 sandbox 的網路事實（實測）
| 目標 | 從 Claude sandbox | 說明 |
|---|---|---|
| `api.line.me` | ✅ 通 | bot/info 回 200、push 回 OK |
| `pypi.org` / Ubuntu apt archive | ❌ 403 | 裝不了 Pillow/字型 → 改用內建元件 |
| `raw.githubusercontent.com` | ❌ 403（從 sandbox） | 但 **LINE 伺服器**在公開網路抓得到，故圖床仍可行 |
> 註：舊 README 曾誤稱 api.line.me 被擋，實測為通；請以本表為準。

---

## 9. 合規防呆規則（每次產出都必須遵守）
1. 業務切入點是「引導關懷與對話」的起手式，促成需求檢視，不是買賣指令。
2. 不得對未來市場走勢做方向性預測並作為買賣/投保依據。
3. 不得對任何特定商品做報酬保證或收益承諾。
4. 全文禁止出現：「保證」「一定」「穩賺」「最佳時機」「不會賠」。
5. 涉及商品時只談「功能與資產配置角色」，不談「績效預期」。

---

## 10. 年度維護
`data/taiwan_calendar_<year>.json` 是工作日判斷的資料來源（含國定假日與補班日）。
**每年年底要新增次年度的 JSON**（取自台灣政府公開行事曆資料集），否則跨年後
`check_workday.py` 會找不到當年度資料。

---

## 11. 關鍵決策紀錄（為什麼是現在這樣）
- **不用 Gemini**：改由 Claude 親自查證撰寫，品質與合規把關更穩；移除 GEMINI_API_KEY
  與 GitHub Actions workflow，避免與 Claude Routine 重複發報。（PR #1）
- **不用 Pillow、改 HTML+Chromium**：Claude sandbox 出網被擋裝不了 Pillow/字型，
  但 base image 已內建 Chromium 與 WQY 中文字型。（PR #1）
- **只發圖片、不發文字**：依 Neil 需求，卡片已含完整資訊。（PR #2）
- **token 放 Claude 環境變數、非 GitHub Secrets**：因系統跑在 Claude Routine，
  GitHub Secrets 只餵 GitHub Actions，餵不到 Claude sandbox。

---

## 12. 行情 API 取代部分 WebSearch（已實作，2026-07-14 上線）

**背景**：WebSearch 常回傳過時或彼此矛盾的數字（同一數據要交叉查證 2-3 次），拖慢每日執行時間。評估後改用 Alpha Vantage / Twelve Data / Oil Price API 這幾個有免費額度的行情 API，取代部分 WebSearch。

**網路政策**：sandbox 的網路政策是**明確白名單**，不是黑名單擋特定站。要讓新的 API 網域能連，必須到 Claude Code 環境設定 → **Network access** → 選 **Custom** → 把網域加入允許清單（不要選 Full，沒必要開放到套件庫等無關網域）：
```
www.alphavantage.co
api.twelvedata.com
api.oilpriceapi.com
```
「Also include default list of common package managers」**不勾**——這次不需要 pip/npm，維持最小權限。

**已實作、每天會自動使用**（`scripts/fetch_market_data.py`，2026-07-14 實測全部跑通）：

| 資料 | 來源 | 備註 |
|---|---|---|
| 美 10Y 公債殖利率 | Alpha Vantage `TREASURY_YIELD` | 免費額度緊：**25 次/天**，腳本只呼叫 1 次，夠每天用但不要拿去做額外測試 |
| 四組匯率（TWD/JPY/CNY/EUR） | Twelve Data `/quote` | 免費 800 次/天、8 次/分鐘 |
| 黃金 | Twelve Data `/quote?symbol=XAU/USD` | 免費版可查 |
| WTI／Brent | Oil Price API `/v1/prices/latest?code=...`（`WTI_USD`／`BRENT_CRUDE_USD`） | 免費 200 次/月，每天 2 次，一個月約 44 次，額度充裕 |

**確認免費版查不到、仍然固定用 WebSearch 的項目**（2026-07-14 實測）：
- **美股三指數**：S&P 500（Twelve Data 免費版查 `SPX` 回「需升級 Grow/Venture 方案」）、NASDAQ、費半 SOX（Twelve Data 的 symbol_search 完全查不到這兩個 symbol，只找得到不相關市場的同名 ETF）
- **亞股**：日經 225、台股加權(TAIEX)、台指期夜盤——同樣不在 Twelve Data 免費版的 symbol 清單裡
- **白銀**：Twelve Data 的 `XAG/USD` 回「需升級方案」（金/銀待遇不一致，黃金免費、白銀要付費）；Oil Price API 只做原油，沒有貴金屬
- **`highlights` 新聞事件**：兩個 API 都不含新聞，固定要 WebSearch

**運作方式**：`fetch_market_data.py` 對每一項獨立呼叫、獨立 try/except，任何一項失敗（額度用完、網路問題、免費版不支援）就直接從輸出省略該欄位，**不會塞假資料或 null**。SKILL.md 步驟 1a 會先跑這支腳本，只對它沒回傳到的欄位才動用 WebSearch——所以就算某天 API 全掛，流程還是能完全靠 WebSearch 跑完，不會中斷。

**環境變數**（已設定在 Claude Code 環境變數，`.env` 格式）：
```
ALPHA_VANTAGE_API_KEY=...
TWELVE_DATA_API_KEY=...
OIL_PRICE_API_KEY=...
```
