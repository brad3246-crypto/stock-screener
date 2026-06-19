@echo off
chcp 65001>nul
title Stock Screener
cd /d "C:\Users\brad3\stock-screener"
set PYTHONUTF8=1
echo ============================================================
echo   Stock Screener starting...
echo   Browser will open at http://localhost:8501
echo   (Close this window to stop the server)
echo ============================================================
python -m streamlit run app.py
pause
