@echo off
REM ============================================
REM AIQuant FastAPI + Scheduler (定时同步) launcher
REM 工作目录必须为本项目根目录
REM ============================================
cd /d D:\code\Python\ai_quant_python
"C:\veighna_studio\python.exe" -m uvicorn web.app:app --host 0.0.0.0 --port 8088
