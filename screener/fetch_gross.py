"""기존 fundamentals.parquet 에 매출총이익(gross_profit) 컬럼만 보강.

전체 재수집 없이 종목당 finstate_all 1콜로 FY 매출총이익을 받아 머지한다.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm

from . import config
from .dart_client import fetch_gross

GCOL = f"gross_profit_{config.ANNUAL_YEAR}"


def run(workers: int = 8) -> pd.DataFrame:
    df = pd.read_parquet(config.FUNDAMENTALS_PARQUET)
    codes = df["code"].astype(str).tolist()
    print(f"매출총이익 보강: {len(codes)}종목")
    gross: dict = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_gross, c): c for c in codes}
        for fut in tqdm(as_completed(futs), total=len(futs), desc="gross"):
            gross[futs[fut]] = fut.result()
    df[GCOL] = df["code"].astype(str).map(gross)
    df.to_parquet(config.FUNDAMENTALS_PARQUET, index=False)
    n = int(df[GCOL].notna().sum())
    print(f"완료: {n}/{len(df)} 매출총이익 확보 / {time.time()-t0:.0f}s")
    return df


if __name__ == "__main__":
    run()
