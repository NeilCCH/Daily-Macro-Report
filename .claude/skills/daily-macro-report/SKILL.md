---
name: daily-macro-report
description: 產出每日全球總經晨報（繁體中文文字訊息＋PNG 卡片）並推播到指定 LINE 群組。工作日早晨自動執行的完整流程：查證台灣工作日 → WebSearch 抓最近收盤行情與當日財經新聞 → 依合規規則寫三段文案 → 渲染卡片 → git 提交當圖床 → 推播 LINE。設計給 Claude Code Routine（排程自動化）每個工作日 08:00（台北）呼叫。
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
git fetch origin "$(git ls-remote --symref origin HEAD | awk '/^ref:/{print $2}' | sed 's#refs/heads/##')" 2>/dev/null || true
DEFAULT_BRANCH="$(git ls-remote --symref origin HEAD | awk '/^ref:/{print $2}' | sed 's#refs/heads/##')"
git show "origin/${DEFAULT_BRANCH}:reports/<YYYY-MM-DD>/report.json" > /dev/null 2>&1 && echo "ALREADY_DONE=true" || echo "ALREADY_DONE=false"
```

- 用 `git ls-remote --symref origin HEAD` 動態找出**目前的預設分支**（不要寫死分支名稱，因為分支名稱會變）。
- 檢查該預設分支上 `reports/<今天日期>/report.json` 是否已存在。
- **若已存在（`ALREADY_DONE=true`）→ 立刻結束整個任務**，不要重抓資料、不要重推播。直接回報「今日報告已於預設分支產出，本次為重複觸發，跳過。」
- 這一步能防止 Routine 同一天觸發兩次時重複推播 LINE（LINE 有每月推播則數上限，重複推播只會更快把配額用完）。這個機制能生效的前提是步驟 8（自動合併回預設分支）確實有執行；若步驟 8 沒做，這裡永遠檢查不到，請優先確保步驟 8 落實。

---

## 步驟 1：抓資料（WebSearch／WebFetch）

### 數據時間的正確理解（重要，避免抓到不存在的「今天」數據）

- **美股、費半、美債殖利率、原物料**：取「最近一次美國交易日收盤」（多半是台北時間前一晚的場次）。用「最近收盤／latest close」概念搜尋，不要假設有「今天」的美股收盤。
- **亞股（日經、台股）**：亞洲已開盤取最新即時或最近收盤；若尚未開盤，取上一交易日收盤並在文案中註明。
- **台指期夜盤**：夜盤交易時段為前一交易日 15:00 至當日凌晨 05:00，在台北 08:00 產出報告時**夜盤已經收盤**，是全篇最新鮮的一筆數據（比還沒開盤的台股加權更即時），直接取當日已結束的夜盤收盤價，不需要額外註明「上一交易日」。
- **匯率**：取最近即時報價。
- **美股休市（美國假日）**：`us_market` 區塊留空、並在 `us_market_closed` 設 `true`；不可沿用舊數據、不可杜撰。

### 用 WebSearch 搜尋以下關鍵字（需多次搜尋，取最新即時資訊）

- `"S&P 500 close"`、`"NASDAQ close"`、`"費半 SOX 收盤"`
- `"USD/TWD 匯率"`、`"USD/JPY"`、`"USD/CNY"`、`"USD/EUR"`
- `"WTI 原油"`、`"Brent 原油"`、`"黃金 金價"`、`"白銀"`
- `"日經 225"`、`"台股加權 指數"`、`"台指期 夜盤 收盤"`
- `"美國 10年期 公債殖利率"`
- `"Fed 最新發言"` 或 `"今日 全球 經濟 重點"`

若搜尋結果指向財經新聞網站（鉅亨、Bloomberg、Reuters、MoneyDJ、TheStreet），可用 **WebFetch** 補充細節。比對前一交易日數據，標出漲跌方向。

**每一個數值都須來自實際搜尋結果，不可推估、不可沿用記憶中的舊值。查不到的那一列直接省略，不留空欄位、不寫「N/A」。**

### 加速原則（避免不必要的重複搜尋）

WebSearch 常會回傳過時或彼此矛盾的數字（例如把好幾天前的舊收盤數字標成「今日」）。為了不讓查證迴圈無限拉長：

- 查詢字串**帶精確日期**（如 `2026-07-13` 或 `7月13日`），比只寫「今日」「最新」更容易命中正確那天的報導。
- 台股加權、日經 225 這類容易撈到舊快取的項目，優先信任**帶明確日期的新聞標題**（如「XX月XX日盤後：加權指數收跌...」），而不是泛用即時報價頁的摘要。
- 每個數據點最多**再次確認 1 次**（也就是最多 2 次搜尋/該數據點）；兩次搜尋結果仍衝突時，採用敘事最一致、來源最具體（有明確日期標題）的那個，並繼續往下走，不要無限重查。
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
      {"label": "S&P 500", "value": "7,483", "change_pct": "0.22%", "dir": "down"},
      {"label": "NASDAQ", "value": "26,040", "change_pct": "0.66%", "dir": "down"},
      {"label": "費半 SOX", "value": "12,940", "change_pct": "0.31%", "dir": "down"}
    ],
    "asia_market": [
      {"label": "日經 225", "value": "70,474", "change_pct": "0.59%", "dir": "up"},
      {"label": "台股加權", "value": "47,034", "change_pct": "1.97%", "dir": "up"},
      {"label": "台指期夜盤", "value": "47,120", "change_pct": "0.18%", "dir": "up"}
    ],
    "fx": [
      {"label": "USD / TWD", "value": "31.83", "change_pct": "", "dir": "up"},
      {"label": "USD / JPY", "value": "162.0", "change_pct": "", "dir": "up"},
      {"label": "USD / CNY", "value": "6.80", "change_pct": "", "dir": "flat"},
      {"label": "USD / EUR", "value": "0.877", "change_pct": "", "dir": "down"}
    ],
    "commodity_rate": [
      {"label": "WTI 原油", "value": "$68.77", "change_pct": "1.1%", "dir": "down"},
      {"label": "Brent 原油", "value": "$72.20", "change_pct": "1.0%", "dir": "down"},
      {"label": "黃金", "value": "$4,003", "change_pct": "0.88%", "dir": "down"},
      {"label": "白銀", "value": "$58.47", "change_pct": "2.43%", "dir": "down"},
      {"label": "美 10Y 公債", "value": "4.46%", "change_pct": "", "dir": "up"}
    ]
  },
  "highlights": "……（今日重點 1–2 句）",
  "business_angle": "……（今日業務切入點一句）",
  "caring_note": "……（貼心小語一句）",
  "source_note": "資料來源：TheStreet / Yahoo Finance / Reuters・數據為 2026/07/01 最近交易日"
}
```

規則：
- `dir` 只能是 `"up"`（漲，紅）／`"down"`（跌，綠）／`"flat"`（持平，`→`）。台股慣例：漲紅跌綠。
- `change_pct` 沒有明確百分比時給 `""`（只顯示箭頭）。
- 美股區固定三列順序：S&P 500 → NASDAQ → 費半 SOX。
- 亞股區固定三列順序：日經 225 → 台股加權 → 台指期夜盤。
- `source_note` 的日期填「最近交易日」。

---

## 步驟 4：寫出 `line_text.txt`（LINE 文字訊息）

存到 `reports/<YYYY-MM-DD>/line_text.txt`，格式（方便直接貼上 LINE，整則 300 字內）：

```
【早安報報｜每日總經速報】2026/07/02
夥伴早安 ☀

📈 美股
S&P 500：7,483 🔻0.22%
NASDAQ：26,040 🔻0.66%
費半 SOX：12,940 🔻0.31%

🌏 亞股
日經 225：70,474 🔺0.59%
台股加權：47,034 🔺1.97%
台指期夜盤：47,120 🔺0.18%

💱 匯率
USD/TWD：31.83 🔺
USD/JPY：162.0 🔺
USD/CNY：6.80 ➡️
USD/EUR：0.877 🔻

🛢 原物料 / 利率
WTI 原油：$68.77 🔻1.1%
Brent 原油：$72.20 🔻1.0%
黃金：$4,003 🔻0.88%
白銀：$58.47 🔻2.43%
美 10Y 公債：4.46% 🔺

🔥 今日重點
……

💼 今日業務切入點
……

❤️ 貼心小語
……
```

- 漲用 🔺、跌用 🔻、持平用 ➡️。
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

python3 scripts/make_card_html.py \
  "reports/<YYYY-MM-DD>/report.json" "reports/<YYYY-MM-DD>/card.html"
node scripts/shot_card.js \
  "reports/<YYYY-MM-DD>/card.html" \
  "reports/<YYYY-MM-DD>/card.png" \
  "reports/<YYYY-MM-DD>/card_preview.png"
```

會輸出 `card.html`、`card.png` 與 `card_preview.png`（<1MB，給 LINE previewImageUrl）。
紅漲綠跌、持平 `→`、卡片高度依內容自動撐開。產完後用 Read 檢視 `card.png`，確認中文
有正確渲染再繼續。

---

## 步驟 6：提交圖片當圖床，取得公開 URL

LINE 圖片訊息需要公開 HTTPS URL，用本 repo 的 raw 連結當圖床：

```bash
DATE="<YYYY-MM-DD>"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git add "reports/${DATE}"
git commit -m "Daily macro report ${DATE}"
git push origin "HEAD:${BRANCH}"
```

圖片公開 URL（repo 需為 public）：
```
https://raw.githubusercontent.com/NeilCCH/daily-macro-report/<BRANCH>/reports/<DATE>/card.png
https://raw.githubusercontent.com/NeilCCH/daily-macro-report/<BRANCH>/reports/<DATE>/card_preview.png
```

---

## 步驟 7：推播到 LINE 群組

需要環境變數 `LINE_CHANNEL_ACCESS_TOKEN` 與 `LINE_GROUP_IDS`（逗號分隔），以及網路白名單允許 `api.line.me`。

> 註：本流程走 Claude Code Routine，這兩個環境變數要設在 **Claude Code 環境（Environment）設定**裡，不是 GitHub Secrets（GitHub Secrets 只給 GitHub Actions 用）。若 sandbox 內讀不到（`echo $LINE_GROUP_IDS` 為空），就無法自動推播，此時保留已產出的 `card.png` 與 `line_text.txt` 供人工張貼，並回報缺少環境變數。

**只推送圖片卡片，不推送文字訊息**（不要帶 `--text-file`）：

```bash
DATE="<YYYY-MM-DD>"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
BASE="https://raw.githubusercontent.com/NeilCCH/daily-macro-report/${BRANCH}/reports/${DATE}"
python scripts/push_line.py \
  --image-url "${BASE}/card.png" \
  --preview-url "${BASE}/card_preview.png"
```

`push_line.py` 會對 `LINE_GROUP_IDS` 裡、且在 `data/line_groups.json` 未被標記 `enabled: false` 的每個群組送出「圖片訊息」，並逐一回報成功／跳過／失敗。`line_text.txt` 仍會產生，作為卡片內容來源與 repo 記錄，但**不推送到 LINE**。

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
- 每個 LINE 群組都收到**圖片卡片**（不推送文字），`push_line.py` 全部回報 OK。
- PR 已開立並合併回預設分支（步驟 8）；若合併失敗，已在回報中明確說明。
- 全程遵守合規防呆規則；數字全部來自當次搜尋。
