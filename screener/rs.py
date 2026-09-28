"""RS(상대강도) 분석 → data/rs_kr.parquet, data/rs_global.parquet.

종목별 ~15개월 일봉으로 IBD식 가중 모멘텀 점수를 만들고, 시장 내 백분위(1~99)로
RS Rating을 매긴다. 동시에 3개월 전 시점의 RS도 계산해 rs_delta(최근 3개월 개선폭)를
제공한다. '바닥반등' = RS가 하위권(수급 소외)이면서 최근 3개월 RS가 오르는 종목.

실행:  python -m screener.rs                      # 한국 + 미국·일본
       python -m screener.rs --kr-only --limit 50  # 빠른 테스트
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import FinanceDataReader as fdr
import yfinance as yf
from tqdm import tqdm

from . import config
from .universe import load_universe

# IBD식 가중 모멘텀: 최근 분기(3개월)에 2배 가중
_LB = [63, 126, 189, 252]      # 3·6·9·12개월(거래일)
_W = [2.0, 1.0, 1.0, 1.0]
_NEED = 252                    # 점수 1개에 필요한 최소 거래일

# 볼린저 밴드: 20일 이동평균 ± 2σ, 밴드폭 순위는 최근 6개월(126거래일) 내 비교
_BB_N, _BB_K = 20, 2.0
_BB_LB = 126


def _score(closes: np.ndarray, off: int = 0):
    """off 거래일 전 시점 기준 IBD 가중 모멘텀 점수. 데이터 부족 시 None."""
    if len(closes) < _NEED + off + 1:
        return None
    base = closes[-1 - off]
    if not (base > 0):
        return None
    s = 0.0
    for w, lb in zip(_W, _LB):
        past = closes[-1 - off - lb]
        if not (past > 0):
            return None
        s += w * (base / past)
    return s


def _returns(closes: np.ndarray):
    """현재 기준 3·6·12개월 수익률(%). 데이터 없으면 NaN."""
    def r(lb):
        if len(closes) < lb + 1 or not (closes[-1 - lb] > 0):
            return np.nan
        return (closes[-1] / closes[-1 - lb] - 1) * 100
    return r(63), r(126), r(252)


def _mdd(closes: np.ndarray, window: int = 252):
    """최근 window 거래일(기본 12개월) 최대낙폭(MDD, %). 음수로 반환."""
    if len(closes) < 2:
        return None
    w = closes[-window:] if len(closes) > window else closes
    peak = np.maximum.accumulate(w)
    dd = w / peak - 1.0          # 고점 대비 하락률(≤0)
    return float(dd.min() * 100)


def _bollinger(closes: np.ndarray, n: int = _BB_N, k: float = _BB_K, lb: int = _BB_LB):
    """볼린저 밴드 → (%B, 밴드폭%, 밴드폭 순위%). 데이터 부족 시 None.

    %B      = (종가 - 하단) / (상단 - 하단). 0 이하 하단 이탈, 1 이상 상단 돌파.
    밴드폭  = (상단 - 하단) / 중심선 × 100.
    폭 순위 = 최근 lb거래일 밴드폭 중 현재 이하 비율(0~100). 낮을수록 수축(스퀴즈).
    """
    if len(closes) < n:
        return None, None, None
    s = pd.Series(closes)
    mid = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    span = float(upper.iloc[-1] - lower.iloc[-1])
    pctb = float((closes[-1] - lower.iloc[-1]) / span) if span > 0 else None
    bw = ((upper - lower) / mid * 100).where(mid > 0)
    width = float(bw.iloc[-1]) if bw.iloc[-1] == bw.iloc[-1] else None
    hist = bw.dropna().iloc[-lb:]
    rank = None
    if width is not None and len(hist) >= lb:
        rank = float((hist <= width).mean() * 100)
    return pctb, width, rank


def _rec(key: str, market: str, closes) -> dict:
    arr = np.asarray([c for c in (closes if closes is not None else []) if c == c],
                     dtype=float)
    r3, r6, r12 = _returns(arr)
    pctb, width, width_rank = _bollinger(arr)
    return {"key": key, "market": market,
            "score": _score(arr, 0), "score_3m": _score(arr, 63),
            "mdd": _mdd(arr),
            "bb_pctb": pctb, "bb_width": width, "bb_width_rank": width_rank,
            "ret_3m": r3, "ret_6m": r6, "ret_12m": r12}


def _pct_rank(s: pd.Series) -> pd.Series:
    """점수 → 1~99 백분위(높을수록 강함)."""
    return (s.rank(pct=True) * 98 + 1).round().clip(1, 99)


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    """시장 내 백분위로 rs, rs_3m 부여 + rs_delta(개선폭)."""
    df = df.copy()
    df["rs"] = np.nan
    df["rs_3m"] = np.nan
    for _mk, idx in df.groupby("market").groups.items():
        sub = df.loc[idx]
        df.loc[idx, "rs"] = _pct_rank(sub["score"])
        df.loc[idx, "rs_3m"] = _pct_rank(sub["score_3m"])
    df["rs_delta"] = df["rs"] - df["rs_3m"]
    return df


def _start_date() -> str:
    # 3개월 전 시점의 12개월 모멘텀까지 필요 → ~15개월(여유 600일)
    return (dt.date.today() - dt.timedelta(days=600)).isoformat()


# ── 한국 (FinanceDataReader) ──────────────────────────────────────────────
def _kr_closes(code: str, start: str, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            h = fdr.DataReader(code, start)
            if h is None or h.empty or "Close" not in h.columns:
                return None
            return h["Close"].dropna().to_numpy(dtype=float)
        except Exception:
            if attempt < retries:
                time.sleep(1.0 + attempt)
                continue
            return None


def compute_kr(limit: int | None = None, workers: int = 8) -> pd.DataFrame:
    uni = load_universe(refresh=False)
    if limit:
        uni = uni.groupby("market", group_keys=False).head(limit)
    start = _start_date()
    markets = dict(zip(uni["code"], uni["market"]))
    codes = uni["code"].tolist()
    print(f"[KR] {len(codes)}종목 주가 수집 (start={start}, workers={workers})")

    def one(code):
        return _rec(code, markets[code], _kr_closes(code, start))

    recs = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in tqdm(ex.map(one, codes), total=len(codes), desc="KR RS"):
            recs.append(r)
    df = _finalize(pd.DataFrame(recs)).rename(columns={"key": "code"})
    return df[["code", "market", "rs", "rs_3m", "rs_delta", "mdd",
               "ret_3m", "ret_6m", "ret_12m", "bb_pctb", "bb_width", "bb_width_rank"]]


# ── 미국·일본 (yfinance 배치 다운로드) ────────────────────────────────────
def _yf_closes_chunk(tickers: list, start: str) -> dict:
    out = {}
    try:
        data = yf.download(tickers, start=start, interval="1d", group_by="ticker",
                           auto_adjust=True, threads=True, progress=False)
    except Exception:
        return out
    for t in tickers:
        try:
            s = data["Close"] if len(tickers) == 1 else data[t]["Close"]
            arr = s.dropna().to_numpy(dtype=float)
            if len(arr):
                out[t] = arr
        except Exception:
            continue
    return out


def compute_global(limit: int | None = None, chunk: int = 25, pause: float = 1.5) -> pd.DataFrame:
    if config.GLOBAL_PARQUET.exists():
        uni = pd.read_parquet(config.GLOBAL_PARQUET)[["ticker", "market"]].drop_duplicates()
    else:
        from .global_data import build_universe
        uni = build_universe(("US", "JP"))[["ticker", "market"]]
    if limit:
        uni = uni.groupby("market", group_keys=False).head(limit)
    start = _start_date()
    markets = dict(zip(uni["ticker"], uni["market"]))
    tickers = uni["ticker"].tolist()
    print(f"[GLOBAL] {len(tickers)}종목 주가 수집 (start={start}, chunk={chunk})")

    closes: dict = {}
    for i in tqdm(range(0, len(tickers), chunk), desc="GLOBAL RS"):
        closes.update(_yf_closes_chunk(tickers[i:i + chunk], start))
        time.sleep(pause)
    recs = [_rec(t, markets[t], closes.get(t)) for t in tickers]
    df = _finalize(pd.DataFrame(recs)).rename(columns={"key": "ticker"})
    return df[["ticker", "market", "rs", "rs_3m", "rs_delta", "mdd",
               "ret_3m", "ret_6m", "ret_12m", "bb_pctb", "bb_width", "bb_width_rank"]]


def _save(df: pd.DataFrame, path, tag: str, min_valid: float) -> bool:
    """RS 유효 비율이 min_valid 이상일 때만 저장(수집 실패로 캐시를 덮어쓰는 사고 방지)."""
    n_ok = int(df["rs"].notna().sum())
    ratio = n_ok / len(df) if len(df) else 0.0
    if ratio < min_valid:
        print(f"[{tag}] 저장 안 함: RS유효 {n_ok}/{len(df)} ({ratio:.0%}) < 기준 {min_valid:.0%}")
        return False
    df.to_parquet(path, index=False)
    print(f"[{tag}] 저장 {len(df)}종목 · RS유효 {n_ok} ({ratio:.0%}) → {path.name}")
    return True


def run(do_kr: bool = True, do_global: bool = True,
        limit: int | None = None, workers: int = 8, min_valid: float = 0.0) -> None:
    config.DATA_DIR.mkdir(exist_ok=True)
    t0 = time.time()
    if do_kr:
        _save(compute_kr(limit=limit, workers=workers), config.RS_KR_PARQUET, "KR", min_valid)
    if do_global:
        _save(compute_global(limit=limit), config.RS_GLOBAL_PARQUET, "GLOBAL", min_valid)
    print(f"완료 / {time.time() - t0:.0f}s")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RS(상대강도) 수집")
    p.add_argument("--kr-only", action="store_true")
    p.add_argument("--global-only", action="store_true")
    p.add_argument("--limit", type=int, default=None, help="시장별 앞 N종목(테스트)")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--min-valid", type=float, default=0.0,
                   help="RS 유효 비율이 이 값(0~1) 미만이면 캐시를 덮어쓰지 않음")
    a = p.parse_args()
    run(do_kr=not a.global_only, do_global=not a.kr_only,
        limit=a.limit, workers=a.workers, min_valid=a.min_valid)
