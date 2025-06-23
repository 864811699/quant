@echo off
cd /d "%~dp0"
set app_name=清理数据库.exe
set conda_env=D:\app\anaconda3
set py_name=clear_db.py

REM 激活虚拟环境
call %conda_env%\Scripts\activate.bat mt5

REM 清理之前的打包文件
echo Cleaning previous builds...

rmdir /S /Q dist
rmdir /S /Q build


del run\%app_name%

REM 执行 PyInstaller 打包
echo Starting build...
pyinstaller --clean --onefile ^
  --paths=%conda_env%\envs\mt5\Lib\site-packages ^
  --exclude-module __pycache__ ^
  --exclude-module tkinter ^
  --exclude-module numpy ^
  --exclude-module pandas ^
  --exclude-module matplotlib ^
  --exclude-module PIL ^
  --exclude-module eventlet ^
  --hidden-import=toml ^
  --hidden-import=sqlalchemy ^
  --name %app_name% ^
  %py_name%

REM 检查打包结果
echo Build finished. Checking package content...
:: pyi-archive_viewer dist/run.exe

REM 打包完成
move /Y dist\%app_name% %app_name%

rmdir /S /Q dist
rmdir /S /Q build

echo "All done! Executable created in dist/%app_name%"
pause