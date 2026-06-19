"""수급 소외 실적주 스크리너 — Streamlit 대시보드.

실행:  streamlit run app.py
사전:  python -m screener.fetch   (DART 재무 캐시 1회 생성)
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from screener import config, metrics
from screener.universe import load_universe

st.set_page_config(page_title="수급 소외 실적주 스크리너", layout="wide")


@st.cache_data(ttl=900)
def _load_fundamentals() -> pd.DataFrame:
    if not config.FUNDAMENTALS_PARQUET.exists():
        return pd.DataFrame()
    return pd.read_parquet(config.FUNDAMENTALS_PARQUET)


@st.cache_data(ttl=900)
def _load_universe() -> pd.DataFrame:
    """실시간 시총(FDR) 우선. 클라우드에서 막히면 동봉된 캐시로 폴백."""
    try:
        live = load_universe(refresh=True)
        if live is not None and not live.empty:
            return live
    except Exception:
        pass
    if config.UNIVERSE_PARQUET.exists():
        return pd.read_parquet(config.UNIVERSE_PARQUET)
    raise RuntimeError("유니버스 로드 실패: FDR 호출 불가 + 캐시 없음")


st.title("📉 수급 소외 실적주 스크리너")
st.caption(
    "ADR 바닥 · 업종 쏠림 국면에서 실적은 우상향인데 수급에서 소외된 종목을 거른다. "
    f"기준연도 FY{config.ANNUAL_YEAR} 연간 · {config.QUARTER_YEAR} 1분기 (KOSPI/KOSDAQ)"
)

fund = _load_fundamentals()
if fund.empty:
    st.error(
        "재무 캐시가 없습니다. 터미널에서 먼저 실행하세요:\n\n"
        "```\npython -m screener.fetch\n```"
    )
    st.stop()

universe = _load_universe()

# ── 사이드바: 필터 ───────────────────────────────────────────────────────
with st.sidebar:
    st.header("필터")
    min_roe = st.slider("기준3 · 최근 3년 ROE 하한 (%)", 0.0, 30.0, config.DEFAULT_MIN_ROE, 0.5)
    max_por = st.slider("기준4 · 영업이익 PER(POR) 상한", 2.0, 30.0, config.DEFAULT_MAX_POR, 0.5)
    st.divider()
    st.subheader("적용할 기준")
    c1 = st.checkbox("① 최근 2년 영업이익 우상향", value=True)
    c2 = st.checkbox(f"② {config.QUARTER_YEAR} 1분기 영업이익 YoY 증가", value=True)
    c3 = st.checkbox("③ 최근 3년 ROE ≥ 하한", value=True)
    c4 = st.checkbox("④ POR ≤ 상한", value=True)
    st.divider()
    markets = st.multiselect("시장", ["KOSPI", "KOSDAQ"], default=["KOSPI", "KOSDAQ"])
    min_cap = st.number_input("최소 시총 (억원)", 0, 1_000_000, 0, step=100)
    show_all = st.checkbox("기준 일부만 충족도 표시(통과 개수순)", value=False)

df = metrics.compute(fund, universe, min_roe=min_roe, max_por=max_por)
df = df[df["market"].isin(markets)]
if min_cap > 0:
    df = df[df["marcap"] >= min_cap * 1e8]

# 선택된 기준만 AND 결합
flags = {"c1_uptrend": c1, "c2_q1_yoy": c2, "c3_roe": c3, "c4_por": c4}
active = [k for k, v in flags.items() if v]
if active:
    mask = pd.Series(True, index=df.index)
    for k in active:
        mask &= df[k]
    passed = df[mask]
else:
    passed = df

if show_all and active:
    df["sel_count"] = df[active].sum(axis=1)
    view = df.sort_values(["sel_count", "por"], ascending=[False, True])
else:
    view = passed.sort_values("por", ascending=True)

# ── 상단 메트릭 ──────────────────────────────────────────────────────────
m1, m2, m3, m4 = st.columns(4)
m1.metric("유니버스", f"{len(df):,}")
m2.metric("전 기준 통과", f"{int(df['pass_all'].sum()):,}")
m3.metric("현재 필터 통과", f"{len(passed):,}")
m4.metric("기준일", dt.date.today().isoformat())

# ── 결과 표 ─────────────────────────────────────────────────────────────
disp = view[metrics.DISPLAY_COLS].copy()
disp["marcap"] = (disp["marcap"] / 1e8).round(0)   # 억원
disp = disp.rename(columns={
    "code": "종목코드", "name": "종목명", "market": "시장", "marcap": "시총(억)",
    "roe_2023": "ROE23", "roe_2024": "ROE24", "roe_2025": "ROE25", "roe_min3y": "ROE최소",
    "op_yoy_24": "영익YoY24", "op_yoy_25": "영익YoY25", "op_q1_yoy": "1Q영익YoY",
    "por_annual": "POR연간", "por_q1x4": "POR(1Qx4)", "por": "POR",
    "c1_uptrend": "①", "c2_q1_yoy": "②", "c3_roe": "③", "c4_por": "④",
    "pass_count": "충족수",
})
num_cols = ["ROE23", "ROE24", "ROE25", "ROE최소", "영익YoY24", "영익YoY25",
            "1Q영익YoY", "POR연간", "POR(1Qx4)", "POR"]
st.dataframe(
    disp.style.format({c: "{:.1f}" for c in num_cols}, na_rep="-"),
    use_container_width=True,
    height=560,
)

st.download_button(
    "결과 CSV 다운로드",
    view[metrics.DISPLAY_COLS].to_csv(index=False).encode("utf-8-sig"),
    file_name=f"screener_{dt.date.today():%Y%m%d}.csv",
    mime="text/csv",
)

with st.expander("기준 정의 / 주의사항"):
    st.markdown(
        f"""
- **기준①** 영업이익 {config.YEARS[0]} < {config.YEARS[1]} < {config.YEARS[2]} (2년 연속 증가)
- **기준②** {config.QUARTER_YEAR} 1분기 영업이익 > {config.QUARTER_YEAR-1} 1분기 영업이익
- **기준③** {config.YEARS[0]}·{config.YEARS[1]}·{config.YEARS[2]} ROE 모두 ≥ 하한 (ROE = 당기순이익 ÷ 자본총계, 기말)
- **기준④** `시총 ÷ FY{config.ANNUAL_YEAR} 영업이익` **또는** `시총 ÷ (1분기 영업이익×4)` 중 하나라도 ≤ 상한
- POR은 일반 PER(순이익 기준)이 아니라 **영업이익 기준** 입니다(사용자 정의).
- 연결(CFS) 우선, 없으면 별도(OFS). 적자/결손 기업은 ROE·POR이 NaN 처리되어 자동 제외됩니다.
- 시총은 실시간(FinanceDataReader), 재무는 캐시. 재무 갱신은 `python -m screener.fetch`.
        """
    )
