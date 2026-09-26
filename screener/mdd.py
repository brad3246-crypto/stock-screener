"""지수·신용잔고 MDD(Running Maximum Drawdown) 데이터.

MDD 정의(화면의 엑셀 방식과 동일한 Running Drawdown):
    dd_t = (V_t − max(V_0..V_t)) / max(V_0..V_t)
그날까지의 사상 최고치 대비 낙폭(언더워터 커브). 항상 ≤ 0.

- 지수(코스피·코스닥): FinanceDataReader에서 조회 → 이 앱 환경에서 바로 됨.
- 신용융자잔고: KRX 데이터라 봇차단으로 자동조회 불가.
  공매도와 동일하게 사용자가 CSV를 올려 credit_kr.parquet 로 저장 → 있으면 표시.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from screener import config

INDEX_CODES = {"코스피": "KS11", "코스닥": "KQ11"}


def running_mdd(s: pd.Series) -> pd.Series:
    """누적 최고치 대비 낙폭(0 이하). 입력은 날짜 인덱스의 값 시리즈."""
    s = s.dropna().astype(float)
    peak = s.cummax()
    return (s - peak) / peak


def index_series(code: str, start: str) -> pd.DataFrame:
    """지수 종가 + running MDD. 실패 시 빈 DataFrame."""
    import FinanceDataReader as fdr

    try:
        h = fdr.DataReader(code, start)
    except Exception:
        return pd.DataFrame()
    if h is None or h.empty or "Close" not in h.columns:
        return pd.DataFrame()
    close = h["Close"].dropna().astype(float)
    out = pd.DataFrame({"value": close})
    out["mdd"] = running_mdd(close)
    out.index.name = "date"
    return out


def default_start(years: int = 1) -> str:
    return (dt.date.today() - dt.timedelta(days=365 * years)).isoformat()


def load_credit() -> pd.DataFrame:
    """저장된 신용융자잔고(코스피·코스닥) parquet 로드. 없으면 빈 DataFrame.

    기대 컬럼: date, kospi, kosdaq (억원 단위 등 무관, MDD는 비율이라 단위 불변)
    """
    p = config.CREDIT_KR_PARQUET
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
    return df


def build_credit_from_csv(files) -> pd.DataFrame:
    """KRX/금투협에서 받은 신용융자잔고 CSV(들)를 표준형으로.

    한글 컬럼 관용 매칭: 날짜/일자, 코스피/유가증권/거래소, 코스닥.
    여러 파일이면 날짜 기준 병합.
    """
    frames = []
    for f in files:
        raw = f.read() if hasattr(f, "read") else open(f, "rb").read()
        for enc in ("utf-8-sig", "cp949", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            continue
        import io

        df = pd.read_csv(io.StringIO(text))
        cols = {c: str(c).strip() for c in df.columns}
        df = df.rename(columns=cols)

        def pick(*keys):
            for c in df.columns:
                if any(k in str(c) for k in keys):
                    return c
            return None

        dcol = pick("날짜", "일자", "date", "DATE")
        kospi = pick("코스피", "유가증권", "거래소", "KOSPI")
        kosdaq = pick("코스닥", "KOSDAQ")
        if not dcol:
            continue
        keep = {"date": pd.to_datetime(df[dcol], errors="coerce")}
        if kospi:
            keep["kospi"] = pd.to_numeric(
                df[kospi].astype(str).str.replace(",", ""), errors="coerce")
        if kosdaq:
            keep["kosdaq"] = pd.to_numeric(
                df[kosdaq].astype(str).str.replace(",", ""), errors="coerce")
        frames.append(pd.DataFrame(keep).dropna(subset=["date"]))

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames).groupby("date").first().sort_index().reset_index()
    return out
