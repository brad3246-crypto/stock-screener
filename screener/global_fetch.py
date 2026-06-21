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

WORKERS = 2   # 야후 레이트리밋(429) 회피 위해 보수적으로


def run(markets=("US", "JP"), limit: int | None = None, workers: int = WORKERS,
        merge: bool = True) -> pd.DataFrame:
    uni = g.build_universe(markets)
    if limit:
        uni = uni.groupby("market", group_keys=False).head(limit)
    fx = g.fx_to_krw()
    print(f"유니버스 {len(uni)}종목 {tuple(markets)} / FX {fx} / 워커 {workers} / 수집 시작")

    tickers = uni["ticker"].tolist()
    records = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(g.fetch_one, t): t for t in tickers}
        for fut in tqdm(as_completed(futs), total=len(futs), desc="yfinance"):
            records.append(fut.result())

    df = g.to_frame(records, uni, fx)

    # 부분 시장만 받았을 때 기존 parquet의 다른 시장 데이터를 보존하고 병합
    if merge and config.GLOBAL_PARQUET.exists():
        old = pd.read_parquet(config.GLOBAL_PARQUET)
        kept = old[~old["market"].isin(markets)]
        df = pd.concat([kept, df], ignore_index=True)
        print(f"병합: 기존 {len(old)}행 중 {len(kept)}행 유지(다른 시장) + 갱신분 결합")

    config.DATA_DIR.mkdir(exist_ok=True)
    df.to_parquet(config.GLOBAL_PARQUET, index=False)

    fresh = df[df["market"].isin(markets)]
    ok = int(fresh["op_y2"].notna().sum())
    print(f"완료: 갱신 {len(fresh)}종목 (영익확보 {ok}) / 전체 {len(df)}종목 / "
          f"{time.time()-t0:.0f}s → {config.GLOBAL_PARQUET.name}")
    return df


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="미국·일본 데이터 수집")
    p.add_argument("--limit", type=int, default=None, help="시장별 앞 N종목(테스트)")
    p.add_argument("--workers", type=int, default=WORKERS)
    p.add_argument("--markets", default="US,JP")
    p.add_argument("--no-merge", action="store_true",
                   help="기존 parquet과 병합하지 않고 통째로 덮어쓰기")
    a = p.parse_args()
    run(markets=tuple(a.markets.split(",")), limit=a.limit, workers=a.workers,
        merge=not a.no_merge)
