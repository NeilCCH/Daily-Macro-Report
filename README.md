# daily-macro-report

每個台灣工作日早上自動產出「每日晨報」（全球總經/市場速報），並推播到 LINE 官方帳號
**NeilCCH** 的 3 個業務群組。整條流程全部跑在 GitHub Actions 上（排程／抓資料／產圖／
推播），不依賴任何外部圖床或本機/Claude Cloud session。

## 架構

```
GitHub Actions cron (00:00 UTC = 08:00 Asia/Taipei，每天觸發)
  1. scripts/check_workday.py   讀 data/taiwan_calendar_<year>.json 判斷是否工作日
  2. scripts/generate_report.py 呼叫 Claude API（web_search 工具）→ report.json + line_text.txt
  3. scripts/make_card.py       依 report.json 用 Pillow 渲染 card.png + card_preview.png
  4. commit + push 報告資產回 repo（raw.githubusercontent.com 網址立即生效，當圖床用）
  5. scripts/push_line.py       對 3 個 LINE 群組呼叫 Messaging API pushMessage（圖片 + 文字）
```

非工作日（假日／週末，且未被排成補班日）時，第 1 步之後的所有步驟都會被跳過。

## 為什麼全部跑在 GitHub Actions

Claude Cloud session 的網路代理層會封鎖 `api.line.me` 與一般圖床（已實測確認 403），
但放行 GitHub 網域。因此 LINE 推播與圖片託管都必須由 GitHub Actions（在 GitHub 自己
的機器上執行，不受該代理限制）負責，而不是在互動式 Claude session 裡直接呼叫。

## 需要設定的 GitHub Actions Secrets

到 repo **Settings → Secrets and variables → Actions** 新增：

| Secret | 說明 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API 金鑰 |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API 的 Channel Access Token |
| `LINE_GROUP_IDS` | 逗號分隔的群組 ID，例如 `Cxxxx1,Cxxxx2,Cxxxx3` |

> ⚠️ **務必使用重新發行（Reissue）過的新 token。** 若這個 channel access token 曾經
> 在任何聊天記錄、文件或截圖中以明文出現過，代表它已經外流，必須先到 LINE Developers
> Console 重新發行/作廢舊 token，再把新 token 填進這個 Secret。Token 絕對不要寫進任何
> 會被 commit 的檔案。

## 本機測試

```bash
pip install -r requirements.txt
sudo apt-get install -y fonts-noto-cjk   # PNG 渲染需要中文字型

export ANTHROPIC_API_KEY=...
python scripts/generate_report.py --out-dir reports/test
python scripts/make_card.py reports/test/report.json reports/test/card.png

export LINE_CHANNEL_ACCESS_TOKEN=...
export LINE_GROUP_IDS=Cxxxx1
python scripts/push_line.py \
  --image-url https://raw.githubusercontent.com/<owner>/<repo>/<branch>/reports/test/card.png \
  --preview-url https://raw.githubusercontent.com/<owner>/<repo>/<branch>/reports/test/card_preview.png \
  --text-file reports/test/line_text.txt
```

建議先用單一群組（例如群組1）驗證圖片與文字都正確送達後，才把 `LINE_GROUP_IDS` 換成
正式的 3 個群組。

## 手動觸發整條 workflow

在 GitHub repo 的 **Actions → Daily Macro Report → Run workflow** 可以手動觸發
（`workflow_dispatch`），不需要等到隔天 08:00。

## 行事曆資料

`data/taiwan_calendar_<year>.json` 取自台灣政府公開的工作日行事曆資料集，包含一般
假日、週末，以及國定假日的補班日（`isHoliday: false` 的週六）。每年需要更新/新增對應
年度的 JSON 檔。
