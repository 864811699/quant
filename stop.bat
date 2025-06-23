@echo off
taskkill /F /FI "WINDOWTITLE eq ctp.exe" /IM cmd.exe >nul 2>&1
taskkill /F /IM ctp.exe >nul 2>&1
wmic process where "name='cmd.exe' and commandline like '%ctp.exe%'" delete >nul

taskkill /F /FI "WINDOWTITLE eq usd.exe" /IM cmd.exe >nul 2>&1
taskkill /F /IM usd.exe >nul 2>&1
wmic process where "name='cmd.exe' and commandline like '%usd.exe%'" delete >nul

taskkill /F /FI "WINDOWTITLE eq xau.exe" /IM cmd.exe >nul 2>&1
taskkill /F /IM xau.exe >nul 2>&1
wmic process where "name='cmd.exe' and commandline like '%xau.exe%'" delete >nul

taskkill /F /FI "WINDOWTITLE eq core.exe" /IM cmd.exe >nul 2>&1
taskkill /F /IM core.exe >nul 2>&1
wmic process where "name='cmd.exe' and commandline like '%core.exe%'" delete >nul


taskkill /F /IM "core.exe" /T >nul 2>&1
taskkill /F /IM "ctp.exe" /T >nul 2>&1
taskkill /F /IM "xau.exe" /T >nul 2>&1
taskkill /F /IM "usd.exe" /T >nul 2>&1

:: 获取当前 CMD 进程的 PID
for /f "tokens=2" %%a in ('wmic process where "name='cmd.exe'" get processid^, commandline /value ^| findstr /i "%~nx0%"') do set "CURRENT_PID=%%a"

:: 结束其他 CMD 进程
for /f "tokens=2" %%a in ('wmic process where "name='cmd.exe'" get processid ^| findstr /v "%CURRENT_PID%"') do (
    echo 终止进程 PID: %%a
    taskkill /F /PID %%a /T
)