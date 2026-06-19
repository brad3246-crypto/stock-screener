"""DART 단일회사 주요계정(finstate) 래퍼.

핵심: finstate(11011, YEAR) 한 번이 당기/전기/전전기 3개년을 함께 준다.
연결(CFS) 우선, 없으면 별도(OFS) 사용.
"""
from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd
from opendartreader import OpenDartReader

from . import config

# 관심 계정 매칭 규칙. DART는 회사마다 계정명이 조금씩 다르다.
#   예) 당기순이익 → '당기순이익(손실)',  매출액 → '영업수익'(금융/지주)
# 후보 리스트를 순서대로 시도하고, exact → 부분포함 순으로 찾는다.
ACCOUNT_CANDIDATES = {
    "revenue": ["매출액", "수익(매출액)", "영업수익"],
    "op_profit": ["영업이익", "영업이익(손실)"],
    "net_income": ["당기순이익", "당기순이익(손실)"],
    "equity": ["자본총계"],
}

_dart: OpenDartReader | None = None


def get_dart() -> OpenDartReader:
    global _dart
    if _dart is None:
        _dart = OpenDartReader(config.get_dart_key())
    return _dart


def to_num(x) -> float | None:
    """DART 금액 문자열을 float로. 빈값/괄호음수 처리."""
    if x is None:
        return None
    s = str(x).strip()
    if s in ("", "-", "－"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[(),\s]", "", s).replace("－", "-")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _pick_rows(fs: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """연결(CFS) 우선, 없으면 별도(OFS) 행만 추린다."""
    if "fs_div" not in fs.columns:
        return fs, "?"
    for div in ("CFS", "OFS"):
        sub = fs[fs["fs_div"] == div]
        if not sub.empty:
            return sub, div
    return fs, "?"


def _amount(rows: pd.DataFrame, metric: str, col: str) -> float | None:
    """metric(예: 'net_income')의 후보 계정명으로 금액을 찾는다.

    같은 sj_div(BS/IS) 내에서 exact 매칭 우선, 없으면 부분포함(첫 행).
    '법인세차감전 순이익'이 '당기순이익' 부분매칭에 걸리지 않도록 후보를 좁게 둔다.
    """
    names = rows["account_nm"].astype(str).str.replace(r"\s+", "", regex=True)
    for cand in ACCOUNT_CANDIDATES[metric]:
        key = cand.replace(" ", "")
        hit = rows[names == key]
        if not hit.empty:
            return to_num(hit.iloc[0].get(col))
    for cand in ACCOUNT_CANDIDATES[metric]:
        key = cand.replace(" ", "")
        hit = rows[names.str.contains(re.escape(key), na=False)]
        if not hit.empty:
            return to_num(hit.iloc[0].get(col))
    return None


def fetch_annual(code: str) -> dict | None:
    """연간 사업보고서(11011, ANNUAL_YEAR). 3개년 매출/영업이익/순이익/자본 반환."""
    try:
        fs = get_dart().finstate(code, config.ANNUAL_YEAR, reprt_code=config.ANNUAL_REPRT)
    except Exception:
        return None
    if fs is None or len(fs) == 0:
        return None
    rows, fs_div = _pick_rows(fs)
    y0, y1, y2 = config.YEARS  # [전전기, 전기, 당기]
    out: dict = {"fs_div_annual": fs_div}
    cols = {
        y2: "thstrm_amount",
        y1: "frmtrm_amount",
        y0: "bfefrmtrm_amount",
    }
    for metric in ACCOUNT_CANDIDATES:
        for yr, col in cols.items():
            out[f"{metric}_{yr}"] = _amount(rows, metric, col)
    return out


def fetch_quarter(code: str) -> dict | None:
    """1분기보고서(11013, QUARTER_YEAR). 올해/작년 Q1 영업이익·순이익 반환."""
    try:
        fs = get_dart().finstate(code, config.QUARTER_YEAR, reprt_code=config.QUARTER_REPRT)
    except Exception:
        return None
    if fs is None or len(fs) == 0:
        return None
    rows, fs_div = _pick_rows(fs)
    out = {"fs_div_q": fs_div}
    qy = config.QUARTER_YEAR        # 2026
    pqy = config.QUARTER_YEAR - 1   # 2025
    for metric in ("op_profit", "net_income", "revenue"):
        out[f"{metric}_q1_{qy}"] = _amount(rows, metric, "thstrm_amount")
        out[f"{metric}_q1_{pqy}"] = _amount(rows, metric, "frmtrm_amount")
    return out


def fetch_company(code: str) -> dict:
    """한 종목의 연간+분기 재무를 합친 dict (실패해도 code는 항상 포함)."""
    rec: dict = {"code": code}
    ann = fetch_annual(code)
    if ann:
        rec.update(ann)
    q = fetch_quarter(code)
    if q:
        rec.update(q)
    rec["has_annual"] = ann is not None
    rec["has_quarter"] = q is not None
    return rec
