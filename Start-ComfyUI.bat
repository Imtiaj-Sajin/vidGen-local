@echo off
title ComfyUI + LTX Lab
cd /d "%~dp0ComfyUI_windows_portable"
rem keep every cache on G: (C: is almost full)
set HF_HOME=%~dp0cache\hf
set TORCH_HOME=%~dp0cache\torch
set PIP_CACHE_DIR=%~dp0cache\pip
set TRITON_CACHE_DIR=%~dp0cache\triton
echo.
echo   ComfyUI : http://127.0.0.1:8188
echo   LTX Lab : http://127.0.0.1:8188/ltxlab
echo   Close this window to stop ComfyUI.
echo.
:loop
rem --vram-headroom 2: keep 2 GB VRAM free so Windows never spills GPU work into system RAM (10-50x slower)
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --vram-headroom 2 %*
if %errorlevel%==0 goto end
rem crashed (e.g. the occasional text-encoder access violation): restart; LTX Lab re-queues the interrupted run once
echo.
echo   ComfyUI stopped with code %errorlevel%. Restarting in 5 seconds...
echo.
ping -n 6 127.0.0.1 >nul
goto loop
:end
pause
