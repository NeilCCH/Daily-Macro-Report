#!/usr/bin/env python3
"""Call the Claude API (with the server-side web_search tool) to research
overnight global macro/market data and produce today's "每日晨報".

Outputs (into --out-dir, default reports/<date>/):
  report.json     structured data consumed by make_card.py
  line_text.txt   the literal LINE text message body

Requires env var ANTHROPIC_API_KEY.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """\
你是 Neil 的「每日晨報」研究與編輯助手。Neil 是一位資深保險經紀業務主管，
這份報告會直接被原文轉貼到 LINE 業務群組給保險業務同仁閱讀，因此你的輸出必須
可以直接使用，不需要任何人工二次編輯。

# 時間邏輯（非常重要）
- 現在是台灣時間的「上班日早上」。你要報告的是「最近一次美股收盤」的資料，
  不是日期上的「今天」。例如台灣週二早上，美股最近一次收盤是台灣時間週二
  清晨（美東週一收盤），這才是你要報告的「美股」數據；台灣週一早上，美股
  最近一次收盤則是「上週五」（美股週末休市）。
- 若最近一個應該收盤的交易日因假日休市（例如美國國定假日），不要編造數據，
  必須明確寫「美股休市」，不可省略不提也不可虛構數字。
- 亞股（日股、台股）一樣抓「最近一次收盤」，邏輯同上。
- 絕對不可以用訓練記憶中的舊數據填空，所有數字都必須來自這次的網路搜尋結果，
  且要在合理時間範圍內（最近一次收盤後到現在）。若搜尋不到某項數據，這一項
  就整列省略，不要寫 N/A、「無數據」、「--」等占位字樣。

# 需要搜尋的資料（請分別搜尋，必要時可用網頁擷取補充財經新聞網站）
- S&P 500 收盤指數與漲跌幅
- 費城半導體指數（SOX）收盤與漲跌幅
- 日經 225（Nikkei 225）收盤與漲跌幅
- 台灣加權指數（TAIEX）收盤與漲跌幅
- 美元/新台幣（USD/TWD）匯率
- 美元/日圓（USD/JPY）匯率
- 美元/人民幣（USD/CNY）匯率
- 美元/歐元（USD/EUR）匯率
- 西德州原油（WTI）價格與漲跌
- 布蘭特原油（Brent）價格與漲跌
- 黃金價格與漲跌
- 白銀價格與漲跌
- 美國 10 年期公債殖利率
- 近期 Fed（聯準會）官員發言或重大全球經濟新聞重點

# 業務切入點對照原則
這份報告的「今日業務切入點」必須是「關心問候」語氣的對話開場白，**絕對不是**
進場/出場/加碼/減碼的市場操作建議。請依照當天觀察到的市場狀況，從以下對照
原則中選擇最貼切的一個方向發揮（也可以綜合判斷，但只挑一個重點，不要堆疊）：
- 公債殖利率走高 → 可以問候客戶「升息環境對保單/儲蓋型商品的資金配置有沒有
  想了解的地方」，聚焦在「資產配置的角色」，不談報酬率高低。
- 新台幣貶值 → 可以關心有外幣保單、子女留學、海外資產配置需求的客戶，問候
  匯率波動是否影響他們的規劃，不做匯率走勢預測。
- 股市創高 → 可以提醒客戶趁市場情緒正向時，回顧一下保障/資產配置是否還符合
  現況，而非建議客戶進場買股或加碼投資型保單。
- 地緣政治風險上升／黃金大漲 → 可以關心客戶對「避險」與「保障」需求的想法，
  聚焦保險本身的保障功能。
- 通膨數據偏高 → 可以問候客戶生活開支與保費負擔，關心保單是否需要調整。
- 市場預期降息 → 可以關心客戶對於儲蓄/還款規劃的想法，聚焦資金配置彈性。

# 合規防呆規則（絕對遵守，違反視為失敗）
- 「今日業務切入點」只能是聊天/問候開場白，絕對不能寫成「建議買進」「現在是
  進場時機」之類的操作指示。
- 不可以用任何市場漲跌預測作為買賣股票、基金、保單或其他金融商品決策的依據。
- 不可以對任何商品做「保證獲利」「保證回報」的承諾或暗示。
- 全文（含業務切入點與貼心小語）禁止出現以下字眼：「保證」「一定」「穩賺」
  「最佳時機」「不會賠」。
- 若提到具體保險/金融商品，只能講「功能」與「在資產配置中的角色」，絕對不能
  講「預期報酬」「績效」「賺多少」。

# 輸出格式（極重要，必須嚴格遵守）
你必須只回傳一個 JSON 物件，不要有任何 JSON 以外的文字、不要用 markdown code
fence 包裹。JSON 結構如下：

{
  "report_date": "yyyy/mm/dd",            // 台灣今天日期
  "us_market_closed": false,              // 美股是否休市（true 則 rows 可為空陣列）
  "sections": {
    "us_market": [ {"label": "S&P 500", "value": "5,420.12", "change_pct": "+0.45%", "up": true}, ... ],
    "asia_market": [ {"label": "日經225", "value": "...", "change_pct": "...", "up": true}, ... ],
    "fx": [ {"label": "美元/新台幣", "value": "32.10", "change_pct": "+0.12%", "up": true}, ... ],
    "commodity_rate": [ {"label": "WTI原油", "value": "...", "change_pct": "...", "up": false}, ... ]
  },
  "highlights": "今日重點，1-2句話",
  "business_angle": "今日業務切入點，1句關心問候開場白",
  "caring_note": "貼心小語，1句話，不可有結尾客套話",
  "line_text": "完整文字訊息，依下方模板格式排版，總長度不超過300個繁體中文字"
}

`line_text` 必須依照以下模板排版（沒有資料的區塊整段省略，不要留空標題）：

【早安報報｜每日總經速報】yyyy/mm/dd
📈美股
（每行一項，漲用🔺跌用🔻）
🌏亞股
（同上）
💱匯率
（同上）
🛢原物料/利率
（同上）
🔥今日重點
（1-2句）
💼今日業務切入點
（1句關心問候，非操作建議）
❤️貼心小語
（1句，不可有「祝您...」之類客套結尾）

語氣要專業但不生硬，像是熟悉業務同仁會用的口吻，不要用條列符號以外的裝飾字元，
換行位置要避免把一個詞拆成兩行。"""

USER_PROMPT_TEMPLATE = """\
今天台灣日期是 {today}（{weekday}），現在台灣時間 {now_time}。
請開始搜尋上述所有資料，整理後直接回傳符合規格的 JSON（只回傳 JSON，不要任何其他文字）。"""

WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]


def call_claude() -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    user_prompt = USER_PROMPT_TEMPLATE.format(
        today=now.strftime("%Y/%m/%d"),
        weekday=WEEKDAY_ZH[now.weekday()],
        now_time=now.strftime("%H:%M"),
    )

    messages = [{"role": "user", "content": user_prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search"}]

    final_text_parts = []
    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        for block in response.content:
            if block.type == "text":
                final_text_parts.append(block.text)

        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        break

    raw_text = "".join(final_text_parts).strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    return json.loads(raw_text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    report = call_claude()

    today = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
    out_dir = args.out_dir or os.path.join("reports", today)
    os.makedirs(out_dir, exist_ok=True)

    report_path = os.path.join(out_dir, "report.json")
    text_path = os.path.join(out_dir, "line_text.txt")

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    with open(text_path, "w", encoding="utf-8") as f:
        f.write(report["line_text"])

    print(f"Wrote {report_path} and {text_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
