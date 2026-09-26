"""한국 공매도·대차 수급 → data/short_kr.parquet.

RS 모듈과 같은 패턴: 별도로 1회 수집해 parquet로 캐시하고, 앱은 그 캐시를
종목코드(code)로 머지해서 '수급 필터'에 쓴다.

산출 컬럼(code 기준):
  short_bal_ratio  공매도 잔고비중(%)   ← KRX 공매도 잔고 '비중'
  short_bal_qty    공매도 잔고수량
  short_vol_ratio  공매도 거래비중(%)   ← KRX 공매도 거래 '비중'
  avg_vol_Nd       최근 N거래일 평균 거래량
  days_to_cover    상환소요일수(일)     = 공매도잔고수량 / 평균거래량
  loan_bal_qty     대차잔고수량
  loan_bal_ratio   대차잔고비율(%)      = 대차잔고수량 / 상장주식수

데이터 소스:
  · 공매도(잔고/거래) + 거래량 → pykrx (정식 API)
  · 대차잔고 → KRX 정보데이터시스템 JSON (pykrx 미지원, 보조 스크래핑)
    실패하면 loan_* 컬럼만 NaN으로 비워두고 나머지는 정상 저장한다.

실행:  python -m screener.short                 # 최근 영업일 자동
       python -m screener.short --date 20260620 # 특정 일자
       python -m screener.short --no-loan       # 대차 수집 생략(빠름)
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from pathlib import Path

import pandas as pd
import requests
from pykrx import stock

from . import config

MARKETS = ("KOSPI", "KOSDAQ")
VOL_WINDOW = 20          # 상환소요일수에 쓰는 평균 거래량 기간(거래일)


# ── 공통 헬퍼 ────────────────────────────────────────────────────────────
def _ymd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def _pick(df: pd.DataFrame, *subs: str):
    """컬럼명에 subs 문자열을 모두 포함하는 첫 컬럼명. pykrx 버전차 방어."""
    for c in df.columns:
        if all(s in str(c) for s in subs):
            return c
    return None


def _codes6(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = df.index.astype(str).str.zfill(6)
    return df


def recent_bizday(base: dt.date | None = None, back: int = 10) -> str | None:
    """base(미지정 시 오늘)부터 거슬러 올라가며 공매도 잔고 데이터가 있는 첫 영업일."""
    base = base or dt.date.today()
    for i in range(back + 1):
        ds = _ymd(base - dt.timedelta(days=i))
        try:
            b = stock.get_shorting_balance_by_ticker(ds, market="KOSPI")
            if b is not None and not b.empty:
                return ds
        except Exception:
            continue
    return None


# ── 공매도 잔고 ──────────────────────────────────────────────────────────
def fetch_balance(date: str) -> pd.DataFrame:
    """전 종목 공매도 잔고비중·잔고수량 (code 기준)."""
    out = []
    for mk in MARKETS:
        try:
            b = stock.get_shorting_balance_by_ticker(date, market=mk)
        except Exception:
            b = None
        if b is None or b.empty:
            continue
        b = _codes6(b)
        qty = _pick(b, "공매도", "잔고") or _pick(b, "잔고", "수량")
        ratio = _pick(b, "비중")
        sub = pd.DataFrame({"code": b.index})
        sub["short_bal_qty"] = pd.to_numeric(b[qty].values, errors="coerce") if qty else pd.NA
        sub["short_bal_ratio"] = pd.to_numeric(b[ratio].values, errors="coerce") if ratio else pd.NA
        out.append(sub)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["code", "short_bal_qty", "short_bal_ratio"])


# ── 공매도 거래비중 ──────────────────────────────────────────────────────
def fetch_volume(date: str) -> pd.DataFrame:
    """전 종목 당일 공매도 거래비중(%) (code 기준)."""
    out = []
    for mk in MARKETS:
        try:
            v = stock.get_shorting_volume_by_ticker(date, market=mk)
        except Exception:
            v = None
        if v is None or v.empty:
            continue
        v = _codes6(v)
        ratio = _pick(v, "비중")
        sub = pd.DataFrame({"code": v.index})
        sub["short_vol_ratio"] = pd.to_numeric(v[ratio].values, errors="coerce") if ratio else pd.NA
        out.append(sub)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["code", "short_vol_ratio"])


# ── 평균 거래량(상환소요일수용) ──────────────────────────────────────────
def fetch_avg_volume(date: str, window: int = VOL_WINDOW) -> pd.DataFrame:
    """최근 window 거래일 평균 거래량 (code 기준)."""
    base = dt.datetime.strptime(date, "%Y%m%d").date()
    frames, days, attempts = [], 0, 0
    while days < window and attempts < window * 2 + 15:
        ds = _ymd(base - dt.timedelta(days=attempts))
        attempts += 1
        got = False
        for mk in MARKETS:
            try:
                o = stock.get_market_ohlcv_by_ticker(ds, market=mk)
            except Exception:
                o = None
            if o is None or o.empty:
                continue
            o = _codes6(o)
            vcol = _pick(o, "거래량")
            if not vcol:
                continue
            frames.append(pd.DataFrame({"code": o.index,
                                        "vol": pd.to_numeric(o[vcol].values, errors="coerce")}))
            got = True
        if got:
            days += 1
    if not frames:
        return pd.DataFrame(columns=["code", f"avg_vol_{window}d"])
    allv = pd.concat(frames, ignore_index=True)
    avg = allv.groupby("code", as_index=False)["vol"].mean()
    return avg.rename(columns={"vol": f"avg_vol_{window}d"})


# ── 대차잔고 (KRX 정보데이터시스템 — pykrx 미지원, 보조 스크래핑) ──────────
# 주의: KRX 페이지/식별자(bld)가 바뀌면 깨질 수 있다. 실패 시 graceful 하게
# 빈 DataFrame을 돌려주고 loan_* 는 NaN으로 남긴다.
KRX_JSON = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
# 대차거래 종목별 현황 화면의 bld. 동작하지 않으면 KRX 정보데이터시스템에서
# 해당 화면을 열고 개발자도구 Network 탭의 getJsonData 요청 'bld' 값으로 교체.
LOAN_BLD = "dbms/MDC/STAT/srt/MDCSTAT30901"
_KRX_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
    "X-Requested-With": "XMLHttpRequest",
}


def fetch_loan_balance(date: str) -> pd.DataFrame:
    """전 종목 대차잔고수량 (code 기준). 실패 시 빈 DataFrame."""
    empty = pd.DataFrame(columns=["code", "loan_bal_qty"])
    try:
        r = requests.post(
            KRX_JSON,
            data={"bld": LOAN_BLD, "trdDd": date, "mktId": "ALL",
                  "share": "1", "money": "1", "csvxls_isNo": "false"},
            headers=_KRX_HEADERS, timeout=20,
        )
        js = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"  [대차] 수집 실패(스킵): {str(e)[:80]}")
        return empty
    rows = js.get("output") or js.get("OutBlock_1") or js.get("block1") or []
    if not rows:
        print("  [대차] 응답에 행이 없음 — bld/파라미터 확인 필요. loan_* = NaN")
        return empty
    raw = pd.DataFrame(rows)
    code_c = next((c for c in raw.columns if c.upper() in ("ISU_SRT_CD", "ISU_CD")), None)
    # 대차잔고 수량 후보 컬럼(잔고 관련)
    qty_c = next((c for c in raw.columns
                  if "BAL" in c.upper() and ("QTY" in c.upper() or "_QTY" in c.upper())), None)
    qty_c = qty_c or next((c for c in raw.columns if "BAL_QTY" in c.upper()), None)
    if not code_c or not qty_c:
        print(f"  [대차] 컬럼 매칭 실패 {list(raw.columns)[:8]} — loan_* = NaN")
        return empty
    out = pd.DataFrame({
        "code": raw[code_c].astype(str).str.zfill(6),
        "loan_bal_qty": pd.to_numeric(
            raw[qty_c].astype(str).str.replace(",", "", regex=False), errors="coerce"),
    })
    return out


# ── 빌드 ─────────────────────────────────────────────────────────────────
def build(date: str | None = None, with_loan: bool = True) -> pd.DataFrame:
    date = date or recent_bizday()
    if not date:
        raise RuntimeError("최근 공매도 데이터가 있는 영업일을 찾지 못했습니다(네트워크/휴장 확인).")
    print(f"[SHORT] 기준일 {date}")

    bal = fetch_balance(date)
    print(f"  공매도 잔고  {len(bal)}종목")
    vol = fetch_volume(date)
    print(f"  공매도 거래  {len(vol)}종목")
    avg = fetch_avg_volume(date)
    vcol = f"avg_vol_{VOL_WINDOW}d"
    print(f"  평균거래량({VOL_WINDOW}d)  {len(avg)}종목")

    df = bal.merge(vol, on="code", how="outer").merge(avg, on="code", how="outer")

    # 상장주식수(대차잔고비율용) — 유니버스에서
    try:
        from .universe import load_universe
        uni = load_universe(refresh=False)[["code", "shares"]]
        df = df.merge(uni, on="code", how="left")
    except Exception:
        df["shares"] = pd.NA

    if with_loan:
        loan = fetch_loan_balance(date)
        print(f"  대차 잔고    {len(loan)}종목")
        df = df.merge(loan, on="code", how="left")
    else:
        df["loan_bal_qty"] = pd.NA

    # 파생: 상환소요일수, 대차잔고비율
    qty = pd.to_numeric(df.get("short_bal_qty"), errors="coerce")
    av = pd.to_numeric(df.get(vcol), errors="coerce")
    df["days_to_cover"] = qty / av.where(av > 0)
    loan_qty = pd.to_numeric(df.get("loan_bal_qty"), errors="coerce")
    shares = pd.to_numeric(df.get("shares"), errors="coerce")
    df["loan_bal_ratio"] = (loan_qty / shares.where(shares > 0) * 100)

    df["asof"] = date
    cols = ["code", "short_bal_ratio", "short_bal_qty", "short_vol_ratio",
            vcol, "days_to_cover", "loan_bal_qty", "loan_bal_ratio", "asof"]
    df = df[[c for c in cols if c in df.columns]]
    return df.dropna(subset=["code"]).reset_index(drop=True)


# ── 수동 CSV 가져오기 (KRX Akamai 봇차단 우회: 브라우저로 직접 받은 CSV 사용) ──
# KRX는 자동 스크래핑(pykrx/requests/headless)을 막으므로, 사람이 브라우저에서
# 직접 내려받은 CSV를 data/krx_csv/ 에 넣어 읽는다. 파일명에 다음 단어가 들어가면
# 자동 분류: '잔고'→공매도잔고, '거래'→공매도거래, '대차'→대차잔고.
CSV_DIR = config.DATA_DIR / "krx_csv"


def _read_csv_any(path) -> pd.DataFrame | None:
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str)
        except Exception:
            continue
    return None


def _col(df: pd.DataFrame, *subs: str, exclude: tuple = ()):
    """컬럼명에 subs를 모두 포함하고 exclude는 하나도 없는 첫 컬럼."""
    for c in df.columns:
        cs = str(c)
        if all(s in cs for s in subs) and not any(e in cs for e in exclude):
            return c
    return None


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False)
                         .str.replace("%", "", regex=False), errors="coerce")


def _code_series(df: pd.DataFrame) -> pd.Series | None:
    c = _col(df, "단축") or _col(df, "종목코드") or _col(df, "코드")
    if c is None:
        return None
    return df[c].astype(str).str.extract(r"(\d{6})", expand=False).str.zfill(6)


def _read_upload(file) -> pd.DataFrame | None:
    """스트림릿 업로드 파일(file-like) → DataFrame. 인코딩 자동."""
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            file.seek(0)
            df = pd.read_csv(file, encoding=enc, dtype=str)
            if df is not None and not df.empty:
                return df
        except Exception:
            continue
    return None


def _build_from_named(named: list) -> pd.DataFrame:
    """[(파일명, DataFrame), ...] → 통합 공매도/대차 DataFrame.

    파일명에 '잔고'/'거래'/'대차'가 있으면 그걸로 분류(없으면 컬럼으로 추정).
    """
    bal = vol = loan = None
    for name, df in named:
        if df is None or df.empty:
            print(f"  [CSV] 읽기 실패/빈 파일: {name}")
            continue
        kind = ("대차" if "대차" in name else "잔고" if "잔고" in name
                else "거래" if "거래" in name
                else ("대차" if _col(df, "대차") else "잔고" if _col(df, "잔고") else "거래"))
        code = _code_series(df)
        if code is None:
            print(f"  [CSV] 종목코드 컬럼 못찾음: {name} {list(df.columns)[:6]}")
            continue
        if kind == "잔고":
            qty = _col(df, "잔고", "수량") or _col(df, "잔고", exclude=("금액", "비중", "비율"))
            rto = _col(df, "비중") or _col(df, "비율")
            bal = pd.DataFrame({"code": code,
                                "short_bal_qty": _to_num(df[qty]) if qty else pd.NA,
                                "short_bal_ratio": _to_num(df[rto]) if rto else pd.NA})
            print(f"  [CSV] 공매도잔고 ← {name} (수량={qty}, 비중={rto}, {len(bal)}행)")
        elif kind == "거래":
            rto = _col(df, "공매도", "비중") or _col(df, "비중")
            tot = (_col(df, "총", "거래량") or _col(df, "전체", "거래량")
                   or _col(df, "거래량", exclude=("공매도",)))
            vol = pd.DataFrame({"code": code,
                                "short_vol_ratio": _to_num(df[rto]) if rto else pd.NA,
                                "acc_vol": _to_num(df[tot]) if tot else pd.NA})
            print(f"  [CSV] 공매도거래 ← {name} (비중={rto}, 총거래량={tot}, {len(vol)}행)")
        else:
            qty = _col(df, "대차", "잔고") or _col(df, "잔고", "수량") or _col(df, "잔고")
            loan = pd.DataFrame({"code": code,
                                 "loan_bal_qty": _to_num(df[qty]) if qty else pd.NA})
            print(f"  [CSV] 대차잔고 ← {name} (잔고={qty}, {len(loan)}행)")

    if bal is None:
        raise RuntimeError("공매도 '잔고' CSV를 찾지 못했습니다(파일명에 '잔고' 포함 권장).")

    df = bal
    if vol is not None:
        df = df.merge(vol, on="code", how="outer")
    if loan is not None:
        df = df.merge(loan, on="code", how="left")

    # 상장주식수(대차비율용) — 유니버스에서
    try:
        from .universe import load_universe
        df = df.merge(load_universe(refresh=False)[["code", "shares"]], on="code", how="left")
    except Exception:
        df["shares"] = pd.NA

    # 파생: 상환소요일수(당일 총거래량 기준), 대차잔고비율
    qty = pd.to_numeric(df.get("short_bal_qty"), errors="coerce")
    acc = pd.to_numeric(df.get("acc_vol"), errors="coerce") if "acc_vol" in df else pd.Series(index=df.index, dtype=float)
    df["days_to_cover"] = qty / acc.where(acc > 0)
    loan_qty = pd.to_numeric(df.get("loan_bal_qty"), errors="coerce")
    shares = pd.to_numeric(df.get("shares"), errors="coerce")
    df["loan_bal_ratio"] = loan_qty / shares.where(shares > 0) * 100
    df["asof"] = "CSV"
    keep = ["code", "short_bal_ratio", "short_bal_qty", "short_vol_ratio",
            "days_to_cover", "loan_bal_qty", "loan_bal_ratio", "asof"]
    return df[[c for c in keep if c in df.columns]].dropna(subset=["code"]).reset_index(drop=True)


def build_from_csv(folder=CSV_DIR) -> pd.DataFrame:
    """data/krx_csv/ 폴더의 KRX CSV들을 읽어 통합."""
    folder = Path(folder)
    files = sorted(folder.glob("*.csv")) if folder.exists() else []
    if not files:
        raise RuntimeError(f"CSV가 없습니다: {folder} 폴더에 KRX에서 받은 csv를 넣으세요.")
    return _build_from_named([(f.name, _read_csv_any(f)) for f in files])


def build_from_uploads(files) -> pd.DataFrame:
    """스트림릿 업로드 파일 리스트(각각 .name 보유)를 읽어 통합."""
    named = [(getattr(f, "name", "upload.csv"), _read_upload(f)) for f in files]
    if not any(df is not None and not df.empty for _, df in named):
        raise RuntimeError("업로드한 CSV를 읽지 못했습니다(형식/인코딩 확인).")
    return _build_from_named(named)


def run(date: str | None = None, with_loan: bool = True, from_csv: bool = False) -> None:
    t0 = time.time()
    if from_csv:
        try:
            df = build_from_csv()
        except RuntimeError as e:
            print(f"[SHORT] CSV 가져오기 실패: {e}")
            return
        config.DATA_DIR.mkdir(exist_ok=True)
        df.to_parquet(config.SHORT_KR_PARQUET, index=False)
        n_bal = int(df["short_bal_ratio"].notna().sum())
        n_loan = int(df["loan_bal_ratio"].notna().sum()) if "loan_bal_ratio" in df else 0
        print(f"[SHORT] (CSV) 저장 {len(df)}종목 · 공매도잔고 {n_bal} · 대차 {n_loan} "
              f"→ {config.SHORT_KR_PARQUET.name} / {time.time()-t0:.0f}s")
        return
    try:
        df = build(date=date, with_loan=with_loan)
    except RuntimeError as e:
        print(f"[SHORT] 건너뜀: {e}")
        print("[SHORT] KRX 데이터포털(data.krx.co.kr) 데이터 응답이 비어 있습니다('LOGOUT' 차단).")
        print("        · 보안프로그램(백신 웹가드)·공유기/회사망·VPN/보안DNS가 KRX 데이터 요청만")
        print("          가로채는 경우가 많습니다. 휴대폰 핫스팟 등 다른 네트워크에서 재시도해 보세요.")
        print("        · short_kr.parquet 미생성 → 앱의 공매도 컬럼만 빈칸. 다른 기능엔 영향 없음.")
        return
    config.DATA_DIR.mkdir(exist_ok=True)
    df.to_parquet(config.SHORT_KR_PARQUET, index=False)
    n_bal = int(df["short_bal_ratio"].notna().sum())
    n_loan = int(df["loan_bal_ratio"].notna().sum()) if "loan_bal_ratio" in df else 0
    print(f"[SHORT] 저장 {len(df)}종목 · 공매도잔고 {n_bal} · 대차 {n_loan} "
          f"→ {config.SHORT_KR_PARQUET.name} / {time.time()-t0:.0f}s")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="한국 공매도·대차 수급 수집")
    p.add_argument("--date", default=None, help="기준일 YYYYMMDD(미지정 시 최근 영업일)")
    p.add_argument("--no-loan", action="store_true", help="대차잔고 수집 생략")
    p.add_argument("--csv", action="store_true",
                   help="자동수집 대신 data/krx_csv/ 의 KRX CSV에서 가져오기(Akamai 차단 우회)")
    a = p.parse_args()
    run(date=a.date, with_loan=not a.no_loan, from_csv=a.csv)
