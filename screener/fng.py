"""지수 Fear & Greed Index (공포·탐욕 지수) 합성.

CNN F&G를 지수 데이터로 재현한 단순 합성 버전. 아래 5개 요소를 각각
0~100(50=중립)로 환산해 평균한다. 지수 OHLCV + 국고채 ETF만으로 계산되어
이 앱 환경(FDR)에서 바로 동작한다. 요소 가중치·창 길이는 상단 상수로 조정.

  1) 모멘텀      : 종가 vs 125일 이동평균 이격
  2) RSI(14)     : 강도
  3) 52주 위치   : 최근 252일 고저 범위 내 위치
  4) 변동성(역)  : 실현변동성 백분위의 역수(저변동=탐욕)
  5) 안전자산    : 지수 20일 수익률 − 국고채 20일 수익률

출력: close, fng, ema20, osc(오실레이터) + 요소별 점수.
  ema20 = F&G의 20일 지수이동평균
  osc   = EMA12(F&G/100) − EMA26(F&G/100)  (MACD식, 대략 ±0.02)
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

INDEX_CODES = {"코스피": "KS11", "코스닥": "KQ11"}
BOND_ETF = "114260"          # KODEX 국고채3년 (안전자산 프록시)
WARMUP_DAYS = 420            # 252일 창을 채우기 위한 사전 조회분
MOM_MA = 125
RSI_N = 14
RANGE_N = 252
VOL_N = 20
SAFE_N = 20


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _squash(z: pd.Series, k: float) -> pd.Series:
    """z를 0~100(50 중립)로. tanh 압축."""
    return 50 + 50 * np.tanh(z * k)


def fear_greed(code: str, display_start: str, bond: pd.Series | None = None) -> pd.DataFrame:
    """display_start 이후 구간의 F&G. 실패 시 빈 DataFrame."""
    import FinanceDataReader as fdr

    warm = (pd.Timestamp(display_start) - pd.Timedelta(days=WARMUP_DAYS)).date().isoformat()
    try:
        h = fdr.DataReader(code, warm)
    except Exception:
        return pd.DataFrame()
    if h is None or h.empty or "Close" not in h.columns:
        return pd.DataFrame()
    c = h["Close"].astype(float)

    comp = pd.DataFrame(index=c.index)
    # 1) 모멘텀
    ma = c.rolling(MOM_MA).mean()
    comp["모멘텀"] = _squash((c - ma) / ma, k=6)
    # 2) RSI
    comp["RSI"] = _rsi(c, RSI_N)
    # 3) 52주 위치
    lo = c.rolling(RANGE_N).min()
    hi = c.rolling(RANGE_N).max()
    comp["52주위치"] = ((c - lo) / (hi - lo) * 100).clip(0, 100)
    # 4) 변동성(역): 저변동 = 탐욕
    vol = c.pct_change().rolling(VOL_N).std()
    volrank = vol.rolling(RANGE_N).apply(
        lambda x: (x[-1] > x).mean() * 100, raw=True)
    comp["변동성"] = 100 - volrank
    # 5) 안전자산 선호
    if bond is not None and not bond.empty:
        b = bond.reindex(c.index).ffill()
        rk = c / c.shift(SAFE_N) - 1
        rb = b / b.shift(SAFE_N) - 1
        comp["안전자산"] = _squash(rk - rb, k=8)

    fng = comp.mean(axis=1)
    out = pd.DataFrame({"close": c, "fng": fng})
    out = out.join(comp)
    out["ema20"] = fng.ewm(span=20, adjust=False).mean()
    n = fng / 100
    out["osc"] = n.ewm(span=12, adjust=False).mean() - n.ewm(span=26, adjust=False).mean()

    out = out[out.index >= pd.Timestamp(display_start)].dropna(subset=["fng"])
    out.index.name = "date"
    return out


def load_bond(start: str) -> pd.Series:
    import FinanceDataReader as fdr
    try:
        b = fdr.DataReader(BOND_ETF, start)
        return b["Close"].astype(float)
    except Exception:
        return pd.Series(dtype=float)


def default_start(years: int = 1) -> str:
    return (dt.date.today() - dt.timedelta(days=365 * years)).isoformat()


def label(v: float) -> str:
    """F&G 점수 → 심리 라벨."""
    if v is None or np.isnan(v):
        return "-"
    if v < 25:
        return "극단적 공포"
    if v < 45:
        return "공포"
    if v < 55:
        return "중립"
    if v < 75:
        return "탐욕"
    return "극단적 탐욕"
