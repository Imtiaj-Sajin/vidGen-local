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
echo.
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build %*
pause
