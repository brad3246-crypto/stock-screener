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


def compute(
    fund: pd.DataFrame,
    universe: pd.DataFrame,
    min_roe: float = config.DEFAULT_MIN_ROE,
    max_por: float = config.DEFAULT_MAX_POR,
) -> pd.DataFrame:
    """파생 지표 + 4개 기준 플래그가 붙은 DataFrame 반환."""
    df = universe.merge(fund, on="code", how="inner")

    # ── ROE 3개년 (당기순이익 / 자본총계, 기말 기준) ──────────────────────
    for yr in config.YEARS:
        df[f"roe_{yr}"] = [
            _roe(ni, eq)
            for ni, eq in zip(df.get(f"net_income_{yr}"), df.get(f"equity_{yr}"))
        ]

    # ── 영업이익 기준 PER (POR) ──────────────────────────────────────────
    op_annual = pd.to_numeric(df.get(f"op_profit_{Y2}"), errors="coerce")
    op_q1 = pd.to_numeric(df.get(f"op_profit_q1_{QY}"), errors="coerce")
    op_q1_ann = op_q1 * 4
    marcap = pd.to_numeric(df["marcap"], errors="coerce")

    df["por_annual"] = np.where(op_annual > 0, marcap / op_annual, np.nan)
    df["por_q1x4"] = np.where(op_q1_ann > 0, marcap / op_q1_ann, np.nan)
    df["por"] = df[["por_annual", "por_q1x4"]].min(axis=1)   # 둘 중 낮은 값

    # ── 기준 1: 최근 2년 영업이익 우상향 (전전기<전기<당기) ───────────────
    o0 = pd.to_numeric(df.get(f"op_profit_{Y0}"), errors="coerce")
    o1 = pd.to_numeric(df.get(f"op_profit_{Y1}"), errors="coerce")
    o2 = op_annual
    df["op_yoy_24"] = (o1 / o0.abs() - 1) * 100
    df["op_yoy_25"] = (o2 / o1.abs() - 1) * 100
    df["c1_uptrend"] = (o0.notna() & o1.notna() & o2.notna() & (o0 < o1) & (o1 < o2))

    # ── 기준 2: 올해 1분기 영업이익 YoY 증가 ─────────────────────────────
    q_cur = op_q1
    q_prev = pd.to_numeric(df.get(f"op_profit_q1_{PQY}"), errors="coerce")
    df["op_q1_yoy"] = (q_cur / q_prev.abs() - 1) * 100
    df["c2_q1_yoy"] = q_cur.notna() & q_prev.notna() & (q_cur > q_prev)

    # ── 기준 3: 최근 3년 ROE >= min_roe ─────────────────────────────────
    roe_cols = [f"roe_{yr}" for yr in config.YEARS]
    df["roe_min3y"] = df[roe_cols].min(axis=1)
    df["c3_roe"] = df[roe_cols].notna().all(axis=1) & (df[roe_cols] >= min_roe).all(axis=1)

    # ── 기준 4: POR <= max_por (연간 또는 1Qx4 중 하나라도) ───────────────
    df["c4_por"] = df["por"].notna() & (df["por"] <= max_por)

    df["pass_all"] = df["c1_uptrend"] & df["c2_q1_yoy"] & df["c3_roe"] & df["c4_por"]
    df["pass_count"] = (
        df[["c1_uptrend", "c2_q1_yoy", "c3_roe", "c4_por"]].sum(axis=1)
    )
    return df


# 보기 좋은 표시용 컬럼 선택
DISPLAY_COLS = [
    "code", "name", "market", "marcap",
    "roe_2023", "roe_2024", "roe_2025", "roe_min3y",
    "op_yoy_24", "op_yoy_25", "op_q1_yoy",
    "por_annual", "por_q1x4", "por",
    "c1_uptrend", "c2_q1_yoy", "c3_roe", "c4_por", "pass_count",
]
