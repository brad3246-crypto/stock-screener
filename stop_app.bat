@echo off
chcp 65001>nul
title Stock Screener - Stop
echo 스톡 스크리너 서버를 종료합니다 (포트 8501)...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8501 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)
if "%FOUND%"=="1" (
    echo 종료 완료. 브라우저의 localhost:8501 은 이제 연결이 끊깁니다.
) else (
    echo 실행 중인 서버가 없습니다 (이미 꺼져 있음).
)
timeout /t 3 >nul
