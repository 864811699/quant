@echo off

set app_name=core.exe
set conda_env=D:\app\anaconda3
set py_name=bin\runCore.py

REM �������⻷��
call %conda_env%\Scripts\activate.bat mt5

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
  --paths=%conda_env%\envs\mt5\Lib\site-packages ^
  --add-data "src/core;src/core" ^
  --add-data "templates;templates" ^
  --add-data "package;package" ^
  --exclude-module __pycache__ ^
  --exclude-module tkinter ^
  --exclude-module numpy ^
  --exclude-module pandas ^
  --exclude-module matplotlib ^
  --exclude-module PIL ^
  --hidden-import=logging ^
  --hidden-import=logging.handlers ^
  --hidden-import=toml ^
  --hidden-import=threading ^
  --hidden-import=ctypes ^
  --hidden-import=dataclasses ^
  --hidden-import=multiprocessing ^
  --hidden-import=copy ^
  --hidden-import=json ^
  --hidden-import=zmq ^
  --hidden-import=uuid ^
  --hidden-import=sqlalchemy ^
  --hidden-import=winsound ^
  --hidden-import=requests ^
  --hidden-import=flask_restful ^
  --hidden-import=flask ^
  --hidden-import=concurrent ^
  --name %app_name% ^
  %py_name%

REM ��������
echo Build finished. Checking package content...
:: pyi-archive_viewer dist/run.exe

REM ������
move /Y dist\%app_name% run\%app_name%

rmdir /S /Q dist
rmdir /S /Q build

echo "All done! Executable created in dist/%app_name%"
pause