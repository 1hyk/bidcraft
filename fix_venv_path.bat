@echo off
setlocal
chcp 65001 >nul
rem ============================================================
rem  换机后修复 .venv 的基础解释器路径
rem  用法：fix_venv_path.bat [Python3.12安装目录]
rem  例：  fix_venv_path.bat "C:\Users\me\AppData\Local\Programs\Python\Python312"
rem ============================================================
set "ROOT=%~dp0"
set "VENVCFG=%ROOT%.venv\pyvenv.cfg"

if not exist "%VENVCFG%" goto novencfg

set "BASE=%~1"
if not "%BASE%"=="" goto normalize
if exist "D:\python312\python.exe" set "BASE=D:\python312"
if not "%BASE%"=="" goto normalize

for /f "delims=" %%P in ('where python 2^>nul') do if not defined BASE set "BASE=%%~dpP"
if "%BASE%"=="" goto nopython

:normalize
set "BASE=%BASE:"=%"
:stripbs
if not "%BASE:~-1%"=="\" goto checkpy
set "BASE=%BASE:~0,-1%"
goto stripbs

:nopython
echo [错误] 未找到 Python。请安装 Python 3.12，然后把安装目录作为参数传进来：
echo        fix_venv_path.bat "C:\Path\To\Python312"
exit /b 1

:checkpy
if not exist "%BASE%\python.exe" echo [错误] %BASE%\python.exe 不存在。& exit /b 1
"%BASE%\python.exe" -c "import sys;sys.exit(0 if sys.version_info[:2]==(3,12) else 1)"
if errorlevel 1 echo [错误] %BASE% 不是 Python 3.12。本包 .venv 依赖 3.12。& exit /b 1

> "%VENVCFG%" echo home = %BASE%
>>"%VENVCFG%" echo include-system-site-packages = false
>>"%VENVCFG%" echo version = 3.12
>>"%VENVCFG%" echo executable = %BASE%\python.exe
>>"%VENVCFG%" echo command = %BASE%\python.exe -m venv "%ROOT%."

echo [完成] .venv 已指向 %BASE%
echo.
echo 验证：
"%ROOT%.venv\Scripts\python.exe" -c "import sys;print(sys.executable)"
if errorlevel 1 goto verifyfail
echo [成功] 解释器可用。
exit /b 0

:verifyfail
echo [警告] 解释器仍不可用。可尝试重建：
echo        "%BASE%\python.exe" -m venv "%ROOT%.venv"
echo        "%ROOT%.venv\Scripts\python.exe" -m pip install -r "%ROOT%backend\requirements.txt"
exit /b 1

:novencfg
echo [错误] 找不到 %VENVCFG%
echo        请确认已在项目根目录解压，且 .venv 已随包带上。
exit /b 1
