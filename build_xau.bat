@echo off
cd /d "%~dp0"
set app_name=xau.exe
set conda_env=D:\app\anaconda3
set py_name=bin\runMT5XAUUSD.py

set myenv=build_mt5

REM 激活虚拟环境
call %conda_env%\Scripts\activate.bat %myenv%

REM ����֮ǰ�Ĵ���ļ�
echo Cleaning previous builds...

rmdir /S /Q dist
rmdir /S /Q build
mkdir run
del run.spec
del run\*.con
del run\*.log
del run\%app_name%

REM ִ�� PyInstaller ���
echo Starting build...

pyinstaller --clean --onefile ^
  --paths=%CONDA_PREFIX%\Lib\site-packages ^
  --add-data "src/mt5;src/mt5" ^
  --add-data "package/config;package/config" ^
  --add-data "package/db;package/db" ^
  --add-data "package/logger;package/logger" ^
  --add-data "package/zmq;package/zmq" ^
  --exclude-module __pycache__ ^
  --hidden-import=logging ^
  --hidden-import=logging.handlers ^
  --hidden-import=toml ^
  --hidden-import=dataclasses ^
  --hidden-import=multiprocessing ^
  --hidden-import=json ^
  --hidden-import=zmq ^
  --hidden-import=uuid ^
  --hidden-import=sqlalchemy ^
  --hidden-import=pymysql ^
  --hidden-import=sqlalchemy.dialects.mysql.pymysql ^
  --hidden-import=MetaTrader5 ^
  --hidden-import=threading ^
  --name %app_name% ^
  %py_name%

REM ��������
echo Build finished. Checking package content...
:: pyi-archive_viewer dist/run.exe

REM ������
move /Y dist\%app_name% run\%app_name%

rmdir /S /Q dist
rmdir /S /Q build
del *.spec
echo "All done! Executable created in dist/%app_name%"
pause