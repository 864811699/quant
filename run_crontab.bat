@echo off
cd /d "%~dp0"
start cmd /k "ctp.exe"
timeout /t 10 /nobreak > nul

start cmd /k "usd.exe"
timeout /t 10 /nobreak > nul

start cmd /k "xau.exe"

timeout /t 60 /nobreak > nul

start cmd /k "core.exe"

