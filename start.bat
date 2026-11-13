@echo off
setlocal
cd /d "%~dp0"
uv run --locked python run.py %*
exit /b %errorlevel%
