@echo off
title LTX Lab remote access
rem Starts the password gate + a Cloudflare quick tunnel. Run Start-ComfyUI.bat first.
cd /d "%~dp0"
if not exist tools\cloudflared.exe (
  echo Downloading cloudflared...
  curl.exe -sL -o tools\cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
)
start "LTX password gate" /min ComfyUI_windows_portable\python_embeded\python.exe -s remote\proxy.py
timeout /t 3 >nul
echo.
echo   Password: & type remote\password.txt & echo.
echo   Your link appears below as https://....trycloudflare.com  (it changes every time)
echo.
tools\cloudflared.exe tunnel --no-autoupdate --url http://127.0.0.1:8190 2>&1 | findstr /i "trycloudflare.com"
