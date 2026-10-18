@echo off
set ROOT=%~dp0..
if exist "%ROOT%\.venv\Scripts\python.exe" (
  set "PATH=%ROOT%\.venv\Scripts;%PATH%"
  echo [OK] 已优先使用项目 venv
) else (
  echo [WARN] venv 缺失
)
where python
python -c "import sys; print(sys.executable)"
