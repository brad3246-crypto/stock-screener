"""PC에서 갱신한 한국 데이터 캐시를 검증한 뒤 GitHub에 푸시.

refresh_data.bat 마지막 단계에서 실행된다:  python -m screener.publish
- fundamentals.parquet 숫자 채움률이 기준 미만이면 푸시하지 않는다
  (수집 실패로 빈 파일이 올라가는 사고 방지 — 2026-08-25 Actions 사례).
- 데이터 파일만 커밋한다. 작업 중인 코드 변경분은 건드리지 않는다.
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys

import pandas as pd

from screener import config

MIN_FILL = 0.90      # 재무 숫자 칸 채움률 하한
MIN_ROWS = 2000      # 종목 수 하한

DATA_FILES = [
    config.FUNDAMENTALS_PARQUET,
    config.UNIVERSE_PARQUET,
    config.SHORT_KR_PARQUET,
]


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=config.ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=check,
    )


def validate() -> tuple[bool, str]:
    df = pd.read_parquet(config.FUNDAMENTALS_PARQUET)
    num_cols = [c for c in df.columns
                if c.startswith(("revenue_", "op_profit_", "net_income_", "equity_"))]
    fill = float(df[num_cols].notna().mean().mean()) if num_cols else 0.0
    msg = f"종목 {len(df):,}개, 숫자 채움률 {fill:.1%}"
    return (len(df) >= MIN_ROWS and fill >= MIN_FILL), msg


def main() -> int:
    ok, msg = validate()
    if not ok:
        print(f"[publish] 검증 실패 — 푸시 안 함: {msg} (기준: {MIN_ROWS}개·{MIN_FILL:.0%} 이상)")
        return 1
    print(f"[publish] 검증 통과: {msg}")

    paths = [str(p.relative_to(config.ROOT)).replace("\\", "/")
             for p in DATA_FILES if p.exists()]
    _git("add", "--", *paths)
    if _git("diff", "--cached", "--quiet", "--", *paths, check=False).returncode == 0:
        print("[publish] 데이터 변경 없음 — 푸시 생략")
        return 0

    today = dt.date.today().isoformat()
    _git("commit", "-m", f"data: 한국 재무·유니버스·공매도 캐시 자동 갱신 ({today}, {msg})",
         "--", *paths)

    push = _git("push", "origin", "HEAD:main", check=False)
    if push.returncode != 0:
        # GitHub 쪽이 앞서 있으면(Actions의 미국·일본 갱신 등) 받아서 다시 시도
        pull = _git("pull", "--rebase", "--autostash", "origin", "main", check=False)
        if pull.returncode != 0:
            _git("rebase", "--abort", check=False)
            print("[publish] GitHub와 병합 실패 — 로컬 커밋만 남김. 수동 확인 필요:\n" + pull.stderr)
            return 1
        push = _git("push", "origin", "HEAD:main", check=False)
    if push.returncode != 0:
        print("[publish] 푸시 실패:\n" + push.stderr)
        return 1
    print("[publish] GitHub 푸시 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
