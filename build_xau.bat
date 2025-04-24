@echo off

set app_name=xau.exe
set conda_env=D:\app\anaconda3
set py_name=bin\runMT5XAUUSD.py

REM 激活虚拟环境
call %conda_env%\Scripts\activate.bat mt5

REM 清理之前的打包文件
echo Cleaning previous builds...

rmdir /S /Q dist
rmdir /S /Q build
mkdir run
del run.spec
del run\*.con
del run\*.log
del run\%app_name%

REM 执行 PyInstaller 打包
echo Starting build...
pyinstaller --clean --onefile ^
  --paths=%conda_env%\envs\mt5\Lib\site-packages ^
  --add-data "src/mt5;src/mt5" ^
  --add-data "package;package" ^
  --exclude-module __pycache__ ^
  --hidden-import=logging ^
  --hidden-import=logging.handlers ^
  --hidden-import=toml ^
  --hidden-import=ctypes ^
  --hidden-import=dataclasses ^
  --hidden-import=multiprocessing ^
  --hidden-import=copy ^
  --hidden-import=json ^
  --hidden-import=zmq ^
  --hidden-import=uuid ^
  --hidden-import=sqlalchemy ^
  --hidden-import=MetaTrader5 ^
  --hidden-import=threading ^
  --name %app_name% ^
  %py_name%

REM 检查打包结果
echo Build finished. Checking package content...
:: pyi-archive_viewer dist/run.exe

REM 打包完成
move /Y dist\%app_name% run\%app_name%

rmdir /S /Q dist
rmdir /S /Q build

echo "All done! Executable created in dist/%app_name%"
pause