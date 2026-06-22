# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from web.api.sync_api import router as sync_router
from web.api.backtest_api import router as backtest_router
from web.api.quality_api import router as quality_router
from web.api.admin_api import router as admin_router
import os

app = FastAPI(title="AI Quant Python", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sync_router, prefix="/api/sync", tags=["数据同步"])
app.include_router(backtest_router, prefix="/api/backtest", tags=["回测管理"])
app.include_router(quality_router, prefix="/api/quality", tags=["数据质检"])
app.include_router(admin_router, prefix="/admin/api", tags=["管理后台"])

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def root():
    return {"name": "AI Quant Python", "version": "1.0.0", "status": "running"}


@app.get("/admin")
@app.get("/admin/")
def admin_page():
    return FileResponse(os.path.join(static_dir, "admin.html"))
