@echo off
taskkill /IM run.exe /F
cd /d %~dp0
start cmd /k run.exe