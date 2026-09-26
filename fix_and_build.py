# -*- coding: utf-8 -*-
"""누락 12종목을 유니버스에 주입 → KR RS 재계산 → 소부장 RS 엑셀 재생성(원스톱)."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import FinanceDataReader as fdr

base = r"C:\Users\brad3\stock-screener\data"

# FDR StockListing 캐시에서 빠진 종목 (전부 KOSDAQ)
MISSING = {
    "058470": "리노공업", "240810": "원익IPS", "036930": "주성엔지니어링",
    "095610": "테스", "056190": "에스에프에이", "140860": "파크시스템스",
    "403870": "HPSP", "357780": "솔브레인", "036830": "솔브레인홀딩스",
    "064760": "티씨케이", "222800": "심텍", "131970": "두산테스나",
}

# ── 1) 유니버스 패치 ──
uni = pd.read_parquet(base + r"\universe.parquet")
uni["code"] = uni["code"].astype(str).str.zfill(6)


def last_close(code):
    try:
        h = fdr.DataReader(code, "2026-06-01")
        return float(h["Close"].dropna().iloc[-1])
    except Exception:
        return None


with ThreadPoolExecutor(max_workers=6) as ex:
    closes = dict(zip(MISSING, ex.map(last_close, MISSING)))

add = pd.DataFrame([
    {"code": c, "name": n, "market": "KOSDAQ",
     "close": closes.get(c), "marcap": None, "shares": None}
    for c, n in MISSING.items()
    if c not in set(uni["code"])
])
uni2 = pd.concat([uni, add], ignore_index=True)
uni2.to_parquet(base + r"\universe.parquet", index=False)
print(f"유니버스 패치: {len(uni)} -> {len(uni2)} (+{len(add)})")

# ── 2) RS 재계산 ──
from screener import rs as rsmod
df_rs = rsmod.compute_kr(workers=8)
df_rs.to_parquet(base + r"\rs_kr.parquet", index=False)
print(f"RS 재계산 완료: {len(df_rs)}종목 · RS유효 {int(df_rs['rs'].notna().sum())}")

# ── 3) 소부장 엑셀 재생성 ──
import build_soubujang  # noqa  (import 시 엑셀까지 생성)
