"""종목 유니버스 + 시가총액 (FinanceDataReader, API 키 불필요)."""
from __future__ import annotations

import pandas as pd
import FinanceDataReader as fdr

from . import config


def _is_common_stock(code: str, name: str, market: str) -> bool:
    """ETF/스팩/우선주/리츠 등 영업이익 스크리닝에 부적합한 종목 제외."""
    if market not in ("KOSPI", "KOSDAQ"):
        return False
    if not code or not code.isdigit() or len(code) != 6:
        return False
    # 우선주: 보통주 코드는 끝자리 0. 우선주는 5/7 등으로 끝남.
    if not code.endswith("0"):
        return False
    bad = ("스팩", "ETN", "리츠")  # 리츠는 영업이익 구조가 달라 기본 제외
    return not any(b in name for b in bad)


def load_universe(refresh: bool = True) -> pd.DataFrame:
    """KOSPI/KOSDAQ 보통주의 코드·이름·시장·종가·시총·주식수.

    반환 컬럼: code, name, market, close, marcap, shares
    """
    if not refresh and config.UNIVERSE_PARQUET.exists():
        return pd.read_parquet(config.UNIVERSE_PARQUET)

    df = fdr.StockListing("KRX")
    df = df.rename(
        columns={
            "Code": "code",
            "Name": "name",
            "Market": "market",
            "Close": "close",
            "Marcap": "marcap",
            "Stocks": "shares",
        }
    )
    df = df[["code", "name", "market", "close", "marcap", "shares"]].copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    mask = [
        _is_common_stock(c, n, m)
        for c, n, m in zip(df["code"], df["name"], df["market"])
    ]
    df = df[mask].reset_index(drop=True)
    df["marcap"] = pd.to_numeric(df["marcap"], errors="coerce")
    df = df.dropna(subset=["marcap"])
    df = df[df["marcap"] > 0].reset_index(drop=True)

    config.DATA_DIR.mkdir(exist_ok=True)
    df.to_parquet(config.UNIVERSE_PARQUET, index=False)
    return df


if __name__ == "__main__":
    u = load_universe()
    print(f"유니버스 종목 수: {len(u)}")
    print(u.head(10).to_string())
