"""중앙 설정값.

오늘(2026년 중반) 기준으로 사용할 회계연도/분기를 정의한다.
연간 사업보고서(11011)는 3월경, 1분기보고서(11013)는 5월경 공시되므로
2026-06 시점에는 FY2025 연간과 2026 1분기가 모두 확보된다.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

# ── 대상 기간 (오늘 날짜 기준 자동 산출) ──────────────────────────────────
# 연간 사업보고서(Y년치)는 Y+1년 3월 말까지 공시 → 4월부터 확실히 확보.
# 1분기보고서(Y년 1Q)는 Y년 5월 중순까지 공시 → 6월부터 확실히 확보.
_TODAY = dt.date.today()
_Y = _TODAY.year

ANNUAL_YEAR = _Y - 1 if _TODAY.month >= 4 else _Y - 2     # 최근 완료 회계연도
QUARTER_YEAR = _Y if (_TODAY.month, _TODAY.day) >= (6, 1) else _Y - 1
QUARTER_REPRT = "11013"     # 1분기보고서 (요청 기준: '올해 1분기')
ANNUAL_REPRT = "11011"      # 사업보고서(연간)

# 연간 finstate(11011, ANNUAL_YEAR) 호출 한 번이 커버하는 3개년
YEARS = [ANNUAL_YEAR - 2, ANNUAL_YEAR - 1, ANNUAL_YEAR]   # [2023, 2024, 2025]

# ── 기본 필터 임계값(앱에서 슬라이더로 조정 가능) ──────────────────────────
DEFAULT_MIN_ROE = 10.0      # 최근 3년 ROE 하한(%)
DEFAULT_MAX_POR = 10.0      # 영업이익 기준 PER(POR) 상한
DEFAULT_MAX_PER = 15.0      # PER 상한
DEFAULT_MAX_PBR = 2.0       # PBR 상한

# ── 경로 ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FUNDAMENTALS_PARQUET = DATA_DIR / "fundamentals.parquet"
UNIVERSE_PARQUET = DATA_DIR / "universe.parquet"
GLOBAL_PARQUET = DATA_DIR / "global_fundamentals.parquet"   # 미국·일본

# ── DART 호출 ────────────────────────────────────────────────────────────
FETCH_WORKERS = 8           # 동시 호출 스레드 수
FETCH_RETRY = 2


def get_dart_key() -> str:
    """DART API 키를 환경변수 또는 Streamlit secrets에서 가져온다."""
    key = os.environ.get("DART_API_KEY", "").strip()
    if key:
        return key
    try:
        import streamlit as st  # noqa: PLC0415
        return str(st.secrets["DART_API_KEY"]).strip()
    except Exception:
        pass
    # 마지막 폴백: secrets.toml 직접 파싱
    secrets = ROOT / ".streamlit" / "secrets.toml"
    if secrets.exists():
        for line in secrets.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DART_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(
        "DART API 키를 찾을 수 없습니다. 환경변수 DART_API_KEY 또는 "
        ".streamlit/secrets.toml 에 키를 설정하세요."
    )
