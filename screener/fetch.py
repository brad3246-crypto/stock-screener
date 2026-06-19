"""DART 재무 수집 파이프라인 → data/fundamentals.parquet.

회사당 2콜(연간 11011 + 1분기 11013). 동시 스레드 + 재시도 + 부분 저장.
이미 캐시된 종목은 건너뛰므로 중단 후 재실행하면 이어받는다.
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm

from . import config
from .dart_client import fetch_company
from .universe import load_universe


def _load_cache() -> pd.DataFrame:
    if config.FUNDAMENTALS_PARQUET.exists():
        return pd.read_parquet(config.FUNDAMENTALS_PARQUET)
    return pd.DataFrame(columns=["code"])


def run(limit: int | None = None, force: bool = False, workers: int | None = None) -> pd.DataFrame:
    universe = load_universe(refresh=True)
    cache = _load_cache()
    done = set() if force else set(cache["code"].astype(str))

    todo = [c for c in universe["code"].tolist() if c not in done]
    if limit:
        todo = todo[:limit]
    print(f"유니버스 {len(universe)}종목 / 캐시 {len(done)} / 수집대상 {len(todo)}")
    if not todo:
        print("수집할 종목 없음 (캐시 최신). --force 로 강제 갱신 가능.")
        return cache

    workers = workers or config.FETCH_WORKERS
    results: list[dict] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_company, code): code for code in todo}
        for i, fut in enumerate(tqdm(as_completed(futs), total=len(futs), desc="DART"), 1):
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001
                results.append({"code": futs[fut], "error": str(e)})
            # 250종목마다 중간 저장
            if i % 250 == 0:
                _save(cache, results)

    out = _save(cache, results)
    ok = out["has_annual"].sum() if "has_annual" in out else 0
    print(f"완료: {len(out)}종목 캐시 (연간확보 {ok}) / {time.time()-t0:.0f}s")
    return out


def _save(cache: pd.DataFrame, new: list[dict]) -> pd.DataFrame:
    if not new:
        return cache
    merged = pd.concat([cache, pd.DataFrame(new)], ignore_index=True)
    merged = merged.drop_duplicates(subset="code", keep="last").reset_index(drop=True)
    config.DATA_DIR.mkdir(exist_ok=True)
    merged.to_parquet(config.FUNDAMENTALS_PARQUET, index=False)
    return merged


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="DART 재무 수집")
    p.add_argument("--limit", type=int, default=None, help="앞 N종목만(테스트)")
    p.add_argument("--force", action="store_true", help="캐시 무시 전체 재수집")
    p.add_argument("--workers", type=int, default=None)
    a = p.parse_args()
    run(limit=a.limit, force=a.force, workers=a.workers)
