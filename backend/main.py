from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import deploy, server
from core.database import engine, Base
import models.server

# 引擎启动时建表 (如果表不存在)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="批量易优核心接口", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(deploy.router, prefix="/api/deploy", tags=["Deploy"])
app.include_router(server.router, prefix="/api/servers", tags=["Servers"])

@app.get("/")
def health_check():
    return {"status": "success", "message": "批量易优核心引擎运行中！", "version": "1.0"}
