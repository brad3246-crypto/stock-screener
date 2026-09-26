@echo off
chcp 65001>nul
title Stock Screener - Refresh DART data
cd /d "C:\Users\brad3\stock-screener"
set PYTHONUTF8=1
echo [%date% %time%] Refreshing DART financial cache (force)...
python -m screener.fetch --force
echo [%date% %time%] Importing short-selling / lending CSV (if any in data\krx_csv)...
python -m screener.short --csv
echo [%date% %time%] Done. >> "data\refresh_history.log"
