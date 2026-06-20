"""미국·일본 데이터 수집 파이프라인 → data/global_fundamentals.parquet.

yfinance 종목별 호출(스레드) + 메트릭 계산 + 시총 원화 환산.
야후 레이트리밋을 피하려 워커 수를 보수적으로 둔다.
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm

from . import config, global_data as g

WORKERS = 6


def run(markets=("US", "JP"), limit: int | None = None, workers: int = WORKERS) -> pd.DataFrame:
    uni = g.build_universe(markets)
    if limit:
        uni = uni.groupby("market", group_keys=False).head(limit)
    fx = g.fx_to_krw()
    print(f"유니버스 {len(uni)}종목 (US/JP) / FX {fx} / 수집 시작")

    tickers = uni["ticker"].tolist()
    records = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(g.fetch_one, t): t for t in tickers}
        for fut in tqdm(as_completed(futs), total=len(futs), desc="yfinance"):
            records.append(fut.result())

    df = g.to_frame(records, uni, fx)
    config.DATA_DIR.mkdir(exist_ok=True)
    df.to_parquet(config.GLOBAL_PARQUET, index=False)
    ok = int(df["op_y2"].notna().sum())
    print(f"완료: {len(df)}종목 (영익확보 {ok}) / {time.time()-t0:.0f}s → {config.GLOBAL_PARQUET.name}")
    return df


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="미국·일본 데이터 수집")
    p.add_argument("--limit", type=int, default=None, help="시장별 앞 N종목(테스트)")
    p.add_argument("--workers", type=int, default=WORKERS)
    p.add_argument("--markets", default="US,JP")
    a = p.parse_args()
    run(markets=tuple(a.markets.split(",")), limit=a.limit, workers=a.workers)
