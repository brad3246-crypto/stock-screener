@echo off
chcp 65001>nul
title Stock Screener - Import short/lending CSV
cd /d "C:\Users\brad3\stock-screener"
set PYTHONUTF8=1
echo ============================================================
echo   data\krx_csv\ 의 KRX CSV(공매도잔고/거래/대차)를 가져옵니다.
echo   (KRX에서 브라우저로 직접 받은 CSV를 그 폴더에 먼저 넣으세요)
echo ============================================================
python -m screener.short --csv
echo.
pause
