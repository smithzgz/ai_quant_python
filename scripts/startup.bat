@echo off
REM ============================================
REM 开机自启动脚本
REM 启动: Grafana + FastAPI + 调度器
REM ============================================

REM 等待网络就绪
timeout /t 10 /nobreak >nul

REM 启动 Grafana
echo Starting Grafana...
"C:\Program Files\GrafanaLabs\grafana\bin\grafana-server.exe" --config "C:\Program Files\GrafanaLabs\grafana\conf\grafana.ini" --homepath "C:\Program Files\GrafanaLabs\grafana" >nul 2>&1

REM 启动 FastAPI (包含调度器)
echo Starting FastAPI with Scheduler...
cd /d D:\code\Python\ai_quant_python
C:\veighna_studio\python.exe -m uvicorn web.app:app --host 0.0.0.0 --port 8088 >nul 2>&1
