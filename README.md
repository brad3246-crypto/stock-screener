# 수급 소외 실적주 스크리너

ADR이 역대 최저 수준으로 업종 쏠림이 심한 국면에서, **실적은 우상향인데 수급에서 소외되어 저평가된**
KOSPI/KOSDAQ 종목을 거르는 스크리너.

## 필터 기준
1. **최근 2년 영업이익 우상향** — FY2023 < FY2024 < FY2025
2. **올해 1분기 영업이익 YoY 증가** — 2026 1Q > 2025 1Q
3. **최근 3년 ROE ≥ 10%** — 2023·2024·2025 모두 (ROE = 당기순이익 ÷ 자본총계)
4. **영업이익 기준 PER(POR) ≤ 10** — `시총 ÷ FY2025 영업이익` 또는 `시총 ÷ (1Q영업이익×4)` 중 하나라도

> POR은 일반 PER(순이익 기준)이 아니라 **영업이익 기준** 지표입니다(요청 정의).

## 데이터 소스
- **종목·시총·주가**: FinanceDataReader (`StockListing('KRX')`) — API 키 불필요, 실시간
- **재무(영업이익·순이익·자본·매출, 3개년 + 1분기)**: DART Open API (`finstate` 단일회사 주요계정)
  - 회사당 2콜(연간 11011 + 1분기 11013)로 3개년이 한 번에 확보됨
  - 연결(CFS) 우선, 없으면 별도(OFS)

## 설치
```powershell
pip install -r requirements.txt
```

## DART 키 설정
`opendart.fss.or.kr` 에서 키 발급 후, 둘 중 하나:
- 환경변수: `$env:DART_API_KEY = "..."`
- 또는 `.streamlit/secrets.toml` 에 `DART_API_KEY = "..."`

## 사용
```powershell
# 1) 재무 캐시 생성 (최초 1회, ~수분). 중단해도 재실행 시 이어받음
python -m screener.fetch            # 전체
python -m screener.fetch --limit 50 # 테스트용 앞 50종목

# 2) 대시보드 실행
streamlit run app.py
```

대시보드에서 ROE 하한·POR 상한 슬라이더와 기준 on/off, 시장·시총 필터를 조정하고
결과를 CSV로 내려받을 수 있다. 시총은 실행 시 실시간 갱신, 재무는 캐시 사용.

## 재무 갱신
분기/연간 보고서 시즌이 지나면 캐시를 새로 받는다:
```powershell
python -m screener.fetch --force
```
대상 연도/분기는 `screener/config.py` 의 `ANNUAL_YEAR` / `QUARTER_YEAR` 에서 조정.

## 구조
```
app.py                  Streamlit 대시보드
screener/
  config.py             대상연도·임계값·경로·DART 키 로딩
  universe.py           FDR 종목/시총 유니버스
  dart_client.py        DART finstate 래퍼 (금액 파싱·CFS/OFS)
  metrics.py            ROE·POR·우상향·YoY·필터 플래그
  fetch.py              수집 파이프라인(병렬·부분저장)
data/                   캐시 parquet (gitignore)
```
