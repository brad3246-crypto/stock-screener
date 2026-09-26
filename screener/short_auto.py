"""KRX 공매도 잔고/거래를 '진짜 크롬 브라우저'로 자동 수집 → data/short_kr.parquet.

KRX(data.krx.co.kr)는 Akamai 봇차단이 있어 requests/pykrx로는 데이터가 안 나온다
('LOGOUT' 또는 서비스 이용 불가). 반면 진짜 브라우저로 페이지를 열면 데이터가 열리므로,
undetected-chromedriver(없으면 일반 Selenium)로 KRX 공매도 페이지를 연 뒤, 그 페이지
내부에서 getJsonData를 호출(fetch)해 JSON을 받아 저장한다.

실행:
    python -m screener.short_auto            # 최근 영업일 자동, 브라우저 잠깐 뜸
    python -m screener.short_auto --hidden   # 창 숨김 시도(차단될 확률 ↑)
    python -m screener.short_auto --date 20260710

주의: KRX가 차단을 강화하면 실패할 수 있다. 그때는 CSV 수동 업로드(앱)로 쓰면 된다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time

import pandas as pd

from . import config

KRX_PAGE = ("https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd"
            "?menuId=MDC02030301")
BAL_BLD = "dbms/MDC/STAT/srt/MDCSTAT30501"   # 개별종목 공매도 잔고(전종목)
VOL_BLD = "dbms/MDC/STAT/srt/MDCSTAT30101"   # 개별종목 공매도 거래(전종목)
SECU_STOCK = "STMFRTSCIFDRFS"                # 증권구분: 주식

# 페이지 내부에서 getJsonData를 POST하고 응답 text를 콜백으로 돌려주는 JS
_FETCH_JS = """
const done = arguments[arguments.length - 1];
fetch('/comm/bldAttendant/getJsonData.cmd', {
  method: 'POST',
  headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest'},
  body: arguments[0]
}).then(r => r.text()).then(t => done(t)).catch(e => done('ERR:' + e));
"""


# ── 드라이버 생성 (uc 우선, 실패 시 일반 Selenium) ────────────────────────
def _make_driver(show: bool = True):
    os.environ.setdefault("SETUPTOOLS_USE_DISTUTILS", "local")
    try:
        import setuptools  # noqa: F401  (distutils shim 활성화)
        import undetected_chromedriver as uc
        opts = uc.ChromeOptions()
        opts.add_argument("--window-size=1200,900")
        if not show:
            opts.add_argument("--headless=new")
        drv = uc.Chrome(options=opts)
        print("  [driver] undetected-chromedriver 사용")
        return drv
    except Exception as e:  # noqa: BLE001
        print(f"  [driver] uc 실패 → 일반 Selenium 폴백: {str(e)[:90]}")
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    o = Options()
    o.add_argument("--window-size=1200,900")
    o.add_argument("--disable-blink-features=AutomationControlled")
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    if not show:
        o.add_argument("--headless=new")
    drv = webdriver.Chrome(options=o)
    try:
        drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
                            {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"})
    except Exception:
        pass
    return drv


def _rows(text: str):
    """getJsonData 응답 text → 행 리스트. 데이터 없으면 []."""
    try:
        j = json.loads(text)
    except Exception:
        return []
    for k in ("OutBlock_1", "output", "block1"):
        if isinstance(j.get(k), list):
            return j[k]
    # 알 수 없는 키: 첫 리스트 값
    for v in j.values():
        if isinstance(v, list):
            return v
    return []


def _pick(row: dict, *cands):
    for c in cands:
        if c in row:
            return row[c]
    return None


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _code6(v):
    s = "".join(ch for ch in str(v) if ch.isdigit())
    return s.zfill(6)[-6:] if s else None


def fetch(date: str | None = None, show: bool = True) -> pd.DataFrame:
    drv = _make_driver(show=show)
    drv.set_script_timeout(60)
    try:
        drv.get(KRX_PAGE)
        time.sleep(6)  # Akamai 봇검증(sensor) 정착 대기

        def call(body):
            return drv.execute_async_script(_FETCH_JS, body)

        base = dt.datetime.strptime(date, "%Y%m%d").date() if date else dt.date.today()
        # 잔고가 있는 최근 영업일 탐색(최대 8일 뒤로)
        used = None
        for i in range(8):
            d = (base - dt.timedelta(days=i)).strftime("%Y%m%d")
            rows = _rows(call(f"bld={BAL_BLD}&trdDd={d}&mktTpCd=1"))
            if rows:
                used = d
                break
        if not used:
            raise RuntimeError("공매도 잔고 데이터가 있는 영업일을 못 찾음(차단 또는 휴장).")
        print(f"  기준일 {used}")

        bal, vol = [], []
        for mk in ("1", "2"):   # 1=KOSPI, 2=KOSDAQ
            bal += _rows(call(f"bld={BAL_BLD}&trdDd={used}&mktTpCd={mk}"))
        for mk in ("STK", "KSQ"):
            vol += _rows(call(f"bld={VOL_BLD}&trdDd={used}&mktId={mk}&inqCond={SECU_STOCK}"))
        print(f"  잔고 {len(bal)}행 · 거래 {len(vol)}행")
    finally:
        try:
            drv.quit()
        except Exception:
            pass

    if not bal:
        raise RuntimeError("잔고 응답이 비었습니다(차단 가능성).")

    brows = []
    for r in bal:
        code = _code6(_pick(r, "ISU_CD", "ISU_SRT_CD"))
        if not code:
            continue
        brows.append({"code": code,
                      "short_bal_qty": _num(_pick(r, "BAL_QTY")),
                      "short_bal_ratio": _num(_pick(r, "BAL_RTO", "SHORT_RTO")),
                      "list_shrs": _num(_pick(r, "LIST_SHRS"))})
    bdf = pd.DataFrame(brows).drop_duplicates("code")

    vrows = []
    for r in vol:
        code = _code6(_pick(r, "ISU_CD", "ISU_SRT_CD"))
        if not code:
            continue
        vrows.append({"code": code,
                      "short_vol_ratio": _num(_pick(r, "TRDVOL_WT")),
                      "acc_vol": _num(_pick(r, "ACC_TRDVOL"))})
    vdf = pd.DataFrame(vrows).drop_duplicates("code") if vrows else pd.DataFrame(columns=["code"])

    df = bdf.merge(vdf, on="code", how="left")
    qty = pd.to_numeric(df.get("short_bal_qty"), errors="coerce")
    acc = pd.to_numeric(df.get("acc_vol"), errors="coerce") if "acc_vol" in df else pd.Series(dtype=float)
    df["days_to_cover"] = qty / acc.where(acc > 0)
    df["loan_bal_qty"] = pd.NA
    df["loan_bal_ratio"] = pd.NA
    df["asof"] = used
    keep = ["code", "short_bal_ratio", "short_bal_qty", "short_vol_ratio",
            "days_to_cover", "loan_bal_qty", "loan_bal_ratio", "asof"]
    return df[[c for c in keep if c in df.columns]].dropna(subset=["code"]).reset_index(drop=True)


def run(date: str | None = None, show: bool = True) -> None:
    t0 = time.time()
    try:
        df = fetch(date=date, show=show)
    except Exception as e:  # noqa: BLE001
        print(f"[SHORT-AUTO] 실패: {str(e)[:120]}")
        print("  KRX 봇차단이거나 페이지 변경일 수 있습니다. 앱의 CSV 업로드로 대체하세요.")
        return
    if df.empty or df["short_bal_ratio"].notna().sum() == 0:
        print("[SHORT-AUTO] 데이터가 비었습니다(차단 추정). CSV 업로드로 대체하세요.")
        return
    config.DATA_DIR.mkdir(exist_ok=True)
    df.to_parquet(config.SHORT_KR_PARQUET, index=False)
    print(f"[SHORT-AUTO] 저장 {len(df)}종목 · 공매도잔고 "
          f"{int(df['short_bal_ratio'].notna().sum())} → {config.SHORT_KR_PARQUET.name} "
          f"/ {time.time()-t0:.0f}s")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="KRX 공매도 자동 수집(실브라우저)")
    p.add_argument("--date", default=None, help="기준일 YYYYMMDD(미지정=최근 영업일)")
    p.add_argument("--hidden", action="store_true", help="브라우저 창 숨김(차단 확률↑)")
    a = p.parse_args()
    run(date=a.date, show=not a.hidden)
