@echo off
chcp 65001>nul
title Stock Screener - KRX 공매도 자동 수집
cd /d "C:\Users\brad3\stock-screener"
set PYTHONUTF8=1
echo ============================================================
echo   KRX 공매도 자동 수집 (진짜 크롬 창이 잠깐 떴다 닫힙니다)
echo   성공하면 [SHORT-AUTO] 저장 NNNN종목 이 뜹니다.
echo   차단되면 실패 메시지가 뜨고, 그때는 앱의 CSV 업로드를 쓰세요.
echo ============================================================
python -m screener.short_auto
echo.
pause
