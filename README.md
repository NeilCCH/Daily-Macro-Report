# daily-macro-report

每個台灣工作日早上（台北 08:00）自動產出「每日晨報」（全球總經/市場速報）卡片圖片，
並推播到 LINE 官方帳號 **NeilCCH** 旗下已啟用的業務群組（名單見 `data/line_groups.json`）。**只發圖片，不發文字。**

## 架構（現行）

整套跑在 **Claude Code Routine**（Anthropic 雲端），由 **Claude 本人**查證與撰寫，
**不使用 Gemini、不使用任何外部 LLM API key**。電腦關機、人不在都照跑。

```
Claude Code Routine（工作日 08:00 台北）
  1. scripts/check_workday.py   判斷是否台灣工作日；非工作日→整個跳過
  2. Claude 用 WebSearch 抓最近收盤行情 + 當日財經新聞
  3. Claude 依合規規則寫三段文案 → report.json + line_text.txt
  4. scripts/make_card_html.py  → card.html
     scripts/shot_card.js       → card.png + card_preview.png（內建 Chromium 截圖，免 Pillow）
  5. git commit + push（raw.githubusercontent.com 當圖床）
  6. scripts/push_line.py       對啟用中的群組推播「圖片卡片」（不帶 --text-file）
```

- 每日「執行流程」定義：`.claude/skills/daily-macro-report/SKILL.md`
- **從零重建/搬移的完整手冊**：[`docs/RUNBOOK.md`](docs/RUNBOOK.md) ← 未來要重建看這份

## 快速重點

- **repo 必須 public**：LINE 伺服器要抓 `raw.githubusercontent.com` 上的卡片圖。
- **LINE token / 群組 ID 放在 Claude Code 環境變數**（`LINE_CHANNEL_ACCESS_TOKEN`、
  `LINE_GROUP_IDS`），**不是** GitHub Secrets（Routine 跑在 Claude 雲端，讀不到 GitHub Secrets）。
- **要暫停/恢復某個群組的推播**：改 `data/line_groups.json` 裡該群組的 `enabled`，
  commit 併回預設分支即可，不需要動環境變數（見 `docs/RUNBOOK.md` 3.3.1 節）。
- **產圖不用 Pillow**：Claude sandbox 出網被擋裝不了；改用 base image 內建的
  Chromium + 文泉驛正黑字型。產圖前設 `NODE_PATH=/opt/node22/lib/node_modules`。

## 產圖 / 推播手動測試

```bash
export PATH=/opt/node22/bin:$PATH
export NODE_PATH=/opt/node22/lib/node_modules

python3 scripts/make_card_html.py reports/<日期>/report.json reports/<日期>/card.html
node scripts/shot_card.js reports/<日期>/card.html reports/<日期>/card.png reports/<日期>/card_preview.png

# 先用單一群組驗證圖片正常顯示，再放行全部群組
SHA="<含該日報告的 commit SHA>"
BASE="https://raw.githubusercontent.com/NeilCCH/daily-macro-report/${SHA}/reports/<日期>"
FIRST=$(echo "$LINE_GROUP_IDS" | cut -d',' -f1)
LINE_GROUP_IDS="$FIRST" python scripts/push_line.py \
  --image-url "${BASE}/card.png" --preview-url "${BASE}/card_preview.png"
```

## 行事曆資料

`data/taiwan_calendar_<year>.json` 為工作日判斷來源（含假日、週末、補班日）。
**每年需新增次年度 JSON**，否則跨年後 `check_workday.py` 找不到當年度資料。

## 舊架構（已停用）

早期版本跑在 GitHub Actions + Gemini/Claude API + Pillow，並使用 GitHub Secrets。
相關檔案 `scripts/generate_report.py`、`scripts/make_card.py` 已停用（保留供參考），
`.github/workflows/daily-report.yml` 已移除。詳見 `docs/RUNBOOK.md` 第 11 節決策紀錄。
