---
name: daily-macro-report
description: 產出每日全球總經晨報（繁體中文文字訊息＋PNG 卡片）並推播到指定 LINE 群組。工作日早晨自動執行的完整流程：查證台灣工作日 → WebSearch 抓最近收盤行情與當日財經新聞 → 依合規規則寫三段文案 → 渲染卡片 → git 提交當圖床 → 推播 LINE。設計給 Claude Code Routine（排程自動化）每個工作日 07:00（台北）呼叫。
---

# 每日總經晨報自動化

你正在為一位資深保險經紀業務主管（Neil）執行每日早晨的全球總經晨報任務。輸出會自動推播到他的 LINE 業務群組。本任務在**台灣時間早晨**於雲端自動執行，全程無人監看，請嚴格照下列步驟與規格完成，不要中途停下來問問題。

最終要在 `reports/<YYYY-MM-DD>/` 產出並推播：
- `report.json`：結構化資料（給 `make_card_html.py`）
- `line_text.txt`：可直接貼到 LINE 的繁體中文文字訊息
- `card.html` / `card.png` / `card_preview.png`：晨報卡片（HTML 排版 → Chromium 截圖）

---

## 步驟 0：工作日閘門（最先執行，決定要不要繼續）

```bash
python scripts/check_workday.py
```

- 這支腳本讀 `data/taiwan_calendar_<年>.json`，會處理台灣國定假日與補班日。
- 它在 stdout 印出 `true` 或 `false`（工作日資訊印在 stderr）。
- **若印出 `false`（週末或國定假日）→ 立刻結束整個任務，不要抓資料、不要產圖、不要推播。** 直接回報「今日非台灣工作日，跳過。」
- 若印出 `true` → 繼續以下步驟。

`report_date`（台北日期）取 `YYYY-MM-DD`；卡片標題日期用 `YYYY/MM/DD`。

---

## 步驟 0.5：今日是否已產出過（防止 Routine 重複觸發造成重複推播）

```bash
DEFAULT_BRANCH="$(git ls-remote --symref origin HEAD | awk '/^ref:/{print $2}' | sed 's#refs/heads/##')"
git fetch origin "${DEFAULT_BRANCH}" || echo "FETCH_FAILED"
DATE="<YYYY-MM-DD>"
git show "origin/${DEFAULT_BRANCH}:reports/${DATE}/report.json" > /dev/null 2>&1 && echo "REPORT_EXISTS=true" || echo "REPORT_EXISTS=false"
git show "origin/${DEFAULT_BRANCH}:reports/${DATE}/push_state.json" 2>/dev/null || echo "NO_PUSH_STATE"
```

- 用 `git ls-remote --symref origin HEAD` 動態找出**目前的預設分支**（不要寫死分支名稱）。
- **「報告已產出」與「各群組已推播」是兩件事**，不要用 `report.json` 存在當作推播完成：
  - `REPORT_EXISTS=false` → 今天還沒做，照步驟 1 起往下做。
  - `REPORT_EXISTS=true` 且 `push_state.json` 裡**所有啟用群組**都是 `accepted` → 立刻結束，回報「今日報告已產出且各群組均已被 LINE 接受請求，本次為重複觸發，跳過。」
  - `REPORT_EXISTS=true` 但沒有 `push_state.json`（舊流程產出的報告）→ 視為已推播，立刻結束，不重送。
  - `REPORT_EXISTS=true` 且 `push_state.json` 有未完成的群組（`failed_retryable`／`pending`）→ **續推模式**：不重抓資料、不重產圖；用 `git checkout "origin/${DEFAULT_BRANCH}" -- "reports/${DATE}"` 取回當日資產，直接跳到步驟 7，圖片網址沿用 `push_state.json` 內的 `image_url`／`preview_url`（不要重新組網址）。狀態為 `manual_review`、`quota_exceeded`、`auth_error` 的群組不要自動重送，回報給 Neil。
  - 若 `git fetch` 失敗或讀不到狀態（`FETCH_FAILED`）→ 狀態**未知**，**不得當作「尚未執行」放行**：不要推播，回報後結束。
- 這一步能防止 Routine 同一天觸發兩次時重複推播 LINE。真正的防重複是步驟 7 中每次發送都帶固定的 `X-Line-Retry-Key`（24 小時內同一筆發送重送會被 LINE 以 409 回覆「已接受」）；這裡的檢查只是第一道。前提仍是步驟 8（合併回預設分支）確實有執行，狀態檔才能跨執行保存。

---

## 步驟 1：抓資料（API 優先，WebSearch／WebFetch 補缺口）

### 步驟 1a：先跑 API 腳本，拿到的欄位不用再搜尋

```bash
/usr/bin/python3 scripts/fetch_market_data.py --diagnostics-file "reports/<YYYY-MM-DD>/fetch_diagnostics.json"
```

> 若直譯器缺 `requests`，腳本不會崩潰，而是對每一項輸出 `missing_dependency` 診斷；此時改用 `/usr/bin/python3` 重跑（它有 `requests`），仍不行才整批改走 WebSearch。

這支腳本會呼叫 Alpha Vantage（10Y 公債殖利率）、Twelve Data（USD/TWD、USD/JPY、
USD/CNY、USD/EUR、黃金 XAU/USD）、Oil Price API（WTI、Brent），回傳一份 JSON，
內含 `fx` 與 `commodity_rate` 兩個 section 裡已經能直接用的列（欄位格式跟
`report.json` schema 完全一致，可以直接搬進去）。**任何一項抓失敗（網路、額度、
需要付費方案、資料型別異常、報價過期）會直接從輸出省略，不會給假資料**，且每一項的原因
會寫在 stderr 與 `--diagnostics-file`（`missing_key`／`timeout`／`rate_limited`／`auth_error`／
`schema_error`／`stale`／`unknown_change`…，不含任何金鑰）。看診斷就知道缺哪幾項、為什麼缺，
再對那幾項補 WebSearch。每一列另外帶來源與時間：`source`、`source_url`（不含 API key）、
`as_of`、`market_date`、`fetched_at`、`quote_kind`，**搬進 `report.json` 時要原樣保留**。
`dir` 為 `unknown` 表示缺少比較值（不是持平）：請用 WebSearch 補到明確漲跌，補不到就把該列的
箭頭交給卡片顯示「–」或整列省略；只有確認變動為 0 才可標 `flat`。

**已知這支腳本查不到、一定要靠 WebSearch 的項目**（免費方案沒有這些冷門標的，
2026-07-14 實測結論）：
- 美股三指數：S&P 500、NASDAQ、費半 SOX
- 亞股：日經 225、台股加權、台指期夜盤
- 白銀（Twelve Data 白銀要付費方案；Oil Price API 只做原油）

`highlights`（今日重點的新聞事件）也一定要另外 WebSearch，這支腳本不處理新聞。

若三組 API key（`ALPHA_VANTAGE_API_KEY`／`TWELVE_DATA_API_KEY`／`OIL_PRICE_API_KEY`）
未設定或網路被擋，腳本對應欄位會直接是空的，等同於整個 `fx`／`commodity_rate`
都要 WebSearch——不影響任務繼續進行。

### 數據時間的正確理解（重要，避免抓到不存在的「今天」數據）

- **美股、費半、美債殖利率、原物料**：取「最近一次美國交易日收盤」（多半是台北時間前一晚的場次）。用「最近收盤／latest close」概念搜尋，不要假設有「今天」的美股收盤。
- **亞股（日經、台股）**：亞洲已開盤取最新即時或最近收盤；若尚未開盤，取上一交易日收盤並在文案中註明。
- **台指期夜盤**：夜盤交易時段為前一交易日 15:00 至當日凌晨 05:00，在台北 07:00 產出報告時**夜盤已經收盤**，是全篇最新鮮的一筆數據（比還沒開盤的台股加權更即時），直接取當日已結束的夜盤收盤價，不需要額外註明「上一交易日」。
- **匯率**：取最近即時報價。
- **美股休市（美國假日）**：`us_market` 區塊留空、並在 `us_market_closed` 設 `true`；不可沿用舊數據、不可杜撰。

### 台指期夜盤查詢技巧（必查欄位，2026-09-04 實測心得）

`台指期夜盤` 是亞股區固定三列之一（見步驟 3 規則），**不可因為查不到就直接省略**——
這是 Neil 特別要求的必要欄位，比其他欄位值得多花一點查證成本，此欄位不受下方
「加速原則」2 次搜尋上限限制，可視需要多搜幾次：

1. 優先搜尋固定欄位標題「**M月D日 期交所夜盤行情**」（例如「9月3日 期交所夜盤行情」），
   這是經濟日報／聯合新聞網每天固定發布的夜盤收盤稿，會直接寫出收盤點數與漲跌點數，
   是最權威的來源。用完整日期（月+日）去搜，不要只搜「今日」。
2. 找不到固定收盤稿時，改搜尋接近 05:00（夜盤收盤時間）的**帶時間戳即時報價**，
   關鍵字如「台指期 夜盤 收在」「台指期 夜盤 XX:XX 更新時報」，找時間戳記最接近
   05:00、且日期正確（前一交易日 15:00 至當日 05:00 那一個夜盤）的報價當近似收盤值，
   避免誤用到「今晚才剛開始」的下一個夜盤（那是給明天用的）。
3. 台指期夜盤新聞常見同名文章橫跨不同年份／不同交易日，務必核對文章內容或摘要中
   出現的日期是否與目標日期一致，不一致就跳過重找。
4. 真的多次嘗試（4－5 次內）仍完全找不到任何當日夜盤報價才可省略該列；省略時務必在
   最終回報中明確寫出「今日台指期夜盤查無資料，已省略」，不要默默跳過不提。

### 用 WebSearch 搜尋步驟 1a 沒抓到的項目

步驟 1a 的 API 腳本正常狀況下已經能拿到 `fx`（四組匯率）與 `commodity_rate` 裡的 WTI／Brent／黃金／10Y 殖利率。WebSearch 只需要補以下缺口：

- `"S&P 500 close"`、`"NASDAQ close"`、`"費半 SOX 收盤"`
- `"日經 225"`、`"台股加權 指數"`、`"台指期 夜盤 收盤"`
- `"白銀 金價"`（Twelve Data 免費版查不到，固定要 WebSearch）
- `"Fed 最新發言"` 或 `"今日 全球 經濟 重點"`（給 highlights 用，每天都要查）

若步驟 1a 因為網路或額度問題整組失敗（`fx`／`commodity_rate` 是空的），再補查：
`"USD/TWD 匯率"`、`"USD/JPY"`、`"USD/CNY"`、`"USD/EUR"`、`"WTI 原油"`、`"Brent 原油"`、`"黃金 金價"`、`"美國 10年期 公債殖利率"`。

若搜尋結果指向財經新聞網站（鉅亨、Bloomberg、Reuters、MoneyDJ、TheStreet），可用 **WebFetch** 補充細節。比對前一交易日數據，標出漲跌方向。

**每一個數值都須來自 API 或實際搜尋結果，不可推估、不可沿用記憶中的舊值。查不到的那一列直接省略，不留空欄位、不寫「N/A」。**

### 加速原則（避免不必要的重複搜尋）

WebSearch 常會回傳過時或彼此矛盾的數字（例如把好幾天前的舊收盤數字標成「今日」）。為了不讓查證迴圈無限拉長：

- 查詢字串**帶精確日期**（如 `2026-07-13` 或 `7月13日`），比只寫「今日」「最新」更容易命中正確那天的報導。
- 台股加權、日經 225 這類容易撈到舊快取的項目，優先信任**帶明確日期的新聞標題**（如「XX月XX日盤後：加權指數收跌...」），而不是泛用即時報價頁的摘要。
- 每個數據點最多**再次確認 1 次**（也就是最多 2 次搜尋/該數據點）；兩次搜尋結果仍衝突時，採用敘事最一致、來源最具體（有明確日期標題）的那個，並繼續往下走，不要無限重查。**例外：`台指期夜盤` 這個必要欄位不受此 2 次上限限制**，見上方「台指期夜盤查詢技巧」。
- 同一輪能平行下的查詢就一次平行送出（多個 WebSearch 放在同一個 tool call 訊息裡），不要逐一序列查詢。

---

## 步驟 2：寫文案（三段，嚴守合規）

### 今日重點（highlights）
1–2 句，當日最關鍵的市場事件或央行動態（例如 Fed 發言、地緣事件、財報季氛圍）。

### 今日業務切入點（business_angle）
一句話，依當日「最突出的情境」從下表擇一，轉化成「引導關懷與檢視」的對話起手式。**語氣＝關懷檢視，不是買賣擇時指令。**

| 當日情境 | 切入方向 |
|---|---|
| 美債殖利率偏高／高利率環境 | 從「資產配置中固定收益的角色」切入，聊美元利變型保單在長期規劃裡的定位（談角色，不談擇時） |
| 台幣走貶 | 從「多幣別資產分散」切入，聊外幣保單在匯率波動下的配置意義 |
| 股市創高 | 從「定期檢視與停利紀律」切入，聊投資型保單帳戶值得定期回顧的習慣（談紀律，不喊鎖利） |
| 地緣風險升溫／黃金上漲 | 從「家庭保障缺口」切入，聊風險保障是否仍足夠 |
| 通膨數據偏高 | 從「長期購買力與保障額度」切入，聊保額是否需隨生活成本檢視 |
| 降息預期升溫／利率轉折 | 從「鎖利的時間價值」切入，聊變動型保單利率的長期觀察點（談觀察，不做預測） |

### 貼心小語（caring_note）
一句話，針對當天節日或農民曆節氣（擇一）應景，讓人感受到窩心。

### 合規防呆規則（每段都必須遵守）
1. 業務切入點是「引導關懷與對話」的起手式，目的在促成需求檢視，不是買賣指令。
2. 不得對未來市場走勢做方向性預測，並以此作為買賣或投保依據。
3. 不得對任何特定商品做報酬保證或收益承諾。
4. 全文禁止出現：「保證」「一定」「穩賺」「最佳時機」「不會賠」。
5. 涉及商品時只談「功能與資產配置角色」，不談「績效預期」。

---

## 步驟 3：寫出 `report.json`

存到 `reports/<YYYY-MM-DD>/report.json`，格式（`make_card_html.py` 依此渲染）：

```json
{
  "report_date": "2026/07/02",
  "us_market_closed": false,
  "sections": {
    "us_market": [
      {"label": "S&P 500", "value": "7,483", "change_pts": "16.8", "change_pct": "0.22%", "dir": "down"},
      {"label": "NASDAQ", "value": "26,040", "change_pts": "173.5", "change_pct": "0.66%", "dir": "down"},
      {"label": "費半 SOX", "value": "12,940", "change_pts": "40.2", "change_pct": "0.31%", "dir": "down"}
    ],
    "asia_market": [
      {"label": "日經 225", "value": "70,474", "change_pts": "414.9", "change_pct": "0.59%", "dir": "up"},
      {"label": "台股加權", "value": "47,034", "change_pts": "908.6", "change_pct": "1.97%", "dir": "up"},
      {"label": "台指期夜盤", "value": "47,120", "change_pts": "84.0", "change_pct": "0.18%", "dir": "up"}
    ],
    "fx": [
      {"label": "USD / TWD", "value": "31.83", "change_pts": "0.03", "change_pct": "", "dir": "up"},
      {"label": "USD / JPY", "value": "162.0", "change_pts": "0.4", "change_pct": "", "dir": "up"},
      {"label": "USD / CNY", "value": "6.80", "change_pts": "0.00", "change_pct": "", "dir": "flat"},
      {"label": "USD / EUR", "value": "0.877", "change_pts": "0.002", "change_pct": "", "dir": "down"}
    ],
    "commodity_rate": [
      {"label": "WTI 原油", "value": "$68.77", "change_pts": "$0.76", "change_pct": "1.1%", "dir": "down"},
      {"label": "Brent 原油", "value": "$72.20", "change_pts": "$0.73", "change_pct": "1.0%", "dir": "down"},
      {"label": "黃金", "value": "$4,003", "change_pts": "$35.4", "change_pct": "0.88%", "dir": "down"},
      {"label": "白銀", "value": "$58.47", "change_pts": "$1.46", "change_pct": "2.43%", "dir": "down"},
      {"label": "美 10Y 公債", "value": "4.46%", "change_pts": "0.02%", "change_pct": "", "dir": "up"}
    ]
  },
  "highlights": "……（今日重點 1–2 句）",
  "business_angle": "……（今日業務切入點一句）",
  "caring_note": "……（貼心小語一句）",
  "source_note": "資料來源：TheStreet / Yahoo Finance / Reuters・數據為 2026/07/01 最近交易日"
}
```

規則：
- `dir` 只能是 `"up"`（漲，紅）／`"down"`（跌，綠）／`"flat"`（**確認為零變動**，`→`）／`"unknown"`（缺比較值，卡片顯示「–」）。台股慣例：漲紅跌綠。變動顯示為 0.00 時一律標 `flat`，不得標 `down`／`up`。
- **每一列都要有來源紀錄**（WebSearch 補的也要）：`source`（來源名稱）、`source_url`（公開網址，不得含金鑰）、`as_of`（報價／收盤時間，ISO）、`market_date`（交易日 `YYYY-MM-DD`）、`quote_kind`（`realtime`／`daily_close`／`futures_night`／`daily_yield`）。美股收盤的 `market_date` 必須是上一個美股交易日；亞股為報告日或前一交易日；殖利率可落後最多 2 個營業日。
- 查不到而省略的列，要在頂層 `omitted` 陣列留下原因：`{"label": "費半 SOX", "reason": "兩次搜尋仍查無可靠收盤"}`；`us_market_closed=true` 時 `us_market` 必須為空。
- 產圖與推播前會跑 `scripts/validate_report.py`，缺日期、`NaN`、錯方向、日期不符、休市矛盾、禁用詞、缺來源都會被擋下（錯誤即停止，不會默默改成持平）。
- 每一列都要同時給 `change_pts`（漲跌點數／絕對值，不含正負號，只放數字＋原本單位如 `$`／`%`）與 `change_pct`（漲跌百分比）。兩者其中一個沒有明確數字時給 `""`；只有兩者都查不到才整列省略（見上方「台指期夜盤查詢技巧」與步驟 1 的通則）。
- `change_pts` 的單位跟著 `value` 走：指數類（美股／亞股）用純數字（如 `"16.8"`）；原物料用 `$` 開頭（如 `"$0.76"`）；殖利率用 `%` 結尾表示變動了幾個百分點（如 `"0.02%"`，代表 2 個基點）；匯率用該幣別小數位（如 `"0.03"`）。
- 美股區固定三列順序：S&P 500 → NASDAQ → 費半 SOX。
- 亞股區固定三列順序：日經 225 → 台股加權 → 台指期夜盤。
- `fetch_market_data.py`（步驟 1a）現在會直接回傳 `change_pts`＋`change_pct`，兩個都能直接搬進 `report.json`，不用再自己算。
- `source_note` 的日期填「最近交易日」。

---

## 步驟 4：寫出 `line_text.txt`（LINE 文字訊息）

**用腳本由 `report.json` 產生，不要手打**（避免與卡片數字不一致、符號寫錯）：

```bash
python3 scripts/make_line_text.py "reports/<YYYY-MM-DD>/report.json" "reports/<YYYY-MM-DD>/line_text.txt"
```

存到 `reports/<YYYY-MM-DD>/line_text.txt`，格式（方便直接貼上 LINE）：

```
【早安報報｜每日總經速報】2026/07/02
夥伴早安 ☀

📈 美股
S&P 500：7,483 🔻16.8（0.22%）
NASDAQ：26,040 🔻173.5（0.66%）
費半 SOX：12,940 🔻40.2（0.31%）

🌏 亞股
日經 225：70,474 🔺414.9（0.59%）
台股加權：47,034 🔺908.6（1.97%）
台指期夜盤：47,120 🔺84.0（0.18%）

💱 匯率
USD/TWD：31.83 🔺0.03
USD/JPY：162.0 🔺0.4
USD/CNY：6.80 ➡️
USD/EUR：0.877 🔻0.002

🛢 原物料 / 利率
WTI 原油：$68.77 🔻$0.76（1.1%）
Brent 原油：$72.20 🔻$0.73（1.0%）
黃金：$4,003 🔻$35.4（0.88%）
白銀：$58.47 🔻$1.46（2.43%）
美 10Y 公債：4.46% 🔺0.02%

🔥 今日重點
……

💼 今日業務切入點
……

❤️ 貼心小語
……
```

- 漲用 🔺、跌用 🔻、持平用 ➡️。
- 漲跌點數放箭頭後面，百分比有值時用全形括號附在點數後面（`🔺414.9（0.59%）`）；只有其中一個有值就只顯示那一個（不留空括號）。
- 查不到的那一列直接刪除，不留空欄位。
- 結尾就是「❤️ 貼心小語」那一行，不加多餘客套話。

---

## 步驟 5：渲染卡片（HTML → Chromium 截圖）

Claude Code 雲端 sandbox 的出網被政策擋掉（`pip install`／`apt install` 會 403），
所以**不要用** Pillow 版的 `make_card.py`。改用 base image 內建的文泉驛正黑字型
（`wqy-zenhei.ttc`）＋預裝的無頭 Chromium（全域 Playwright）產圖，全程不需外網、
不需裝任何套件：

```bash
export PATH=/opt/node22/bin:$PATH
export NODE_PATH=/opt/node22/lib/node_modules   # 讓 node 找到全域 playwright
# PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers 環境已預設

python3 scripts/validate_report.py "reports/<YYYY-MM-DD>/report.json" --for-push
python3 scripts/make_card_html.py \
  "reports/<YYYY-MM-DD>/report.json" "reports/<YYYY-MM-DD>/card.html"
node scripts/shot_card.js \
  "reports/<YYYY-MM-DD>/card.html" \
  "reports/<YYYY-MM-DD>/card.png" \
  "reports/<YYYY-MM-DD>/card_preview.png"
```

會輸出 `card.html`、`card.png` 與 `card_preview.png`（<1MB，給 LINE previewImageUrl）。
紅漲綠跌、持平 `→`、未知方向 `–`、卡片高度依內容自動撐開。`make_card_html.py` 本身也會先驗證，驗證失敗不會產出 HTML；重新渲染舊報告才可加 `--legacy`（舊模式**永遠不能**通過推播關卡）。產完後用 Read 檢視 `card.png`，確認中文
有正確渲染再繼續。

---

## 步驟 6：提交圖片當圖床，取得固定在 commit SHA 的公開 URL

LINE 圖片訊息需要公開 HTTPS URL。**一律用 commit SHA 組網址，不要用分支名稱**——工作分支是一次性的，分支被刪或改動後，已送出的 LINE 訊息就會失去原圖。

```bash
DATE="<YYYY-MM-DD>"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git add "reports/${DATE}"
git commit -m "Daily macro report ${DATE}"
git push origin "HEAD:${BRANCH}"
SHA="$(git rev-parse HEAD)"     # 含有當日圖片資產、且已成功 push 的完整 40 碼 SHA
BASE="https://raw.githubusercontent.com/NeilCCH/Daily-Macro-Report/${SHA}/reports/${DATE}"
# 圖片 URL：${BASE}/card.png 與 ${BASE}/card_preview.png（repo 需為 public）
```

- 兩張圖用同一個 SHA；重試／續推一律沿用 `push_state.json` 裡記錄的網址，**不要重新解析 HEAD**。
- `push_line.py --git-sha "${SHA}"` 只能證明「本機 git 物件存在」；sandbox 讀不到 raw.githubusercontent.com（403），**「公開圖片可讀」無法在 sandbox 內驗證，回報時不可宣稱已驗證外網可讀**。

---

## 步驟 7：推播到 LINE 群組

需要環境變數 `LINE_CHANNEL_ACCESS_TOKEN` 與 `LINE_GROUP_IDS`（逗號分隔），以及網路白名單允許 `api.line.me`。

> 註：本流程走 Claude Code Routine，這兩個環境變數要設在 **Claude Code 環境（Environment）設定**裡，不是 GitHub Secrets（GitHub Secrets 只給 GitHub Actions 用）。若 sandbox 內讀不到（`echo $LINE_GROUP_IDS` 為空），就無法自動推播，此時保留已產出的 `card.png` 與 `line_text.txt` 供人工張貼，並回報缺少環境變數。

**只推送圖片卡片，不推送文字訊息**（不要帶 `--text-file`）：

```bash
/usr/bin/python3 scripts/push_line.py \
  --report "reports/${DATE}/report.json" \
  --image-url "${BASE}/card.png" \
  --preview-url "${BASE}/card_preview.png" \
  --git-sha "${SHA}"
# 步驟 0.5 查不到遠端狀態時，加 --remote-state unknown（腳本會拒絕發送，exit 3）
# 續推模式：先把預設分支的 push_state.json 取回 reports/${DATE}/，網址改用其中記錄的值
```

`push_line.py` 的行為：
- **先過關卡**（exit 2 就是沒送）：`report.json` 通過 `--for-push` 驗證（日期必須是今天的台北日期）、兩張 PNG 可解碼且大小合規、圖片網址固定在 SHA 且日期正確。
- 每次發送都帶 `X-Line-Retry-Key`（由「報告日期＋群組＋內容雜湊」決定，同一筆發送永遠同一把 key，跨執行也一樣）。timeout／5xx／一般 429 最多重試 3 次（1、2、4 秒）；400／401／403／月額度用完不重送；409 只有回應帶 `x-line-accepted-request-id` 才算已接受。
- 逐群組結果寫入 `reports/${DATE}/push_state.json`（只存群組 ID 的雜湊，不含 token 與群組 ID）。已 `accepted` 的群組續推時不會重送；第一次嘗試超過 24 小時仍未確認者標為 `manual_review`，不自動重送。
- 停用群組（`data/line_groups.json`）不送；`LINE_GROUP_IDS` 重複 ID 只處理一次。
- 結束碼：0 全部啟用群組都被 LINE 接受請求；1 有群組未完成；2 關卡未過；3 遠端狀態未知。
- **用語**：HTTP 200 只代表「LINE 接受了這次 API 請求」，不代表每位成員都收到或圖片顯示正常；回報請寫「LINE 已接受請求」，不要寫「已送達」。

**推播後，把狀態檔也提交並推上去**（它是跨執行的唯一紀錄，Routine 工作目錄會被丟棄）：

```bash
git add "reports/${DATE}/push_state.json"
git commit -m "Record LINE push state ${DATE}"
git push origin "HEAD:${BRANCH}"
```

（這個 commit 不改變圖片網址：網址固定在步驟 6 的 SHA。）

`line_text.txt` 仍會產生，作為卡片內容來源與 repo 記錄，但**不推送到 LINE**。

**若任何群組推播失敗**（例如月推播則數上限 429、token 失效、環境變數缺失，或其他任何非「成功」的結果），**不要只是把失敗寫進最終回報就結束**——改用 `SendUserFile` 直接把 `reports/<DATE>/card.png` 傳給 Neil 本人（可加 caption 簡述失敗原因，例如「LINE推播因月則數上限失敗，附上圖卡供您手動轉發」），讓他當下就能手動轉發到群組，不必等看到回報文字才發現要跟他要圖。這一步不可省略，且不受「非同步/不打擾」的推播節流限制——失敗當下就是需要他行動的時刻。

---

## 步驟 8：自動開 PR 並立即合併回預設分支（關鍵，勿省略）

Routine 每次都是從預設分支開一個新的一次性工作分支執行，**若不做這一步，今天的成果會孤立在這個工作分支上，預設分支永遠看不到**，隔天 Routine 又會從舊的預設分支重新開始，形成「看起來每天都沒跑」的假象。

```bash
DEFAULT_BRANCH="$(git ls-remote --symref origin HEAD | awk '/^ref:/{print $2}' | sed 's#refs/heads/##')"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
```

用 GitHub MCP 工具（`mcp__github__create_pull_request`）從 `$BRANCH` 開一個 PR 到 `$DEFAULT_BRANCH`，標題如「Daily macro report <YYYY-MM-DD>」。**開完後立即用 `mcp__github__merge_pull_request` 自動合併**，不要等待人工審核——這是每日固定格式的資料寫入（report.json / line_text.txt / card 圖片），內容已依合規規則產出，屬於低風險自動化流程，Neil 已同意此步驟免人工 review 直接合併。

若 `create_pull_request` 或 `merge_pull_request` 失敗（例如權限不足、衝突），不要中止整個任務——LINE 推播已完成才是最優先的交付物；記錄失敗原因於最終回報中，讓 Neil 知道需要手動合併。

---

## 完成準則

- 工作日閘門為 `true` 才執行；否則明確回報已跳過。
- 步驟 0.5 檢查今日尚未產出過，才繼續往下執行。
- `report.json`、`line_text.txt`、`card.png`、`card_preview.png` 都已產出並 commit/push。
- 每個 LINE 群組都收到**圖片卡片**（不推送文字），`push_line.py` 全部回報 OK；若有任何群組失敗，已用 `SendUserFile` 把 `card.png` 直接傳給 Neil。
- PR 已開立並合併回預設分支（步驟 8）；若合併失敗，已在回報中明確說明。
- 全程遵守合規防呆規則；數字全部來自當次搜尋。
