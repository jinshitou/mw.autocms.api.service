from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from api.routers import deploy, server, tdk, template, site, audit, landing
from core.database import engine, Base
import models.server
import models.asset
import models.site
import models.site_log
import models.audit_log

Base.metadata.create_all(bind=engine)


def ensure_schema_compatibility():
    inspector = inspect(engine)
    if inspector.has_table("servers"):
        server_cols = {c["name"] for c in inspector.get_columns("servers")}
        if "ssh_port" not in server_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE servers ADD COLUMN ssh_port INTEGER DEFAULT 22"))

    if inspector.has_table("sites"):
        site_cols = {c["name"] for c in inspector.get_columns("sites")}
        statements = []
        if "tdk_name" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN tdk_name VARCHAR")
        if "tdk_keywords" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN tdk_keywords VARCHAR")
        if "tdk_description" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN tdk_description TEXT")
        if "admin_username" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN admin_username VARCHAR")
        if "admin_password" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN admin_password VARCHAR")
        if "landing_page_id" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN landing_page_id INTEGER")
        if "landing_page_name" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN landing_page_name VARCHAR")
        if "https_enabled" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN https_enabled BOOLEAN DEFAULT FALSE")
        if "https_auto_renew" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN https_auto_renew BOOLEAN DEFAULT TRUE")
        if "https_expire_at" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN https_expire_at TIMESTAMP WITH TIME ZONE")
        if "https_updated_at" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN https_updated_at TIMESTAMP WITH TIME ZONE")
        if statements:
            with engine.begin() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))


ensure_schema_compatibility()

app = FastAPI(title="批量易优核心接口", version="1.0", docs_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.openapi.docs import get_swagger_ui_html
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(openapi_url=app.openapi_url, title="API 文档")

app.include_router(deploy.router, prefix="/api/deploy", tags=["Deploy"])
app.include_router(server.router, prefix="/api/servers", tags=["Servers"])
app.include_router(tdk.router, prefix="/api/tdks", tags=["TDK Management"])
app.include_router(template.router, prefix="/api/templates", tags=["Template Management"])
app.include_router(landing.router, prefix="/api/landing-pages", tags=["Landing Page Management"])
app.include_router(site.router, prefix="/api/sites", tags=["Site Management"])
app.include_router(audit.router, prefix="/api/logs", tags=["Audit Logs"])
Path("/app/landing_pages").mkdir(parents=True, exist_ok=True)
app.mount("/landing_pages", StaticFiles(directory="/app/landing_pages"), name="landing_pages")

@app.get("/")
def frontend_or_health():
    # 兼容两种运行方式：
    # 1) Docker: 把 fronttype 挂载到 /fronttype
    # 2) 本机: 项目根目录的 fronttype/index.html
    candidate_paths = [
        Path("/fronttype/index.html"),
        Path(__file__).resolve().parents[1] / "fronttype" / "index.html"
    ]
    for path in candidate_paths:
        if path.exists():
            return FileResponse(path)

    return {"status": "success", "message": "fronttype/index.html 未找到"}

@app.get("/health")
def health_check():
    return {"status": "success"}
