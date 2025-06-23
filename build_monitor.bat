@echo off
cd /d "%~dp0"
set app_name=monitor.exe
set conda_env=D:\app\anaconda3
set py_name=script\monitor.py

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
  --paths=%CONDA_PREFIX%\Lib\site-packages ^
  --add-data "package/db;package/db" ^
  --add-data "package/zmq;package/zmq" ^
  --exclude-module __pycache__ ^
  --hidden-import=dataclasses ^
  --hidden-import=json ^
  --hidden-import=sqlalchemy ^
  --hidden-import=pymysql ^
  --hidden-import=sqlalchemy.dialects.mysql.pymysql ^
  --hidden-import=threading ^
  --hidden-import=winsound ^
  --hidden-import=requests ^
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