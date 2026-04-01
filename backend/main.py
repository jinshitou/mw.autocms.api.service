from pathlib import Path
import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi import Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from api.routers import deploy, server, tdk, template, site, audit, landing, plugin
from core.database import engine, Base, SessionLocal
from core.runtime_paths import LANDING_PAGES_DIR
import models.server
import models.asset
import models.site
import models.site_log
import models.audit_log
from models.asset import PluginPackage, SitePluginDeployment, PluginVersion
from models.site import Site
from services.plugin_deploy_service import default_redirect_config

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
        if "redirect_enabled" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN redirect_enabled BOOLEAN DEFAULT FALSE")
        if "redirect_ip_whitelist" not in site_cols:
            statements.append("ALTER TABLE sites ADD COLUMN redirect_ip_whitelist TEXT")
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


def ensure_plugin_seed_and_backfill():
    db = SessionLocal()
    try:
        plugin_row = db.query(PluginPackage).filter(PluginPackage.plugin_type == "redirect").order_by(PluginPackage.id.asc()).first()
        if not plugin_row:
            cfg = default_redirect_config()
            plugin_row = PluginPackage(
                plugin_type="redirect",
                name="跳转守卫插件",
                owner_username="system",
                current_version="1.0.1",
                config_json=json.dumps(cfg, ensure_ascii=False),
            )
            db.add(plugin_row)
            db.commit()
            db.refresh(plugin_row)
            db.add(
                PluginVersion(
                    plugin_id=plugin_row.id,
                    version="1.0.1",
                    change_log="初始化默认跳转插件版本",
                    config_snapshot_json=json.dumps(cfg, ensure_ascii=False),
                    created_by="system",
                )
            )
            db.commit()
        existing_site_ids = {int(x[0]) for x in db.query(SitePluginDeployment.site_id).filter(SitePluginDeployment.plugin_id == plugin_row.id).all()}
        sites = db.query(Site).all()
        changed = False
        for s in sites:
            if int(s.id) in existing_site_ids:
                continue
            db.add(
                SitePluginDeployment(
                    site_id=int(s.id),
                    plugin_id=int(plugin_row.id),
                    version=str(plugin_row.current_version or "1.0.1"),
                    enabled=1 if bool(getattr(s, "redirect_enabled", False)) else 0,
                    status="success",
                    error_msg=None,
                )
            )
            changed = True
        if changed:
            db.commit()
    finally:
        db.close()


ensure_plugin_seed_and_backfill()

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
app.include_router(plugin.router, prefix="/api/plugins", tags=["Plugin Management"])
app.include_router(site.router, prefix="/api/sites", tags=["Site Management"])
app.include_router(audit.router, prefix="/api/logs", tags=["Audit Logs"])
LANDING_PAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/landing_pages", StaticFiles(directory=str(LANDING_PAGES_DIR)), name="landing_pages")

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


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
