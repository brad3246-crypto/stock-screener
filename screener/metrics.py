"""캐시된 재무(fundamentals) + 실시간 시총으로 지표·필터 플래그 계산."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

Y0, Y1, Y2 = config.YEARS              # 2023, 2024, 2025
QY = config.QUARTER_YEAR               # 2026
PQY = config.QUARTER_YEAR - 1          # 2025


def _roe(ni, eq):
    if ni is None or eq is None or pd.isna(ni) or pd.isna(eq) or eq <= 0:
        return np.nan
    return ni / eq * 100.0


def _num(df, name):
    """컬럼이 없어도(구버전 캐시 등) 안전하게 숫자 Series 반환."""
    s = df.get(name)
    if s is None:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(s, errors="coerce")


def compute(
    fund: pd.DataFrame,
    universe: pd.DataFrame,
    min_roe: float = config.DEFAULT_MIN_ROE,
    max_por: float = config.DEFAULT_MAX_POR,
    max_per: float = 1e9,
    max_pbr: float = 1e9,
    max_gm: float = 1e9,
    min_om: float = -1e9,
    min_nm: float = -1e9,
) -> pd.DataFrame:
    """파생 지표 + 기준 플래그가 붙은 DataFrame 반환."""
    df = universe.merge(fund, on="code", how="inner")

    # ── ROE 3개년 (당기순이익 / 자본총계, 기말 기준) ──────────────────────
    for yr in config.YEARS:
        df[f"roe_{yr}"] = [
            _roe(ni, eq)
            for ni, eq in zip(_num(df, f"net_income_{yr}"), _num(df, f"equity_{yr}"))
        ]

    # ── 영업이익 기준 PER (POR) ──────────────────────────────────────────
    op_annual = _num(df, f"op_profit_{Y2}")
    op_q1 = _num(df, f"op_profit_q1_{QY}")
    op_q1_ann = op_q1 * 4
    marcap = pd.to_numeric(df["marcap"], errors="coerce")

    df["por_annual"] = np.where(op_annual > 0, marcap / op_annual, np.nan)
    df["por_q1x4"] = np.where(op_q1_ann > 0, marcap / op_q1_ann, np.nan)
    df["por"] = df[["por_annual", "por_q1x4"]].min(axis=1)   # 둘 중 낮은 값

    # ── PER(시총÷순이익) · PBR(시총÷자본총계), FY{Y2} 기준 ────────────────
    ni_y2 = _num(df, f"net_income_{Y2}")
    eq_y2 = _num(df, f"equity_{Y2}")
    df["per"] = np.where(ni_y2 > 0, marcap / ni_y2, np.nan)
    df["pbr"] = np.where(eq_y2 > 0, marcap / eq_y2, np.nan)

    # ── 기준 1: 최근 2년 영업이익 우상향 (전전기<전기<당기) ───────────────
    o0 = _num(df, f"op_profit_{Y0}")
    o1 = _num(df, f"op_profit_{Y1}")
    o2 = op_annual
    df["op_yoy_24"] = (o1 / o0.abs() - 1) * 100
    df["op_yoy_25"] = (o2 / o1.abs() - 1) * 100
    df["c1_uptrend"] = (o0.notna() & o1.notna() & o2.notna() & (o0 < o1) & (o1 < o2))

    # ── 기준 2: 올해 1분기 영업이익 YoY 증가 ─────────────────────────────
    q_cur = op_q1
    q_prev = _num(df, f"op_profit_q1_{PQY}")
    df["op_q1_yoy"] = (q_cur / q_prev.abs() - 1) * 100
    df["c2_q1_yoy"] = q_cur.notna() & q_prev.notna() & (q_cur > q_prev)

    # ── 기준 3: 최근 3년 ROE >= min_roe ─────────────────────────────────
    roe_cols = [f"roe_{yr}" for yr in config.YEARS]
    df["roe_min3y"] = df[roe_cols].min(axis=1)
    df["c3_roe"] = df[roe_cols].notna().all(axis=1) & (df[roe_cols] >= min_roe).all(axis=1)

    # ── 기준 4: POR <= max_por (연간 또는 1Qx4 중 하나라도) ───────────────
    df["c4_por"] = df["por"].notna() & (df["por"] <= max_por)

    # ── 기준 5·6: PER·PBR <= 상한 ────────────────────────────────────────
    df["c5_per"] = df["per"].notna() & (df["per"] <= max_per)
    df["c6_pbr"] = df["pbr"].notna() & (df["pbr"] <= max_pbr)

    # ── 이익률 (FY 최근) + 기준 7: GPM ≤ 상한 / 8·9: OPM·NPM ≥ 하한 ────────
    rev = _num(df, f"revenue_{Y2}")
    gp = _num(df, f"gross_profit_{Y2}")
    df["gross_margin"] = np.where(rev > 0, gp / rev * 100, np.nan)
    df["op_margin"] = np.where(rev > 0, op_annual / rev * 100, np.nan)
    df["net_margin"] = np.where(rev > 0, ni_y2 / rev * 100, np.nan)
    df["c7_gm"] = df["gross_margin"].notna() & (df["gross_margin"] <= max_gm)
    df["c8_om"] = df["op_margin"].notna() & (df["op_margin"] >= min_om)
    df["c9_nm"] = df["net_margin"].notna() & (df["net_margin"] >= min_nm)

    # pass_all/전 기준 통과 = 핵심 6기준(이익률은 앱 선택 필터로만)
    cflags = ["c1_uptrend", "c2_q1_yoy", "c3_roe", "c4_por", "c5_per", "c6_pbr"]
    df["pass_all"] = df[cflags].all(axis=1)
    df["pass_count"] = df[cflags].sum(axis=1)
    return df


# 보기 좋은 표시용 컬럼 선택
DISPLAY_COLS = [
    "code", "name", "market", "marcap",
    "roe_2023", "roe_2024", "roe_2025", "roe_min3y",
    "op_yoy_24", "op_yoy_25", "op_q1_yoy",
    "por_annual", "por_q1x4", "por",
    "c1_uptrend", "c2_q1_yoy", "c3_roe", "c4_por", "pass_count",
]
