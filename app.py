"""수급 소외 실적주 스크리너 — Streamlit 대시보드.

실행:  streamlit run app.py
사전:  python -m screener.fetch   (DART 재무 캐시 1회 생성)
"""
from __future__ import annotations

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor

import FinanceDataReader as fdr
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from screener import config, metrics
from screener.universe import load_universe

st.set_page_config(page_title="ADR 바닥 수급 소외 종목 필터", layout="wide")

MARKET_KR = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
GMARKET_KR = {"US": "미국", "JP": "일본"}
CHART_MAX_ROWS = 60   # 미니차트는 상위 N행만(주가 조회 부담 방지)


def _check_password() -> None:
    """공유 비밀번호 잠금. 시크릿 APP_PASSWORD 미설정 시 공개로 동작."""
    try:
        expected = str(st.secrets.get("APP_PASSWORD", "")).strip()
    except Exception:
        expected = ""
    if not expected or st.session_state.get("auth_ok"):
        return
    st.title("ADR 바닥 수급 소외 종목 필터")
    pw = st.text_input("🔒 비밀번호를 입력하세요", type="password")
    if pw == expected:
        st.session_state["auth_ok"] = True
        st.rerun()
    elif pw:
        st.error("비밀번호가 틀렸습니다.")
    st.stop()


_check_password()


@st.cache_data(ttl=86400, show_spinner=False)
def _price_history(code: str) -> list:
    """최근 1년 종가를 주 단위(약 50포인트)로. 실패 시 빈 리스트."""
    try:
        start = (dt.date.today() - dt.timedelta(days=365)).isoformat()
        h = fdr.DataReader(code, start)
        if h is None or h.empty or "Close" not in h.columns:
            return []
        s = h["Close"].dropna().iloc[::5]
        return [round(float(x), 1) for x in s.tolist()]
    except Exception:
        return []


def _histories(codes: list) -> list:
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_price_history, codes))


def _to_float(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def _naver_info(code: str) -> dict:
    """네이버 종목페이지에서 업종·공식PER·PBR 스크랩. 실패 시 빈 값."""
    try:
        raw = requests.get(
            f"https://finance.naver.com/item/main.naver?code={code}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        ).content
        html = raw.decode("utf-8", "replace")
        sec = re.search(r"sise_group_detail\.naver\?type=upjong[^>]*>([^<]+)</a>", html)
        per = re.search(r'id="_per"[^>]*>([\d,.\-]+)', html)
        pbr = re.search(r'id="_pbr"[^>]*>([\d,.\-]+)', html)
        return {
            "sector": sec.group(1).strip() if sec else "",
            "per": _to_float(per.group(1)) if per else None,
            "pbr": _to_float(pbr.group(1)) if pbr else None,
        }
    except Exception:
        return {"sector": "", "per": None, "pbr": None}


def _naver_infos(codes: list) -> list:
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_naver_info, codes))


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


# ── 미국·일본 (yfinance) ─────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def _load_global() -> pd.DataFrame:
    if not config.GLOBAL_PARQUET.exists():
        return pd.DataFrame()
    return pd.read_parquet(config.GLOBAL_PARQUET)


@st.cache_data(ttl=86400, show_spinner=False)
def _g_price(ticker: str) -> list:
    try:
        h = yf.Ticker(ticker).history(period="1y")
        if h is None or h.empty or "Close" not in h.columns:
            return []
        s = h["Close"].dropna().iloc[::5]
        return [round(float(x), 2) for x in s.tolist()]
    except Exception:
        return []


def _g_prices(tickers: list) -> list:
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_g_price, tickers))


def render_global() -> None:
    st.caption("미국 S&P500 · 일본 닛케이225. 회계연도 차이로 ②는 **최근 분기 YoY**로 적용. 시총은 환율로 원화(억) 환산.")
    gdf = _load_global()
    if gdf.empty:
        st.error("미국·일본 캐시가 없습니다. 터미널에서 실행하세요:\n\n```\npython -m screener.global_fetch\n```")
        return

    with st.sidebar:
        st.header("조절 필터")
        min_roe = st.slider("ROE 하한 (%)", 0.0, 30.0, config.DEFAULT_MIN_ROE, 0.5, key="g_roe")
        max_por = st.slider("POR 상한 (영업이익 기준)", 2.0, 30.0, config.DEFAULT_MAX_POR, 0.5, key="g_por")
        max_per = st.slider("PER 상한", 2.0, 50.0, config.DEFAULT_MAX_PER, 0.5, key="g_per")
        max_pbr = st.slider("PBR 상한", 0.2, 10.0, config.DEFAULT_MAX_PBR, 0.1, key="g_pbr")
        min_gm = st.slider("매출총이익률 하한 (%)", 0.0, 80.0, 0.0, 1.0, key="g_gm")
        min_om = st.slider("영업이익률 하한 (%)", 0.0, 50.0, 0.0, 1.0, key="g_om")
        min_nm = st.slider("순이익률 하한 (%)", 0.0, 50.0, 0.0, 1.0, key="g_nm")
        st.divider()
        st.subheader("적용할 기준")
        c1 = st.checkbox("① 최근 2년 영업이익 우상향", value=True, key="g_c1")
        c2 = st.checkbox("② 최근 분기 영업이익 YoY 증가", value=True, key="g_c2")
        c3 = st.checkbox("③ 최근 3년 ROE ≥ 하한", value=True, key="g_c3")
        c4 = st.checkbox("④ POR ≤ 상한", value=True, key="g_c4")
        c5 = st.checkbox("⑤ PER ≤ 상한", value=True, key="g_c5")
        c6 = st.checkbox("⑥ PBR ≤ 상한", value=True, key="g_c6")
        c7 = st.checkbox("⑦ 매출총이익률 ≥ 하한", value=False, key="g_c7")
        c8 = st.checkbox("⑧ 영업이익률 ≥ 하한", value=False, key="g_c8")
        c9 = st.checkbox("⑨ 순이익률 ≥ 하한", value=False, key="g_c9")
        st.divider()
        mkts = st.multiselect("시장", ["US", "JP"], default=["US", "JP"],
                              format_func=lambda m: GMARKET_KR[m], key="g_mkt")
        min_cap = st.number_input("최소 시총 (억원)", 0, 100_000_000, 0, step=1000, key="g_cap")

    df = gdf[gdf["market"].isin(mkts)].copy()
    for col in ("gross_margin", "op_margin", "net_margin"):   # 구버전 캐시 방어
        if col not in df.columns:
            df[col] = float("nan")
    if min_cap > 0:
        df = df[df["marcap"] >= min_cap]

    roe_cols = ["roe_y0", "roe_y1", "roe_y2"]
    df["c3_roe"] = (df["roe_n"] == 3) & (df[roe_cols] >= min_roe).all(axis=1)
    df["c4_por"] = df["por"].notna() & (df["por"] <= max_por)
    df["c5_per"] = df["per"].notna() & (df["per"] <= max_per)
    df["c6_pbr"] = df["pbr"].notna() & (df["pbr"] <= max_pbr)
    df["c7_gm"] = df["gross_margin"].notna() & (df["gross_margin"] >= min_gm)
    df["c8_om"] = df["op_margin"].notna() & (df["op_margin"] >= min_om)
    df["c9_nm"] = df["net_margin"].notna() & (df["net_margin"] >= min_nm)
    flags = {"c1_uptrend": c1, "c2_qyoy": c2, "c3_roe": c3, "c4_por": c4,
             "c5_per": c5, "c6_pbr": c6, "c7_gm": c7, "c8_om": c8, "c9_nm": c9}
    active = [k for k, v in flags.items() if v]
    mask = pd.Series(True, index=df.index)
    for k in active:
        mask &= df[k]
    view = df[mask].sort_values("por", ascending=True).reset_index(drop=True)

    m1, m2, m3 = st.columns(3)
    m1.metric("유니버스", f"{len(df):,}")
    m2.metric("현재 필터 통과", f"{len(view):,}")
    m3.metric("기준일", dt.date.today().isoformat())

    st.caption("표의 열 제목을 클릭하면 정렬됩니다 ↑↓")
    g1, g2, _ = st.columns([1.5, 1.5, 6], gap="small")
    show_roe = g1.checkbox("연도별 ROE 펼치기", value=False, key="g_roey")
    show_op = g2.checkbox("연간 영익 YoY 펼치기", value=False, key="g_opy")

    n_show = min(len(view), CHART_MAX_ROWS)
    with st.spinner("최근 1년 주가 불러오는 중..."):
        charts = _g_prices(view["ticker"].tolist()[:n_show])
    prices = charts + [[] for _ in range(len(view) - n_show)]
    POR_Q = "POR (1Q x 4)"

    data = {
        "시장": view["market"].map(GMARKET_KR),
        "종목코드": view["ticker"],
        "1년 주가": prices,
        "종목명": view["name"],
        "업종": view["sector"],
        "시총(억)": view["marcap"].round(0),
        "POR 연간": view["por_annual"],
        POR_Q: view["por_q1x4"],
        "3년 ROE 평균": view["roe_avg"],
    }
    if show_roe:
        data["ROE(-2)"] = view["roe_y0"]
        data["ROE(-1)"] = view["roe_y1"]
        data["ROE(최근)"] = view["roe_y2"]
    data["PER"] = view["per"]
    data["PBR"] = view["pbr"]
    data["GPM"] = view["gross_margin"]
    data["OPM"] = view["op_margin"]
    data["NPM"] = view["net_margin"]
    if show_op:
        data["영익YoY(-1)"] = view["op_yoy_1"]
        data["영익YoY(최근)"] = view["op_yoy_2"]
    data["분기영익YoY"] = view["q_yoy"]
    disp = pd.DataFrame(data)

    f1 = ["3년 ROE 평균", "POR 연간", POR_Q, "분기영익YoY",
          "GPM", "OPM", "NPM"]
    if show_roe:
        f1 += ["ROE(-2)", "ROE(-1)", "ROE(최근)"]
    if show_op:
        f1 += ["영익YoY(-1)", "영익YoY(최근)"]
    colcfg = {c: st.column_config.NumberColumn(format="%,.1f") for c in f1}
    colcfg["PER"] = st.column_config.NumberColumn(format="%,.2f")
    colcfg["PBR"] = st.column_config.NumberColumn(format="%,.2f")
    colcfg["시총(억)"] = st.column_config.NumberColumn(format="%,d")
    colcfg[POR_Q] = st.column_config.NumberColumn("POR\n(1Q x 4)", format="%,.1f")
    colcfg["1년 주가"] = st.column_config.LineChartColumn("1년 주가", width="small")
    st.dataframe(disp, column_config=colcfg, use_container_width=True, height=560, hide_index=True)

    st.download_button(
        "결과 CSV 다운로드",
        disp.drop(columns=["1년 주가"]).to_csv(index=False).encode("utf-8-sig"),
        file_name=f"screener_global_{dt.date.today():%Y%m%d}.csv",
        mime="text/csv",
    )


st.title("ADR 바닥 수급 소외 종목 필터")
_market_group = st.sidebar.radio(
    "🌐 시장 구분", ["🇰🇷 한국 (KOSPI·KOSDAQ)", "🇺🇸 미국 · 🇯🇵 일본"], index=0
)
st.sidebar.divider()
if _market_group.startswith("🇺🇸"):
    render_global()
    st.stop()

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

# ── 사이드바: 필터 (적용 버튼을 눌러야 반영) ─────────────────────────────
with st.sidebar:
    with st.form("kr_filter_form", border=False):
        st.header("조절 필터")
        st.caption("값을 조정한 뒤 맨 아래 **적용**을 눌러야 반영됩니다.")
        min_roe = st.slider("ROE 하한 (%)", 0.0, 30.0, 10.0, 0.5)
        max_por = st.slider("POR 상한 (영업이익 기준)", 2.0, 30.0, 10.0, 0.5)
        max_per = st.slider("PER 상한", 2.0, 50.0, 15.0, 0.5)
        max_pbr = st.slider("PBR 상한", 0.2, 10.0, 3.0, 0.1)
        min_gm = st.slider("매출총이익률(GPM) 하한 (%)", 0.0, 100.0, 25.0, 1.0)
        min_om = st.slider("영업이익률(OPM) 하한 (%)", 0.0, 50.0, 10.0, 1.0)
        min_nm = st.slider("순이익률(NPM) 하한 (%)", 0.0, 50.0, 5.0, 1.0)
        st.divider()
        st.subheader("적용할 기준")
        c1 = st.checkbox("① 최근 2년 영업이익 우상향", value=True)
        c2 = st.checkbox(f"② {config.QUARTER_YEAR} 1분기 영업이익 YoY 증가", value=True)
        c3 = st.checkbox("③ 최근 3년 ROE ≥ 하한", value=True)
        c4 = st.checkbox("④ POR ≤ 상한", value=True)
        c5 = st.checkbox("⑤ PER ≤ 상한", value=True)
        c6 = st.checkbox("⑥ PBR ≤ 상한", value=True)
        c7 = st.checkbox("⑦ 매출총이익률(GPM) ≥ 하한", value=False)
        c8 = st.checkbox("⑧ 영업이익률(OPM) ≥ 하한", value=False)
        c9 = st.checkbox("⑨ 순이익률(NPM) ≥ 하한", value=False)
        st.divider()
        markets = st.multiselect("시장", ["KOSPI", "KOSDAQ"], default=["KOSPI", "KOSDAQ"])
        min_cap = st.number_input("최소 시총 (억원)", 0, 1_000_000, 0, step=100)
        show_all = st.checkbox("기준 일부만 충족도 표시(통과 개수순)", value=False)
        st.form_submit_button("적용", type="primary", use_container_width=True, key="kr_apply")

df = metrics.compute(fund, universe, min_roe=min_roe, max_por=max_por,
                     max_per=max_per, max_pbr=max_pbr,
                     min_gm=min_gm, min_om=min_om, min_nm=min_nm)
df = df[df["market"].isin(markets)]
if min_cap > 0:
    df = df[df["marcap"] >= min_cap * 1e8]

# 선택된 기준만 AND 결합
flags = {"c1_uptrend": c1, "c2_q1_yoy": c2, "c3_roe": c3, "c4_por": c4,
         "c5_per": c5, "c6_pbr": c6, "c7_gm": c7, "c8_om": c8, "c9_nm": c9}
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
view = view.reset_index(drop=True)

st.caption("표의 열 제목을 클릭하면 그 값 기준으로 정렬됩니다 ↑↓")
t1, t2, _ = st.columns([1.5, 1.5, 6], gap="small")
show_roe_yearly = t1.checkbox("연도별 ROE 펼치기", value=False)
show_op_yearly = t2.checkbox("연간 영익 YoY 펼치기", value=False)

# 표시 상위 N행만 주가차트·업종 조회. PER/PBR은 필터와 일치하도록 계산값 사용.
n_show = min(len(view), CHART_MAX_ROWS)
codes_show = view["code"].tolist()[:n_show]
pad = len(view) - n_show
with st.spinner("최근 1년 주가·업종 불러오는 중..."):
    charts = _histories(codes_show)
    infos = _naver_infos(codes_show)
prices = charts + [[] for _ in range(pad)]
sector_col = [i["sector"] for i in infos] + ["" for _ in range(pad)]
per_col = view["per"]   # 시총÷순이익 (필터 기준과 동일)
pbr_col = view["pbr"]   # 시총÷자본총계

# 연도 라벨: '23, '24, '25 (config.YEARS 기준), 분기: '26
yy = [f"'{str(y)[2:]}" for y in config.YEARS]          # ["'23","'24","'25"]
qy = f"'{str(config.QUARTER_YEAR)[2:]}"                 # "'26"
roe_avg = view[["roe_2023", "roe_2024", "roe_2025"]].mean(axis=1)
POR_Q = "POR (1Q x 4)"

# 시장 → 종목코드 → 주가 → 종목명 → 업종 → 시총 → POR연간 → POR(1Qx4) → 3년ROE평균 →(연ROE)→ PER/PBR →(연영익)→ 1Q영익
data = {
    "시장": view["market"].map(MARKET_KR).fillna(view["market"]),
    "종목코드": view["code"],
    "1년 주가": prices,
    "종목명": view["name"],
    "업종": sector_col,
    "시총(억)": (view["marcap"] / 1e8).round(0),
    "POR 연간": view["por_annual"],
    POR_Q: view["por_q1x4"],
    "3년 ROE 평균": roe_avg,
}
if show_roe_yearly:
    data[f"{yy[0]} ROE"] = view["roe_2023"]
    data[f"{yy[1]} ROE"] = view["roe_2024"]
    data[f"{yy[2]} ROE"] = view["roe_2025"]
data["PER"] = per_col
data["PBR"] = pbr_col
data["GPM"] = view["gross_margin"]
data["OPM"] = view["op_margin"]
data["NPM"] = view["net_margin"]
if show_op_yearly:
    data[f"{yy[1]} YoY 영익"] = view["op_yoy_24"]
    data[f"{yy[2]} YoY 영익"] = view["op_yoy_25"]
data[f"{qy} 1Q YoY 영익"] = view["op_q1_yoy"]
disp = pd.DataFrame(data)

# 천단위 쉼표 포맷
f1 = ["3년 ROE 평균", "POR 연간", POR_Q, f"{qy} 1Q YoY 영익",
      "GPM", "OPM", "NPM"]
if show_roe_yearly:
    f1 += [f"{yy[0]} ROE", f"{yy[1]} ROE", f"{yy[2]} ROE"]
if show_op_yearly:
    f1 += [f"{yy[1]} YoY 영익", f"{yy[2]} YoY 영익"]
colcfg = {c: st.column_config.NumberColumn(format="%,.1f") for c in f1}
colcfg["PER"] = st.column_config.NumberColumn(format="%,.2f")
colcfg["PBR"] = st.column_config.NumberColumn(format="%,.2f")
colcfg["시총(억)"] = st.column_config.NumberColumn(format="%,d")
colcfg[POR_Q] = st.column_config.NumberColumn("POR\n(1Q x 4)", format="%,.1f")
# 가로 스크롤해도 보이도록 왼쪽 식별 열(시장·종목코드·1년 주가·종목명) 고정
colcfg["시장"] = st.column_config.TextColumn(pinned=True)
colcfg["종목코드"] = st.column_config.TextColumn(pinned=True)
colcfg["종목명"] = st.column_config.TextColumn(pinned=True)
colcfg["1년 주가"] = st.column_config.LineChartColumn("1년 주가", width="small", pinned=True)
st.dataframe(
    disp,
    column_config=colcfg,
    use_container_width=True,
    height=560,
    hide_index=True,
)

st.download_button(
    "결과 CSV 다운로드",
    disp.drop(columns=["1년 주가"]).to_csv(index=False).encode("utf-8-sig"),
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
