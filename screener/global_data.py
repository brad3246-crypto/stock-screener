"""미국(S&P500) · 일본(닛케이225) 데이터 — yfinance 기반.

한국(DART)과 동일한 4기준을 적용하되, 회계연도 차이로 '올해 1분기 YoY'는
'최근 보고 분기 YoY'(최신 분기 vs 1년 전 동일 분기)로 해석한다.
시총은 환율로 원화(억)로 환산해 한국과 비교 가능하게 한다.
"""
from __future__ import annotations

import io
import random
import time

import pandas as pd
import requests
import yfinance as yf

import FinanceDataReader as fdr

UA = {"User-Agent": "Mozilla/5.0"}

OP_ROWS = ["Operating Income", "Operating Income As Reported",
           "Total Operating Income As Reported"]
NI_ROWS = ["Net Income", "Net Income Common Stockholders",
           "Net Income From Continuing Operation Net Minority Interest"]
EQ_ROWS = ["Stockholders Equity", "Common Stock Equity",
           "Total Equity Gross Minority Interest"]


# ── 유니버스 ─────────────────────────────────────────────────────────────
def sp500() -> pd.DataFrame:
    df = fdr.StockListing("S&P500")
    df = df.rename(columns={"Symbol": "ticker", "Name": "name", "Sector": "sector"})
    df["market"] = "US"
    return df[["ticker", "name", "market", "sector"]]


def nikkei225() -> pd.DataFrame:
    """일본어 위키 日経平均株価 구성종목 표(섹터별)를 합쳐 ~225종목."""
    url = "https://ja.wikipedia.org/wiki/" + "日経平均株価"
    html = requests.get(url, headers=UA, timeout=20).text
    parts = []
    for t in pd.read_html(io.StringIO(html)):
        cols = [str(c) for c in t.columns]
        if "証券コード" in cols and "銘柄" in cols:
            parts.append(t[["証券コード", "銘柄"]])
    df = pd.concat(parts, ignore_index=True).dropna()
    df.columns = ["code", "name"]
    df["ticker"] = df["code"].astype(str).str.strip() + ".T"
    df["market"] = "JP"
    df["sector"] = ""           # yfinance .info 에서 채움
    return df[["ticker", "name", "market", "sector"]].drop_duplicates("ticker")


def build_universe(markets=("US", "JP")) -> pd.DataFrame:
    frames = []
    if "US" in markets:
        frames.append(sp500())
    if "JP" in markets:
        frames.append(nikkei225())
    return pd.concat(frames, ignore_index=True)


# ── 환율 ────────────────────────────────────────────────────────────────
def fx_to_krw() -> dict:
    out = {"US": None, "JP": None}
    try:
        out["US"] = float(yf.Ticker("USDKRW=X").history(period="5d")["Close"].iloc[-1])
        out["JP"] = float(yf.Ticker("JPYKRW=X").history(period="5d")["Close"].iloc[-1])
    except Exception:
        pass
    return out


# ── 종목별 재무 ──────────────────────────────────────────────────────────
def _row(df, names):
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None


def _vals(s, k=3):
    if s is None:
        return [None] * k
    v = [float(x) if pd.notna(x) else None for x in list(s.values)[:k]]
    return v + [None] * (k - len(v))


def _is_rate_limited(err: Exception) -> bool:
    s = str(err).lower()
    return "too many requests" in s or "rate limit" in s or "429" in s


def fetch_one(ticker: str, retries: int = 4, base_delay: float = 2.0) -> dict:
    """한 종목의 yfinance 재무를 dict로.
    429(레이트리밋)는 지수 백오프로 재시도(2·4·8·16초). 실패해도 ticker는 포함."""
    rec: dict = {"ticker": ticker}
    time.sleep(random.uniform(0.1, 0.4))   # 동시 호출 폭주 완화용 소량 지터
    for attempt in range(retries + 1):
        try:
            tk = yf.Ticker(ticker)
            info = tk.info or {}
            rec["marcap_native"] = info.get("marketCap")
            rec["per"] = info.get("trailingPE")
            rec["pbr"] = info.get("priceToBook")
            rec["yf_sector"] = info.get("sector") or ""
            rec["yf_name"] = info.get("longName") or info.get("shortName") or ""
            rec["currency"] = info.get("financialCurrency") or info.get("currency") or ""
            isa, bs, q = tk.income_stmt, tk.balance_sheet, tk.quarterly_income_stmt
            op = _vals(_row(isa, OP_ROWS))   # [Y, Y-1, Y-2]
            ni = _vals(_row(isa, NI_ROWS))
            eq = _vals(_row(bs, EQ_ROWS))
            rec["op_y0"], rec["op_y1"], rec["op_y2"] = op[2], op[1], op[0]  # [Y-2, Y-1, Y]
            rec["ni_y0"], rec["ni_y1"], rec["ni_y2"] = ni[2], ni[1], ni[0]
            rec["eq_y0"], rec["eq_y1"], rec["eq_y2"] = eq[2], eq[1], eq[0]
            rec["rev_y2"] = _vals(_row(isa, ["Total Revenue", "Operating Revenue"]))[0]
            rec["gp_y2"] = _vals(_row(isa, ["Gross Profit"]))[0]
            qop = _row(q, ["Operating Income"])
            qv = [float(x) if pd.notna(x) else None for x in list(qop.values)] if qop is not None else []
            rec["op_q_cur"] = qv[0] if len(qv) > 0 else None       # 최신 분기
            rec["op_q_prev"] = qv[4] if len(qv) > 4 else None      # 1년 전 동일 분기
            rec["ok"] = True
            return rec
        except Exception as e:  # noqa: BLE001
            if _is_rate_limited(e) and attempt < retries:
                time.sleep(base_delay * (2 ** attempt) + random.uniform(0, 1.0))
                continue
            rec["ok"] = False
            rec["error"] = str(e)[:120]
            return rec
    return rec


# ── 메트릭 계산 (한국 metrics.compute 와 동일 기준) ───────────────────────
def _safe_roe(ni, eq):
    if ni is None or eq is None or eq <= 0:
        return None
    return ni / eq * 100.0


def to_frame(records: list, universe: pd.DataFrame, fx: dict) -> pd.DataFrame:
    """fetch_one 결과 리스트 + 유니버스 + 환율 → 파생 메트릭 DataFrame."""
    rec = pd.DataFrame(records)
    df = universe.merge(rec, on="ticker", how="inner")

    # 섹터: 유니버스(S&P500) 우선, 없으면 yfinance
    df["sector"] = df["sector"].where(df["sector"].astype(bool), df.get("yf_sector", ""))

    # 일본 종목명: 한자/가나 → 영어(yfinance longName)로 표기 (가독성)
    if "yf_name" in df.columns:
        en = df["yf_name"].fillna("")
        jp_en = (df["market"] == "JP") & (en.str.len() > 0)
        df.loc[jp_en, "name"] = en[jp_en]

    # 시총 → 원화(억)
    rate = df["market"].map({"US": fx.get("US"), "JP": fx.get("JP")})
    df["marcap"] = pd.to_numeric(df["marcap_native"], errors="coerce") * rate / 1e8

    # ROE 3개년 (순이익/자본)
    for i in (0, 1, 2):
        df[f"roe_y{i}"] = [
            _safe_roe(n, e)
            for n, e in zip(df.get(f"ni_y{i}"), df.get(f"eq_y{i}"))
        ]
    df["roe_avg"] = df[["roe_y0", "roe_y1", "roe_y2"]].mean(axis=1)
    df["roe_min"] = df[["roe_y0", "roe_y1", "roe_y2"]].min(axis=1)
    df["roe_n"] = df[["roe_y0", "roe_y1", "roe_y2"]].notna().sum(axis=1)

    o0 = pd.to_numeric(df.get("op_y0"), errors="coerce")
    o1 = pd.to_numeric(df.get("op_y1"), errors="coerce")
    o2 = pd.to_numeric(df.get("op_y2"), errors="coerce")
    df["op_yoy_1"] = (o1 / o0.abs() - 1) * 100
    df["op_yoy_2"] = (o2 / o1.abs() - 1) * 100
    df["c1_uptrend"] = o0.notna() & o1.notna() & o2.notna() & (o0 < o1) & (o1 < o2)

    qc = pd.to_numeric(df.get("op_q_cur"), errors="coerce")
    qp = pd.to_numeric(df.get("op_q_prev"), errors="coerce")
    has_q = qc.notna() & qp.notna()
    # ② 최근 분기 영익 YoY 증가. 분기 데이터가 없으면(일본 다수) 최근 연간 영익 YoY로 대체
    ann_up = o1.notna() & o2.notna() & (o2 > o1)
    df["q_yoy"] = ((qc / qp.abs() - 1) * 100).where(has_q, df["op_yoy_2"])
    df["c2_qyoy"] = (has_q & (qc > qp)) | (~has_q & ann_up)

    marcap_n = pd.to_numeric(df["marcap_native"], errors="coerce")
    import numpy as np
    df["por_annual"] = np.where(o2 > 0, marcap_n / o2, np.nan)
    df["por_q1x4"] = np.where(qc > 0, marcap_n / (qc * 4), np.nan)
    df["por"] = df[["por_annual", "por_q1x4"]].min(axis=1)
    df["per"] = pd.to_numeric(df.get("per"), errors="coerce")
    df["pbr"] = pd.to_numeric(df.get("pbr"), errors="coerce")

    # 이익률 (최근 연도)
    rev = pd.to_numeric(df.get("rev_y2"), errors="coerce")
    gp = pd.to_numeric(df.get("gp_y2"), errors="coerce")
    ni2 = pd.to_numeric(df.get("ni_y2"), errors="coerce")
    df["gross_margin"] = np.where(rev > 0, gp / rev * 100, np.nan)
    df["op_margin"] = np.where(rev > 0, o2 / rev * 100, np.nan)
    df["net_margin"] = np.where(rev > 0, ni2 / rev * 100, np.nan)
    return df
