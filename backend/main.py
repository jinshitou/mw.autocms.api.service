from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
# 引入我们刚才写的路由器
from api.routers import deploy

app = FastAPI(title="批量易优核心接口", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由器 (将 /api/deploy/batch 挂载上来)
app.include_router(deploy.router, prefix="/api/deploy", tags=["Deploy"])

@app.get("/")
def health_check():
    return {"status": "success", "message": "批量易优核心引擎已启动！", "version": "1.0"}