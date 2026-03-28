from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import deploy

app = FastAPI(title="批量易优核心接口", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(deploy.router, prefix="/api/deploy", tags=["Deploy"])

@app.get("/")
def health_check():
    return {"status": "success", "message": "批量易优核心引擎运行中！", "version": "1.0"}
