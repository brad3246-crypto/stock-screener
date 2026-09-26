' 스톡 스크리너를 "검은 창 없이" 백그라운드로 실행합니다.
' 더블클릭하면 창이 안 뜨고 서버만 조용히 켜집니다.
' 접속: 브라우저에서 http://localhost:8501
' 끄기: stop_app.bat 더블클릭
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\brad3\stock-screener"
' 0 = 창 숨김, False = 기다리지 않고 바로 반환
sh.Run "cmd /c set PYTHONUTF8=1 && python -m streamlit run app.py --server.headless true", 0, False
' 서버가 뜰 시간을 준 뒤 브라우저 자동 오픈
WScript.Sleep 6000
sh.Run "http://localhost:8501"
