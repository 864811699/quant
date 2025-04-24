@echo off
REM �������⻷��
call D:\app\anaconda3\Scripts\activate.bat mt5

REM ����֮ǰ�Ĵ���ļ�
echo Cleaning previous builds...

rmdir /S /Q dist
rmdir /S /Q build
mkdir run
del run.spec
del run\*.con
del run\*.log
del run\run.exe

REM ִ�� PyInstaller ���
echo Starting build...
pyinstaller --clean --onefile ^
  --paths=D:\app\anaconda3/envs/mt5/Lib/site-packages ^
  --add-data "src;src" ^
  --add-data "templates;templates" ^
  --add-data "src/ctp/thostmduserapi_se.dll;." ^
  --add-data "src/ctp/thosttraderapi.py;." ^
  --add-data "src/ctp/thostmduserapi.py;." ^
  --add-data "src/ctp/thosttraderapi_se.dll;." ^
  --add-data "src/ctp/_thostmduserapi.pyd;." ^
  --add-data "src/ctp/_thosttraderapi.pyd;." ^
  --exclude-module __pycache__ ^
  --hidden-import=logging ^
  --hidden-import=logging.handlers ^
  --hidden-import=toml ^
  --hidden-import=thostmduserapi ^
  --hidden-import=thosttraderapi ^
  --hidden-import=MetaTrader5 ^
  --hidden-import=openctp_ctp ^
  --hidden-import=winsound ^
  --hidden-import=threading ^
  --hidden-import=ctypes ^
  --hidden-import=requests ^
  --hidden-import=dataclasses ^
  --hidden-import=multiprocessing ^
  --hidden-import=queue ^
  --hidden-import=copy ^
  --hidden-import=flask_restful ^
  --hidden-import=flask ^
  --hidden-import=json ^
  bin/run.py

REM ��������
echo Build finished. Checking package content...
:: pyi-archive_viewer dist/run.exe

REM ������
move /Y dist\run.exe run\run.exe
echo All done! Executable created in dist/run.exe
pause