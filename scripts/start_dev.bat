@echo off
REM ============================================================
REM  BidCraft 标书匠 - 一键启动开发环境（Windows）
REM
REM    后端: http://127.0.0.1:8000   (API 文档 http://127.0.0.1:8000/docs)
REM    前端: http://127.0.0.1:5180
REM
REM  首次使用请先执行：
REM    pip install -r backend/requirements.txt
REM    python scripts/check_env.py --init-db
REM ============================================================
setlocal

set ROOT=%~dp0..

REM 优先用项目自带 venv 的解释器：PATH 上的 python 可能是不带实体的 Microsoft
REM Store 占位符（执行报"系统找不到文件"），而 venv 的 base 未必在 PATH 中。
if exist "%ROOT%\.venv\Scripts\python.exe" (
  set "PATH=%ROOT%\.venv\Scripts;%PATH%"
) else (
  echo [WARN] 未找到 %ROOT%\.venv\Scripts\python.exe，将使用 PATH 上的 python。
  echo        若批量报"找不到文件"，请先建 venv：
  echo        python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r backend\requirements.txt
  echo.
)

echo [1/2] 启动后端 FastAPI (127.0.0.1:8000)...
start "bidcraft-backend" cmd /k "cd /d %ROOT%\backend && python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"

timeout /t 4 /nobreak >nul

echo [2/2] 启动前端 Vite (127.0.0.1:5180)...
start "bidcraft-frontend" cmd /k "cd /d %ROOT%\frontend && npm run dev"

timeout /t 6 /nobreak >nul

echo.
echo 本机访问:       http://127.0.0.1:5180
echo 后端 API 文档:  http://127.0.0.1:8000/docs
echo.
echo 如需局域网访问，把上面两条命令的 --host 改为 0.0.0.0 即可。
endlocal
