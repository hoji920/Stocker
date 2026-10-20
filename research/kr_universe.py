"""판단 기록 코스피 종목군 고르기 (2026-10-20, 결과를 보기 전).

규칙: 코스피 보통주(토스 종목 목록의 securityType=STOCK, isCommonShare=true, 이름에 '스팩' 없음) 중
2025-10-20 이전 상장(일봉 1년 이상)이고 거래정지가 아닌 종목을, 시가총액 = 토스 상장주식수 × KRX 종가(야후 .KS, 2026-10-19)로 줄 세워 상위 100개.
야후 종가가 토스 현재가와 30% 넘게 다르면(야후 시세 오류) 뺀다.
코스피 100 공식 구성은 한국거래소 사이트에서 자동으로 받기 어려워서 같은 취지(대형주 100)의 객관적 규칙으로 대신한다.
한 번 고른 목록은 backend/judgments.py 의 KR_UNIVERSE 로 고정하고, 시가총액 순위가 바뀌어도 따라가지 않는다.

    python -m research.kr_universe        # 목록과 시가총액을 research/results/kr_universe.md 에 적는다
"""
from __future__ import annotations

from pathlib import Path

import yfinance as yf

from backend import main

AS_OF, LISTED_BEFORE, TOP = "2026-10-19", "2025-10-20", 100


def build() -> list[dict]:
    t = main.toss()
    stocks = [s for s in t.listed_stocks("KOSPI")
              if s["securityType"] == "STOCK" and s["isCommonShare"] and "스팩" not in s["name"]]
    infos = []
    for k in range(0, len(stocks), 50):                     # 한 번에 50종목씩 (하나씩 부르면 토스 429)
        infos += t._get("/api/v1/stocks", {"symbols": ",".join(s["symbol"] for s in stocks[k:k + 50])})
    infos = [i for i in infos if i.get("status") == "ACTIVE" and (i.get("listDate") or "9999") < LISTED_BEFORE and i.get("sharesOutstanding")
             and not (i.get("koreanMarketDetail") or {}).get("krxTradingSuspended")]
    now = {}
    for k in range(0, len(infos), 200):
        now.update(t.prices([i["symbol"] for i in infos[k:k + 200]]))
    closes = yf.download([f"{i['symbol']}.KS" for i in infos], start="2026-10-12", end="2026-10-20",
                         auto_adjust=False, progress=False, threads=True)["Close"]
    rows = []
    for i in infos:
        col = f"{i['symbol']}.KS"
        if col not in closes or AS_OF not in closes.index.strftime("%Y-%m-%d"):
            continue
        price = closes[col].loc[AS_OF]
        if price != price:          # NaN: 그날 거래 없음
            continue
        if not now.get(i["symbol"]) or abs(float(price) / now[i["symbol"]] - 1) > 0.3:     # 야후 시세 오류
            print("뺌:", i["symbol"], i["name"], float(price), now.get(i["symbol"]))
            continue
        rows.append({"symbol": i["symbol"], "name": i["name"], "close": float(price), "shares": int(i["sharesOutstanding"]),
                     "cap_krw": float(price) * int(i["sharesOutstanding"])})
    return sorted(rows, key=lambda r: -r["cap_krw"])[:TOP]


if __name__ == "__main__":
    rows = build()
    out = Path(__file__).parent / "results" / "kr_universe.md"
    lines = [f"# 판단 기록 코스피 종목군 ({AS_OF} 종가 기준 시가총액 상위 {TOP})", "",
             "| 순위 | 종목 | 코드 | 종가 | 시가총액(조 원) |", "|---|---|---|---|---|"]
    lines += [f"| {k} | {r['name']} | {r['symbol']} | {r['close']:,.0f} | {r['cap_krw'] / 1e12:,.1f} |" for k, r in enumerate(rows, 1)]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(" ".join(r["symbol"] for r in rows))
    print(len(rows), "종목 →", out)
